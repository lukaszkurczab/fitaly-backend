from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.exceptions import OpenAIServiceError


@dataclass(frozen=True)
class GenerationUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class GenerationResult:
    text: str
    usage: GenerationUsage


class ChatGenerator:
    def __init__(
        self,
        openai_client: Any,
        *,
        model: str = "gpt-4o-mini",
        temperature: float = 0.2,
    ) -> None:
        self.openai_client = openai_client
        self.model = model
        self.temperature = temperature

    async def generate(self, *, messages: list[dict[str, str]]) -> GenerationResult:
        response = await self.openai_client.chat_completion(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
        )
        text = str(response.get("content") or "").strip()
        if not text:
            raise OpenAIServiceError("OpenAI returned an empty completion.")

        usage_raw = response.get("usage") if isinstance(response, dict) else None
        usage = GenerationUsage(
            prompt_tokens=self._to_int(usage_raw, "prompt_tokens"),
            completion_tokens=self._to_int(usage_raw, "completion_tokens"),
            total_tokens=self._to_int(usage_raw, "total_tokens"),
        )
        return GenerationResult(text=text, usage=usage)

    @staticmethod
    def _to_int(usage: Any, key: str) -> int:
        if isinstance(usage, dict):
            value = usage.get(key)
            if isinstance(value, bool):
                return 0
            if isinstance(value, (int, float)):
                return int(value)
        return 0
