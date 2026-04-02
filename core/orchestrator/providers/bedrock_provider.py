import json
import uuid
import boto3
from botocore.config import Config
from core.orchestrator.providers.base import LLMProvider


class BedrockProvider(LLMProvider):
    def __init__(self, model: str = "anthropic.claude-sonnet-4-6", region: str = None):
        import os
        self.model = model
        region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

        # Build explicit credentials from env vars to avoid falling back to ~/.aws/credentials
        session_kwargs = {"region_name": region}
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
        # Convert tools to Bedrock Converse format
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

        # Convert messages
        bedrock_messages = self._convert_messages(messages)

        # Claude Sonnet 4.6 supports up to 64K output tokens
        effective_max = min(max_tokens, 32768)

        response = self.client.converse(
            modelId=self.model,
            system=[{"text": system_prompt}],
            messages=bedrock_messages,
            toolConfig={"tools": bedrock_tools},
            inferenceConfig={"maxTokens": effective_max},
        )

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

    def _convert_messages(self, messages):
        """Convert internal message format to Bedrock Converse format.

        Bedrock requires every tool_use block to have a matching tool_result
        in the immediately following user message. If an assistant message has
        tool_calls but no tool results follow (orphaned from a prior session),
        we strip the tool_calls to avoid a ValidationException.
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

                # Check if tool results actually follow this assistant message
                has_tool_results = (
                    i + 1 < len(messages) and messages[i + 1]["role"] == "tool"
                )

                content = []
                if msg.get("content"):
                    content.append({"text": msg["content"]})

                # Only include tool_use blocks if matching tool_results follow
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

                # Collect following tool results into a single user message
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

        # Ensure messages alternate user/assistant and don't end with assistant
        # (Bedrock requires the last message to be from user)
        return bedrock_messages
