from collections.abc import Callable, Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.api.v2.deps import get_chat_orchestrator
from app.core.config import settings
from app.core.errors import AiCreditsExhaustedDomainError, AiProviderRetryableError
from app.main import app
from app.schemas.ai_chat.request import ChatRunRequestDto
from app.schemas.ai_chat.response import ChatRunResponseDto
from app.schemas.ai_credits import AiCreditsStatus, CreditCosts
from tests.types import AuthHeaders

client = TestClient(app)


class _RecordingOrchestrator:
    def __init__(
        self,
        *,
        response: ChatRunResponseDto | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls = 0
        self.user_ids: list[str] = []
        self.requests: list[ChatRunRequestDto] = []

    async def run(
        self,
        *,
        user_id: str,
        request: ChatRunRequestDto,
    ) -> ChatRunResponseDto:
        self.calls += 1
        self.user_ids.append(user_id)
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise RuntimeError("test fake missing response")
        return self.response


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Generator[None, None, None]:
    app.dependency_overrides.pop(get_chat_orchestrator, None)
    yield
    app.dependency_overrides.pop(get_chat_orchestrator, None)


def _override_orchestrator(orchestrator: _RecordingOrchestrator) -> None:
    def _provider() -> _RecordingOrchestrator:
        return orchestrator

    app.dependency_overrides[get_chat_orchestrator] = _provider


def _valid_payload() -> dict[str, object]:
    return {
        "threadId": "thread-1",
        "clientMessageId": "client-msg-1",
        "message": "Podsumuj dzisiaj.",
        "language": "pl",
    }


def _without_client_message_id(payload: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "clientMessageId"}


def _with_empty_message(payload: dict[str, object]) -> dict[str, object]:
    return {**payload, "message": ""}


def _with_overlong_message(payload: dict[str, object]) -> dict[str, object]:
    return {**payload, "message": "x" * 4001}


def _credits_status(*, balance: int = 9) -> AiCreditsStatus:
    return AiCreditsStatus(
        userId="user-1",
        tier="free",
        balance=balance,
        allocation=settings.AI_CREDITS_FREE,
        periodStartAt=datetime(2026, 4, 19, tzinfo=UTC),
        periodEndAt=datetime(2026, 5, 19, tzinfo=UTC),
        costs=CreditCosts(
            chat=settings.AI_CREDIT_COST_CHAT,
            textMeal=settings.AI_CREDIT_COST_TEXT_MEAL,
            photo=settings.AI_CREDIT_COST_PHOTO,
        ),
    )


def _chat_response() -> ChatRunResponseDto:
    return ChatRunResponseDto.model_validate(
        {
            "runId": "run-1",
            "threadId": "thread-1",
            "clientMessageId": "client-msg-1",
            "assistantMessageId": "assistant-1",
            "reply": "ok",
            "usage": {"promptTokens": 1, "completionTokens": 2, "totalTokens": 3},
            "contextStats": {
                "usedSummary": False,
                "historyTurns": 0,
                "truncated": False,
                "scopeDecision": "ALLOW_APP",
            },
            "credits": _credits_status(balance=9).model_dump(mode="json"),
            "persistence": "backend_owned",
        }
    )


def test_kill_switch_disabled_returns_503_without_calling_orchestrator(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch.object(settings, "AI_CHAT_ENABLED", False)
    orchestrator = _RecordingOrchestrator(response=_chat_response())
    _override_orchestrator(orchestrator)

    response = client.post(
        "/api/v2/ai/chat/runs",
        json=_valid_payload(),
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "AI_CHAT_DISABLED",
            "message": "AI Chat v2 is temporarily disabled.",
        }
    }
    assert orchestrator.calls == 0


def test_successful_run_uses_authenticated_uid_and_serializes_response(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch.object(settings, "AI_CHAT_ENABLED", True)
    orchestrator = _RecordingOrchestrator(response=_chat_response())
    _override_orchestrator(orchestrator)

    response = client.post(
        "/api/v2/ai/chat/runs",
        json=_valid_payload(),
        headers=auth_headers("route-contract-user-42"),
    )

    assert response.status_code == 200
    assert response.json()["runId"] == "run-1"
    assert response.json()["credits"]["balance"] == 9
    assert orchestrator.calls == 1
    assert orchestrator.user_ids == ["route-contract-user-42"]


def test_provider_domain_error_returns_503_http_detail(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch.object(settings, "AI_CHAT_ENABLED", True)
    orchestrator = _RecordingOrchestrator(
        error=AiProviderRetryableError("AI provider is temporarily unavailable.")
    )
    _override_orchestrator(orchestrator)

    response = client.post(
        "/api/v2/ai/chat/runs",
        json=_valid_payload(),
        headers=auth_headers("route-contract-provider-user"),
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "AI_CHAT_PROVIDER_UNAVAILABLE",
            "message": "AI provider is temporarily unavailable.",
        }
    }
    assert orchestrator.calls == 1
    assert orchestrator.user_ids == ["route-contract-provider-user"]


def test_credits_exhausted_domain_error_returns_402_with_serialized_credits(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch.object(settings, "AI_CHAT_ENABLED", True)
    orchestrator = _RecordingOrchestrator(
        error=AiCreditsExhaustedDomainError(
            "AI credits exhausted.",
            credits_status=_credits_status(balance=0),
        )
    )
    _override_orchestrator(orchestrator)

    response = client.post(
        "/api/v2/ai/chat/runs",
        json=_valid_payload(),
        headers=auth_headers("route-contract-credits-user"),
    )

    assert response.status_code == 402
    body = response.json()
    detail = body["detail"]
    credits = detail["credits"]
    assert detail["code"] == "AI_CREDITS_EXHAUSTED"
    assert detail["message"] == "AI credits exhausted."
    assert credits["balance"] == 0
    assert credits["costs"] == {
        "chat": settings.AI_CREDIT_COST_CHAT,
        "textMeal": settings.AI_CREDIT_COST_TEXT_MEAL,
        "photo": settings.AI_CREDIT_COST_PHOTO,
    }
    assert orchestrator.calls == 1
    assert orchestrator.user_ids == ["route-contract-credits-user"]


def test_unexpected_orchestrator_error_returns_500_without_leaking_exception_text(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch.object(settings, "AI_CHAT_ENABLED", True)
    orchestrator = _RecordingOrchestrator(
        error=RuntimeError("raw provider secret stack text")
    )
    _override_orchestrator(orchestrator)

    response = client.post(
        "/api/v2/ai/chat/runs",
        json=_valid_payload(),
        headers=auth_headers("route-contract-internal-user"),
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "code": "AI_CHAT_INTERNAL_ERROR",
            "message": "AI Chat v2 run failed.",
        }
    }
    assert "raw provider secret stack text" not in response.text
    assert orchestrator.calls == 1
    assert orchestrator.user_ids == ["route-contract-internal-user"]


@pytest.mark.parametrize(
    "payload_builder",
    [
        _without_client_message_id,
        _with_empty_message,
        _with_overlong_message,
    ],
)
def test_malformed_payload_returns_422_before_orchestrator_run(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
    payload_builder: Callable[[dict[str, object]], dict[str, object]],
) -> None:
    mocker.patch.object(settings, "AI_CHAT_ENABLED", True)
    orchestrator = _RecordingOrchestrator(response=_chat_response())
    _override_orchestrator(orchestrator)

    response = client.post(
        "/api/v2/ai/chat/runs",
        json=payload_builder(_valid_payload()),
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 422
    assert orchestrator.calls == 0
