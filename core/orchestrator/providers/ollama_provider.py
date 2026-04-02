import json
import re
import ollama
from orchestrator.providers.base import LLMProvider


class OllamaProvider(LLMProvider):
    def __init__(self, model: str = "qwen3:32b"):
        self.model = model

    async def chat(self, system_prompt, messages, tools, max_tokens=8192):
        # Prepend system prompt as first message
        full_messages = [{"role": "system", "content": system_prompt}]

        # Convert messages to Ollama-compatible format
        for msg in messages:
            if msg["role"] == "tool":
                # Ollama expects tool responses as role="tool"
                full_messages.append({
                    "role": "tool",
                    "content": msg["content"],
                })
            elif msg["role"] == "assistant":
                full_messages.append({
                    "role": "assistant",
                    "content": msg.get("content", ""),
                })
            else:
                full_messages.append({
                    "role": msg["role"],
                    "content": msg["content"],
                })

        response = ollama.chat(
            model=self.model,
            messages=full_messages,
            tools=self._convert_tools(tools),
        )

        content = response.message.content or ""
        tool_calls = [
            {
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            }
            for tc in (response.message.tool_calls or [])
        ]

        # Fallback: some models emit tool calls as JSON in the content text
        if not tool_calls and content:
            tool_calls = self._extract_tool_calls_from_text(content, tools)
            if tool_calls:
                # Strip the JSON from content since we parsed it as tool calls
                content = self._strip_tool_json(content)

        return {
            "content": content,
            "tool_calls": tool_calls,
            "stop_reason": "tool_use" if tool_calls else "end_turn",
        }

    def _convert_tools(self, tools):
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

    def _extract_tool_calls_from_text(self, content, tools):
        """Parse tool calls from content text when model doesn't use native tool calling."""
        tool_names = {t["name"] for t in tools}
        extracted = []

        # Match JSON objects that look like tool calls
        # Pattern: {"name": "tool_name", "arguments": {...}}
        json_pattern = re.findall(r'\{[^{}]*"name"\s*:\s*"[^"]+?"[^{}]*"arguments"\s*:\s*\{[^{}]*\}[^{}]*\}', content)
        for match in json_pattern:
            try:
                parsed = json.loads(match)
                if parsed.get("name") in tool_names and "arguments" in parsed:
                    extracted.append({
                        "name": parsed["name"],
                        "arguments": parsed["arguments"],
                    })
            except json.JSONDecodeError:
                continue

        if extracted:
            return extracted

        # Also try to find standalone JSON with a "name" field matching a tool
        for block in re.findall(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', content):
            try:
                parsed = json.loads(block)
                if parsed.get("name") in tool_names and "arguments" in parsed:
                    extracted.append({
                        "name": parsed["name"],
                        "arguments": parsed["arguments"],
                    })
            except json.JSONDecodeError:
                continue

        # Last resort: try parsing entire content as JSON
        if not extracted:
            try:
                parsed = json.loads(content.strip())
                name = parsed.get("name") or parsed.get("function")
                if name in tool_names and "arguments" in parsed:
                    extracted.append({
                        "name": name,
                        "arguments": parsed["arguments"],
                    })
            except (json.JSONDecodeError, AttributeError):
                pass

        # Also handle {"function": "tool_name", "arguments": {...}} variant
        if not extracted:
            func_pattern = re.findall(
                r'\{[^{}]*"function"\s*:\s*"([^"]+)"[^{}]*"arguments"\s*:\s*(\{[^{}]*\})[^{}]*\}',
                content,
            )
            for func_name, args_str in func_pattern:
                if func_name in tool_names:
                    try:
                        extracted.append({
                            "name": func_name,
                            "arguments": json.loads(args_str),
                        })
                    except json.JSONDecodeError:
                        continue

        return extracted

    def _strip_tool_json(self, content):
        """Remove parsed tool call JSON from the content text."""
        # Remove JSON blocks and code fences
        cleaned = re.sub(r'```(?:json)?\s*\{[\s\S]*?\}\s*```', '', content)
        cleaned = re.sub(r'\{[^{}]*"(?:name|function)"\s*:\s*"[^"]+?"[^{}]*"arguments"\s*:\s*\{[^{}]*\}[^{}]*\}', '', cleaned)
        cleaned = cleaned.strip()
        return cleaned
