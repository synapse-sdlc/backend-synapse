from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Abstract interface — swap Ollama for Bedrock with one config change."""

    @abstractmethod
    async def chat(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 8192,
    ) -> dict:
        """Returns: { 'content': str, 'tool_calls': list[dict], 'stop_reason': str }"""
        ...
