"""Unit tests for the OpenAI service wrapper without real network calls."""

import asyncio

import openai
import pytest
from pytest_mock import MockerFixture

from app.core.exceptions import OpenAIServiceError
from app.services import openai_service


def test_ask_chat_returns_first_reply_from_async_openai_client(mocker: MockerFixture) -> None:
    completion_response = mocker.Mock()
    completion_response.choices = [mocker.Mock(message=mocker.Mock(content="Hello back"))]

    create = mocker.AsyncMock(return_value=completion_response)
    client = mocker.Mock()
    client.chat.completions.create = create

    async_client = mocker.patch(
        "app.services.openai_service.openai.AsyncOpenAI",
        return_value=client,
    )
    mocker.patch.object(openai_service.settings, "OPENAI_API_KEY", "test-key")

    result = asyncio.run(openai_service.ask_chat("Hello"))

    async_client.assert_called_once_with(api_key="test-key", timeout=30)
    create.assert_awaited_once_with(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "Hello"}],
        temperature=0.2,
    )
    assert result == "Hello back"


def test_ask_chat_wraps_openai_errors(mocker: MockerFixture) -> None:
    create = mocker.AsyncMock(side_effect=openai.OpenAIError("boom"))
    client = mocker.Mock()
    client.chat.completions.create = create

    mocker.patch("app.services.openai_service.openai.AsyncOpenAI", return_value=client)
    mocker.patch.object(openai_service.settings, "OPENAI_API_KEY", "test-key")

    with pytest.raises(OpenAIServiceError):
        asyncio.run(openai_service.ask_chat("Hello"))
