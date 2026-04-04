import json
import uuid
import boto3
from botocore.config import Config
from core.orchestrator.providers.base import LLMProvider


class BedrockProvider(LLMProvider):
    def __init__(self, model: str = "anthropic.claude-sonnet-4-6", region: str = None, bearer_token: str = None):
        import os
        self.model = model
        self.region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        self.bearer_token = bearer_token or os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
        self.client = None

        # Only create boto3 client if no bearer token (bearer uses httpx directly)
        if not self.bearer_token:
            session_kwargs = {"region_name": self.region}
            aws_key = os.environ.get("AWS_ACCESS_KEY_ID")
            aws_secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
            aws_token = os.environ.get("AWS_SESSION_TOKEN")
            if aws_key and aws_secret:
                session_kwargs["aws_access_key_id"] = aws_key
                session_kwargs["aws_secret_access_key"] = aws_secret
                if aws_token:
                    session_kwargs["aws_session_token"] = aws_token

            session = boto3.Session(**session_kwargs)
            self.client = session.client(
                "bedrock-runtime",
                config=Config(read_timeout=300, retries={"max_attempts": 3}),
            )

    async def chat(self, system_prompt, messages, tools, max_tokens=8192):
        bedrock_tools = [
            {
                "toolSpec": {
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": {"json": t["input_schema"]},
                }
            }
            for t in tools
        ]

        bedrock_messages = self._convert_messages(messages)
        effective_max = min(max_tokens, 32768)

        request_body = {
            "modelId": self.model,
            "system": [{"text": system_prompt}],
            "messages": bedrock_messages,
            "inferenceConfig": {"maxTokens": effective_max},
        }
        # Bedrock rejects empty toolConfig.tools — only include when tools exist
        if bedrock_tools:
            request_body["toolConfig"] = {"tools": bedrock_tools}

        if self.bearer_token:
            response = await self._call_with_bearer(request_body)
        else:
            import asyncio
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, self._call_with_boto3, request_body)

        # Parse response
        content_text = ""
        tool_calls = []
        for block in response["output"]["message"]["content"]:
            if "text" in block:
                content_text += block["text"]
            elif "toolUse" in block:
                tool_calls.append({
                    "id": block["toolUse"]["toolUseId"],
                    "name": block["toolUse"]["name"],
                    "arguments": block["toolUse"]["input"],
                })

        stop = response.get("stopReason", "end_turn")
        return {
            "content": content_text,
            "tool_calls": tool_calls,
            "stop_reason": "tool_use" if stop == "tool_use" else "end_turn",
        }

    def _call_with_boto3(self, request_body):
        """Standard boto3 call with IAM credentials (SigV4 signing)."""
        kwargs = {
            "modelId": request_body["modelId"],
            "system": request_body["system"],
            "messages": request_body["messages"],
            "inferenceConfig": request_body["inferenceConfig"],
        }
        if "toolConfig" in request_body:
            kwargs["toolConfig"] = request_body["toolConfig"]
        return self.client.converse(**kwargs)

    async def _call_with_bearer(self, request_body):
        """Direct HTTP call with Bedrock API Key (ABSK prefix).

        Uses httpx instead of boto3 — no SigV4 signing needed.
        """
        import httpx

        token = self.bearer_token
        endpoint = f"https://bedrock-runtime.{self.region}.amazonaws.com/model/{self.model}/converse"

        body = {
            "system": request_body["system"],
            "messages": request_body["messages"],
            "inferenceConfig": request_body["inferenceConfig"],
        }
        if "toolConfig" in request_body:
            body["toolConfig"] = request_body["toolConfig"]

        async with httpx.AsyncClient(timeout=300) as client:
            # Try Authorization: Bearer first (standard for ABSK keys)
            resp = await client.post(endpoint, headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }, json=body)

            # Fallback to X-API-Key header
            if resp.status_code in (401, 403):
                resp = await client.post(endpoint, headers={
                    "X-API-Key": token,
                    "Content-Type": "application/json",
                }, json=body)

            if resp.status_code >= 400:
                raise RuntimeError(f"Bedrock API key auth failed ({resp.status_code}): {resp.text[:500]}")
            return resp.json()

    def _convert_messages(self, messages):
        """Convert internal message format to Bedrock Converse format.

        Bedrock requires every tool_use block to have a matching tool_result
        in the immediately following user message.
        """
        bedrock_messages = []

        i = 0
        while i < len(messages):
            msg = messages[i]

            if msg["role"] == "user":
                bedrock_messages.append({
                    "role": "user",
                    "content": [{"text": msg["content"]}],
                })
                i += 1

            elif msg["role"] == "assistant":
                tool_calls = msg.get("tool_calls", [])

                has_tool_results = (
                    i + 1 < len(messages) and messages[i + 1]["role"] == "tool"
                )

                content = []
                if msg.get("content"):
                    content.append({"text": msg["content"]})

                tool_ids = []
                if tool_calls and has_tool_results:
                    for tc in tool_calls:
                        tool_id = tc.get("id") or f"tool_{uuid.uuid4().hex[:12]}"
                        tool_ids.append(tool_id)
                        content.append({
                            "toolUse": {
                                "toolUseId": tool_id,
                                "name": tc["name"],
                                "input": tc["arguments"],
                            }
                        })

                if not content:
                    content.append({"text": ""})

                bedrock_messages.append({"role": "assistant", "content": content})
                i += 1

                tool_results = []
                tid_idx = 0
                while i < len(messages) and messages[i]["role"] == "tool":
                    tool_use_id = tool_ids[tid_idx] if tid_idx < len(tool_ids) else f"tool_{uuid.uuid4().hex[:12]}"
                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "content": [{"text": messages[i]["content"]}],
                        }
                    })
                    tid_idx += 1
                    i += 1

                if tool_results:
                    bedrock_messages.append({
                        "role": "user",
                        "content": tool_results,
                    })

            else:
                i += 1

        return bedrock_messages
