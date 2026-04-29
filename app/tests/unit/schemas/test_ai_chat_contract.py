from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.ai_chat.request import ChatRunRequestDto
from app.schemas.ai_chat.response import ChatRunResponseDto


def test_ai_chat_v2_request_requires_minimal_explicit_contract() -> None:
    payload = ChatRunRequestDto.model_validate(
        {
            "threadId": "thread-1",
            "clientMessageId": "client-1",
            "message": "Hej",
            "language": "pl",
            "uiContext": {
                "screen": "ChatScreen",
                "entryPoint": "fab",
            },
        }
    )

    assert payload.model_dump(by_alias=True) == {
        "threadId": "thread-1",
        "clientMessageId": "client-1",
        "message": "Hej",
        "language": "pl",
        "uiContext": {
            "screen": "ChatScreen",
            "entryPoint": "fab",
        },
    }


def test_ai_chat_v2_request_rejects_legacy_or_missing_fields() -> None:
    with pytest.raises(ValidationError):
        ChatRunRequestDto.model_validate(
            {
                "threadId": "thread-1",
                "clientMessageId": "client-1",
                "message": "Hej",
            }
        )

    with pytest.raises(ValidationError):
        ChatRunRequestDto.model_validate(
            {
                "threadId": "thread-1",
                "clientMessageId": "client-1",
                "message": "Hej",
                "language": "pl",
                "scopeDecision": "ALLOW_NUTRITION",
            }
        )

    with pytest.raises(ValidationError):
        ChatRunRequestDto.model_validate(
            {
                "threadId": "thread-1",
                "clientMessageId": "client-1",
                "message": "Hej",
                "language": "pl",
                "meals": [],
            }
        )

    with pytest.raises(ValidationError):
        ChatRunRequestDto.model_validate(
            {
                "threadId": "thread-1",
                "clientMessageId": "client-1",
                "message": "Hej",
                "language": "pl",
                "profile": {"goal": "maintain"},
            }
        )


def test_ai_chat_v2_response_serializes_minimal_contract_only() -> None:
    response = ChatRunResponseDto.model_validate(
        {
            "runId": "run-1",
            "threadId": "thread-1",
            "clientMessageId": "client-1",
            "assistantMessageId": "assistant-1",
            "reply": "Czesc",
            "usage": {
                "promptTokens": 10,
                "completionTokens": 5,
                "totalTokens": 15,
            },
            "contextStats": {
                "usedSummary": False,
                "historyTurns": 2,
                "truncated": False,
                "scopeDecision": "ALLOW_NUTRITION",
            },
            "credits": None,
            "persistence": "backend_owned",
        }
    )

    assert response.model_dump(by_alias=True) == {
        "runId": "run-1",
        "threadId": "thread-1",
        "clientMessageId": "client-1",
        "assistantMessageId": "assistant-1",
        "reply": "Czesc",
        "usage": {
            "promptTokens": 10,
            "completionTokens": 5,
            "totalTokens": 15,
        },
        "contextStats": {
            "usedSummary": False,
            "historyTurns": 2,
            "truncated": False,
            "scopeDecision": "ALLOW_NUTRITION",
        },
        "credits": None,
        "persistence": "backend_owned",
    }
