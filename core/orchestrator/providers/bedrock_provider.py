import json
import uuid
import boto3
from botocore.config import Config
from orchestrator.providers.base import LLMProvider


class BedrockProvider(LLMProvider):
    def __init__(self, model: str = "us.anthropic.claude-sonnet-4-6"):
        self.model = model
        self.client = boto3.client(
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

        # Cap max_tokens to model limit (8192 for older Sonnet models)
        effective_max = min(max_tokens, 8192)

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
        """Convert internal message format to Bedrock Converse format."""
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
                content = []
                if msg.get("content"):
                    content.append({"text": msg["content"]})

                # Add toolUse blocks with generated IDs
                tool_ids = []
                for tc in msg.get("tool_calls", []):
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

        return bedrock_messages
