import asyncio

import pytest
from pytest_mock import MockerFixture

from app.core.exceptions import OpenAIServiceError
from app.schemas.ai_text_meal import AiTextMealPayload
from app.services import text_meal_service


def _payload() -> AiTextMealPayload:
    return AiTextMealPayload(
        name="kebab",
        ingredients="kebab",
        amount_g=350,
        notes=None,
    )


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
