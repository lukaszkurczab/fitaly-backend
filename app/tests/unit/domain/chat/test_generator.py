from __future__ import annotations

import pytest

from app.core.exceptions import OpenAIServiceError
from app.domain.chat.generator import ChatGenerator


class _FakeOpenAIClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    async def chat_completion(self, **kwargs: dict) -> dict:
        self.calls.append(kwargs)
        return self.payload


async def test_generator_maps_usage_from_openai_client_response() -> None:
    client = _FakeOpenAIClient(
        {
            "content": "To jest odpowiedz.",
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "total_tokens": 150,
            },
        }
    )
    generator = ChatGenerator(client, model="gpt-4o-mini", temperature=0.1)

    result = await generator.generate(
        messages=[
            {"role": "system", "content": "x"},
            {"role": "user", "content": "y"},
        ]
    )

    assert result.text == "To jest odpowiedz."
    assert result.usage.prompt_tokens == 120
    assert result.usage.completion_tokens == 30
    assert result.usage.total_tokens == 150
    assert client.calls[0]["model"] == "gpt-4o-mini"


async def test_generator_raises_on_empty_text() -> None:
    client = _FakeOpenAIClient({"content": "", "usage": {}})
    generator = ChatGenerator(client)
    with pytest.raises(OpenAIServiceError):
        await generator.generate(messages=[{"role": "user", "content": "hej"}])
