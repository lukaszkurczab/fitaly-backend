import asyncio
import json
from typing import Any, cast

import pytest
from pytest_mock import MockerFixture

from app.core.exceptions import OpenAIServiceError
from app.schemas.ai_text_meal import AiTextMealPayload
from app.services import text_meal_service

_ALLOWED_TEXT_MEAL_PROVIDER_FIELDS = {"name", "ingredients", "amount_g", "notes", "lang"}
_FORBIDDEN_PROVIDER_CONTEXT_FIELDS = (
    "profile",
    "history",
    "mealHistory",
    "chat",
    "logs",
    "userId",
    "threadId",
    "email",
)


def _payload() -> AiTextMealPayload:
    return AiTextMealPayload(
        name="kebab",
        ingredients="kebab",
        amount_g=350,
        notes=None,
    )


def _payload_with_forbidden_context() -> AiTextMealPayload:
    return AiTextMealPayload.model_validate(
        {
            "name": "kebab",
            "ingredients": "kebab, pita, sauce",
            "amount_g": 350,
            "notes": "extra spicy",
            "profile": {"goal": "secret-profile"},
            "history": ["secret-history"],
            "mealHistory": ["secret-meal-history"],
            "chat": [{"content": "secret-chat"}],
            "logs": ["secret-log"],
            "userId": "secret-user-id",
            "threadId": "secret-thread-id",
            "email": "secret@example.com",
        }
    )


def _provider_payload_from_prompt(prompt: str) -> dict[str, Any]:
    marker = "Payload: "
    payload_start = prompt.index(marker) + len(marker)
    payload, _ = json.JSONDecoder().raw_decode(prompt[payload_start:])
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def _assert_no_forbidden_context(prompt: str, payload: dict[str, Any]) -> None:
    assert set(payload) == _ALLOWED_TEXT_MEAL_PROVIDER_FIELDS
    for field in _FORBIDDEN_PROVIDER_CONTEXT_FIELDS:
        assert field not in payload
        assert field not in prompt
    for forbidden_value in (
        "secret-profile",
        "secret-history",
        "secret-meal-history",
        "secret-chat",
        "secret-log",
        "secret-user-id",
        "secret-thread-id",
        "secret@example.com",
    ):
        assert forbidden_value not in prompt


def test_analyze_text_meal_returns_first_valid_result(
    mocker: MockerFixture,
) -> None:
    ask_chat = mocker.patch(
        "app.services.text_meal_service.openai_service.ask_chat_completion",
        new=mocker.AsyncMock(
            return_value={
                "content": '[{"name":"Kebab"}]',
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            }
        ),
    )
    parse = mocker.patch(
        "app.services.text_meal_service.openai_service.parse_ingredients_reply",
        return_value=[
            {
                "name": "Kebab",
                "amount": 350.0,
                "protein": 18.0,
                "fat": 20.0,
                "carbs": 30.0,
                "kcal": 360.0,
            }
        ],
    )

    result = asyncio.run(text_meal_service.analyze_text_meal(_payload(), lang="pl"))

    assert len(result) == 1
    ask_chat.assert_awaited_once()
    parse.assert_called_once()


def test_analyze_text_meal_retries_when_first_reply_has_zero_nutrition(
    mocker: MockerFixture,
) -> None:
    ask_chat = mocker.patch(
        "app.services.text_meal_service.openai_service.ask_chat_completion",
        new=mocker.AsyncMock(
            side_effect=[
                {
                    "content": '[{"name":"Kebab"}]',
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                },
                {
                    "content": '[{"name":"Kebab corrected"}]',
                    "usage": {
                        "prompt_tokens": 12,
                        "completion_tokens": 7,
                        "total_tokens": 19,
                    },
                },
            ]
        ),
    )
    mocker.patch(
        "app.services.text_meal_service.openai_service.parse_ingredients_reply",
        side_effect=[
            [
                {
                    "name": "Kebab",
                    "amount": 350.0,
                    "protein": 0.0,
                    "fat": 0.0,
                    "carbs": 0.0,
                    "kcal": 0.0,
                }
            ],
            [
                {
                    "name": "Kebab",
                    "amount": 350.0,
                    "protein": 18.0,
                    "fat": 20.0,
                    "carbs": 30.0,
                    "kcal": 360.0,
                }
            ],
        ],
    )

    result = asyncio.run(text_meal_service.analyze_text_meal(_payload(), lang="pl"))

    assert len(result) == 1
    assert ask_chat.await_count == 2


def test_analyze_text_meal_provider_prompts_are_current_action_minimized(
    mocker: MockerFixture,
) -> None:
    ask_chat = mocker.patch(
        "app.services.text_meal_service.openai_service.ask_chat_completion",
        new=mocker.AsyncMock(
            side_effect=[
                {
                    "content": (
                        '[{"name":"Kebab","amount":350,"protein":0,"fat":0,"carbs":0,"kcal":0}]'
                    ),
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                },
                {
                    "content": (
                        '[{"name":"Kebab","amount":350,"protein":18,"fat":20,'
                        '"carbs":30,"kcal":360}]'
                    ),
                    "usage": {
                        "prompt_tokens": 12,
                        "completion_tokens": 7,
                        "total_tokens": 19,
                    },
                },
            ]
        ),
    )

    result = asyncio.run(
        text_meal_service.analyze_text_meal_with_usage(
            _payload_with_forbidden_context(),
            lang="pl",
        )
    )

    assert result["total_tokens"] == 34
    assert ask_chat.await_count == 2
    first_prompt = ask_chat.await_args_list[0].args[0]
    retry_prompt = ask_chat.await_args_list[1].args[0]
    assert isinstance(first_prompt, str)
    assert isinstance(retry_prompt, str)

    first_payload = _provider_payload_from_prompt(first_prompt)
    retry_payload = _provider_payload_from_prompt(retry_prompt)

    assert first_payload == {
        "name": "kebab",
        "ingredients": "kebab, pita, sauce",
        "amount_g": 350,
        "notes": "extra spicy",
        "lang": "pl",
    }
    assert retry_payload == first_payload
    _assert_no_forbidden_context(first_prompt, first_payload)
    _assert_no_forbidden_context(retry_prompt, retry_payload)
    assert "Your previous answer was invalid." in retry_prompt


def test_analyze_text_meal_raises_when_retry_is_still_zero_nutrition(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "app.services.text_meal_service.openai_service.ask_chat_completion",
        new=mocker.AsyncMock(
            side_effect=[
                {
                    "content": '[{"name":"Kebab"}]',
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                },
                {
                    "content": '[{"name":"Kebab again"}]',
                    "usage": {
                        "prompt_tokens": 12,
                        "completion_tokens": 7,
                        "total_tokens": 19,
                    },
                },
            ]
        ),
    )
    mocker.patch(
        "app.services.text_meal_service.openai_service.parse_ingredients_reply",
        side_effect=[
            [
                {
                    "name": "Kebab",
                    "amount": 350.0,
                    "protein": 0.0,
                    "fat": 0.0,
                    "carbs": 0.0,
                    "kcal": 0.0,
                }
            ],
            [
                {
                    "name": "Kebab",
                    "amount": 350.0,
                    "protein": 0.0,
                    "fat": 0.0,
                    "carbs": 0.0,
                    "kcal": 0.0,
                }
            ],
        ],
    )

    with pytest.raises(OpenAIServiceError):
        asyncio.run(text_meal_service.analyze_text_meal(_payload(), lang="pl"))
