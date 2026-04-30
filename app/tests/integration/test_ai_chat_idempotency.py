from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from fastapi import HTTPException
from pytest_mock import MockerFixture

from app.api.deps.auth import AuthenticatedUser
from app.api.v2.endpoints.ai_chat import create_chat_run
from app.core.config import settings
from app.core.errors import (
    AiChatIdempotencyConflictError,
    AiProviderNonRetryableError,
    AiProviderRetryableError,
    AiProviderTimeoutError,
    ConsentRequiredError,
)
from app.core.firestore_constants import AI_RUNS_COLLECTION
from app.domain.chat.retry_policy import RetryPolicy
from app.schemas.ai_chat.request import ChatRunRequestDto
from app.schemas.ai_chat.response import ChatRunResponseDto
from app.tests.integration._ai_chat_v2_fixtures import (
    build_orchestrator_harness,
    generation_result,
    planner_result_payload,
)


class _RetryableProviderError(Exception):
    def __init__(self) -> None:
        super().__init__("temporary provider failure")
        self.status_code = 503


def _credits_payload(*, balance: int = 9) -> dict[str, Any]:
    return {
        "userId": "user-1",
        "tier": "free",
        "balance": balance,
        "allocation": settings.AI_CREDITS_FREE,
        "periodStartAt": "2026-04-19T00:00:00Z",
        "periodEndAt": "2026-05-19T00:00:00Z",
        "costs": {
            "chat": settings.AI_CREDIT_COST_CHAT,
            "textMeal": settings.AI_CREDIT_COST_TEXT_MEAL,
            "photo": settings.AI_CREDIT_COST_PHOTO,
        },
    }


async def test_ai_chat_v2_idempotent_replay_returns_existing_response() -> None:
    planner_result = planner_result_payload(
        task_type="data_grounded_answer",
        capabilities=[
            {"name": "resolve_time_scope", "priority": 1, "args": {"label": "today"}},
        ],
        response_mode="concise_answer",
    )
    harness = build_orchestrator_harness(
        planner_result=planner_result,
        tools={
            "resolve_time_scope": {
                "type": "today",
                "startDate": "2026-04-19",
                "endDate": "2026-04-19",
                "timezone": "Europe/Warsaw",
                "isPartial": True,
            }
        },
        generator_script=[generation_result(text="To juz masz policzone.")],
    )

    payload = ChatRunRequestDto.model_validate(
        {
            "threadId": "thread-idem",
            "clientMessageId": "idem-1",
            "message": "Policz dzisiaj kcal.",
            "language": "pl",
        }
    )

    first = await harness.orchestrator.run(user_id="user-idem", request=payload)
    second = await harness.orchestrator.run(user_id="user-idem", request=payload)

    assert first.run_id == second.run_id
    assert first.assistant_message_id == second.assistant_message_id
    assert first.reply == second.reply
    assert len(harness.generator.calls) == 1
    assert len(harness.credits_service.deductions) == 1
    assert harness.credits_service.balance == 9
    assert first.credits is not None
    assert first.credits.balance == 9
    assert second.credits is not None
    assert second.credits.balance == 9
    assert next(iter(harness.credits_service.idempotency.values()))["state"] == "completed"

    run = await harness.ai_run_service.get_run(run_id=first.run_id)
    assert run is not None
    assert run.metadata["creditCost"] == settings.AI_CREDIT_COST_CHAT
    assert run.metadata["creditDeducted"] is True
    assert run.metadata["creditRefunded"] is False
    assert run.metadata["balanceAfter"] == 9
    assert run.metadata["idempotentReplay"] is True


async def test_ai_chat_v2_consent_rejected_before_processing() -> None:
    planner_result = planner_result_payload(
        task_type="data_grounded_answer",
        capabilities=[],
        response_mode="concise_answer",
    )
    harness = build_orchestrator_harness(
        planner_result=planner_result,
        tools={},
        generator_script=[generation_result(text="ignored")],
        consent_allowed=False,
    )

    with pytest.raises(ConsentRequiredError):
        await harness.orchestrator.run(
            user_id="user-no-consent",
            request=ChatRunRequestDto.model_validate(
                {
                    "threadId": "thread-consent",
                    "clientMessageId": "consent-1",
                    "message": "Hej",
                    "language": "pl",
                }
            ),
        )


async def test_ai_chat_v2_provider_retryable_failure_updates_run_and_raises() -> None:
    planner_result = planner_result_payload(
        task_type="data_grounded_answer",
        capabilities=[{"name": "resolve_time_scope", "priority": 1, "args": {"label": "today"}}],
        response_mode="concise_answer",
    )
    harness = build_orchestrator_harness(
        planner_result=planner_result,
        tools={
            "resolve_time_scope": {
                "type": "today",
                "startDate": "2026-04-19",
                "endDate": "2026-04-19",
                "timezone": "Europe/Warsaw",
                "isPartial": True,
            }
        },
        generator_script=[_RetryableProviderError(), _RetryableProviderError()],
        retry_policy=RetryPolicy(
            max_attempts=2,
            timeout_seconds=0.2,
            base_delay_seconds=0.0,
            jitter_seconds=0.0,
        ),
    )

    with pytest.raises(AiProviderRetryableError):
        await harness.orchestrator.run(
            user_id="user-provider",
            request=ChatRunRequestDto.model_validate(
                {
                    "threadId": "thread-provider",
                    "clientMessageId": "provider-1",
                    "message": "Podsumuj dzisiaj",
                    "language": "pl",
                }
            ),
        )

    run_docs = [payload for key, payload in harness.db.docs.items() if key[0] == AI_RUNS_COLLECTION]
    assert len(run_docs) == 1
    run_doc = run_docs[0]
    assert run_doc["status"] == "failed"
    assert run_doc["failureReason"] == "provider_retryable_error"
    assert run_doc["retryCount"] == 1
    assert run_doc["metadata"]["creditDeducted"] is True
    assert run_doc["metadata"]["creditRefunded"] is True
    assert run_doc["metadata"]["balanceAfter"] == 10
    assert len(harness.credits_service.deductions) == 1
    assert len(harness.credits_service.refunds) == 1


async def test_ai_chat_v2_provider_non_retryable_failure_updates_run_and_raises() -> None:
    planner_result = planner_result_payload(
        task_type="data_grounded_answer",
        capabilities=[{"name": "resolve_time_scope", "priority": 1, "args": {"label": "today"}}],
        response_mode="concise_answer",
    )
    harness = build_orchestrator_harness(
        planner_result=planner_result,
        tools={
            "resolve_time_scope": {
                "type": "today",
                "startDate": "2026-04-19",
                "endDate": "2026-04-19",
                "timezone": "Europe/Warsaw",
                "isPartial": True,
            }
        },
        generator_script=[RuntimeError("invalid provider payload")],
        retry_policy=RetryPolicy(
            max_attempts=2,
            timeout_seconds=0.2,
            base_delay_seconds=0.0,
            jitter_seconds=0.0,
        ),
    )

    with pytest.raises(AiProviderNonRetryableError):
        await harness.orchestrator.run(
            user_id="user-provider",
            request=ChatRunRequestDto.model_validate(
                {
                    "threadId": "thread-provider-2",
                    "clientMessageId": "provider-2",
                    "message": "Podsumuj dzisiaj",
                    "language": "pl",
                }
            ),
        )

    run_docs = [payload for key, payload in harness.db.docs.items() if key[0] == AI_RUNS_COLLECTION]
    assert len(run_docs) == 1
    run_doc = run_docs[0]
    assert run_doc["status"] == "failed"
    assert run_doc["failureReason"] == "provider_non_retryable_error"
    assert run_doc["retryCount"] == 0
    assert run_doc["metadata"]["creditDeducted"] is True
    assert run_doc["metadata"]["creditRefunded"] is True
    assert run_doc["metadata"]["balanceAfter"] == 10
    assert len(harness.credits_service.deductions) == 1
    assert len(harness.credits_service.refunds) == 1


async def test_ai_chat_v2_exhausted_credits_returns_402_with_current_status() -> None:
    planner_result = planner_result_payload(
        task_type="data_grounded_answer",
        capabilities=[],
        response_mode="concise_answer",
    )
    harness = build_orchestrator_harness(
        planner_result=planner_result,
        tools={},
        generator_script=[generation_result(text="ignored")],
        initial_credits=0,
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_chat_run(
            payload=ChatRunRequestDto.model_validate(
                {
                    "threadId": "thread-no-credits",
                    "clientMessageId": "no-credits-1",
                    "message": "Hej",
                    "language": "pl",
                }
            ),
            current_user=AuthenticatedUser(uid="user-no-credits", claims={}),
            orchestrator=harness.orchestrator,
    )

    assert exc_info.value.status_code == 402
    detail = cast(dict[str, Any], exc_info.value.detail)
    credits = cast(dict[str, Any], detail["credits"])
    costs = cast(dict[str, Any], credits["costs"])
    assert detail["code"] == "AI_CREDITS_EXHAUSTED"
    assert credits["balance"] == 0
    assert costs["chat"] == settings.AI_CREDIT_COST_CHAT
    assert len(harness.credits_service.deductions) == 0
    assert len(harness.generator.calls) == 0


async def test_ai_chat_v2_retry_after_refunded_failure_charges_final_success_once() -> None:
    planner_result = planner_result_payload(
        task_type="data_grounded_answer",
        capabilities=[{"name": "resolve_time_scope", "priority": 1, "args": {"label": "today"}}],
        response_mode="concise_answer",
    )
    harness = build_orchestrator_harness(
        planner_result=planner_result,
        tools={
            "resolve_time_scope": {
                "type": "today",
                "startDate": "2026-04-19",
                "endDate": "2026-04-19",
                "timezone": "Europe/Warsaw",
                "isPartial": True,
            }
        },
        generator_script=[
            RuntimeError("provider failed after debit"),
            generation_result(text="Retry zakonczony powodzeniem."),
        ],
        retry_policy=RetryPolicy(
            max_attempts=1,
            timeout_seconds=0.2,
            base_delay_seconds=0.0,
            jitter_seconds=0.0,
        ),
    )
    payload = ChatRunRequestDto.model_validate(
        {
            "threadId": "thread-partial",
            "clientMessageId": "partial-1",
            "message": "Podsumuj dzisiaj",
            "language": "pl",
        }
    )

    with pytest.raises(AiProviderNonRetryableError):
        await harness.orchestrator.run(user_id="user-partial", request=payload)

    second = await harness.orchestrator.run(user_id="user-partial", request=payload)

    assert second.reply == "Retry zakonczony powodzeniem."
    assert len(harness.credits_service.deductions) == 2
    assert len(harness.credits_service.refunds) == 1
    assert harness.credits_service.balance == 9
    assert second.credits is not None
    assert second.credits.balance == 9

    run = await harness.ai_run_service.get_run(run_id=second.run_id)
    assert run is not None
    assert run.metadata["creditDeducted"] is True
    assert run.metadata["creditRefunded"] is False
    assert "creditDeductIdempotentReplay" not in run.metadata
    assert next(iter(harness.credits_service.idempotency.values()))["state"] == "completed"


async def test_ai_chat_v2_retry_after_refunded_failure_and_second_failure_refunds_again() -> None:
    planner_result = planner_result_payload(
        task_type="data_grounded_answer",
        capabilities=[{"name": "resolve_time_scope", "priority": 1, "args": {"label": "today"}}],
        response_mode="concise_answer",
    )
    harness = build_orchestrator_harness(
        planner_result=planner_result,
        tools={
            "resolve_time_scope": {
                "type": "today",
                "startDate": "2026-04-19",
                "endDate": "2026-04-19",
                "timezone": "Europe/Warsaw",
                "isPartial": True,
            }
        },
        generator_script=[
            RuntimeError("provider failed after first debit"),
            RuntimeError("provider failed after retry debit"),
        ],
        retry_policy=RetryPolicy(
            max_attempts=1,
            timeout_seconds=0.2,
            base_delay_seconds=0.0,
            jitter_seconds=0.0,
        ),
    )
    payload = ChatRunRequestDto.model_validate(
        {
            "threadId": "thread-second-failure",
            "clientMessageId": "second-failure-1",
            "message": "Podsumuj dzisiaj",
            "language": "pl",
        }
    )

    with pytest.raises(AiProviderNonRetryableError):
        await harness.orchestrator.run(user_id="user-second-failure", request=payload)

    with pytest.raises(AiProviderNonRetryableError):
        await harness.orchestrator.run(user_id="user-second-failure", request=payload)

    assert len(harness.credits_service.deductions) == 2
    assert len(harness.credits_service.refunds) == 2
    assert harness.credits_service.balance == 10
    assert next(iter(harness.credits_service.idempotency.values()))["state"] == "refunded"


async def test_ai_chat_v2_out_of_scope_refusal_is_billable_after_planner() -> None:
    planner_result = planner_result_payload(
        task_type="out_of_scope_refusal",
        capabilities=[],
        response_mode="refusal_redirect",
        requires_user_data=False,
        topics=["non_app"],
    )
    harness = build_orchestrator_harness(
        planner_result=planner_result,
        tools={},
        generator_script=[],
    )

    response = await harness.orchestrator.run(
        user_id="user-refusal",
        request=ChatRunRequestDto.model_validate(
            {
                "threadId": "thread-refusal",
                "clientMessageId": "refusal-1",
                "message": "Napisz mi plan treningowy na silownie.",
                "language": "pl",
            }
        ),
    )

    assert response.context_stats.scope_decision == "DENY_OTHER"
    assert response.credits is not None
    assert response.credits.balance == 9
    assert len(harness.credits_service.deductions) == 1
    assert len(harness.generator.calls) == 0


async def test_ai_chat_v2_concurrent_same_client_message_id_charges_once() -> None:
    planner_result = planner_result_payload(
        task_type="data_grounded_answer",
        capabilities=[],
        response_mode="concise_answer",
    )
    harness = build_orchestrator_harness(
        planner_result=planner_result,
        tools={},
        generator_script=[
            generation_result(text="Pierwsza odpowiedz."),
            generation_result(text="Druga odpowiedz."),
        ],
    )
    payload = ChatRunRequestDto.model_validate(
        {
            "threadId": "thread-concurrent",
            "clientMessageId": "concurrent-1",
            "message": "Hej",
            "language": "pl",
        }
    )

    results = await asyncio.gather(
        harness.orchestrator.run(user_id="user-concurrent", request=payload),
        harness.orchestrator.run(user_id="user-concurrent", request=payload),
    )

    assert len(results) == 2
    assert len(harness.credits_service.deductions) == 1
    assert harness.credits_service.balance == 9


class _FailingRetryableOrchestrator:
    async def run(self, *, user_id: str, request: ChatRunRequestDto):
        del user_id, request
        raise AiProviderRetryableError("AI provider is temporarily unavailable.")


class _FailingTimeoutOrchestrator:
    async def run(self, *, user_id: str, request: ChatRunRequestDto):
        del user_id, request
        raise AiProviderTimeoutError("AI provider timed out before a response was generated.")


class _FailingIdempotencyConflictOrchestrator:
    async def run(self, *, user_id: str, request: ChatRunRequestDto):
        del user_id, request
        raise AiChatIdempotencyConflictError(
            "AI Chat run is already in progress or missing a replayable assistant response."
        )


class _RecordingOrchestrator:
    def __init__(self, response: ChatRunResponseDto | None) -> None:
        self.response = response
        self.calls = 0

    async def run(
        self,
        *,
        user_id: str,
        request: ChatRunRequestDto,
    ) -> ChatRunResponseDto | None:
        del user_id, request
        self.calls += 1
        return self.response


async def test_v2_endpoint_rejects_when_kill_switch_disabled(mocker: MockerFixture) -> None:
    mocker.patch.object(settings, "AI_CHAT_ENABLED", False)
    orchestrator = _RecordingOrchestrator(response=None)

    with pytest.raises(HTTPException) as exc_info:
        await create_chat_run(
            payload=ChatRunRequestDto.model_validate(
                {
                    "threadId": "thread-1",
                    "clientMessageId": "msg-1",
                    "message": "test",
                    "language": "pl",
                }
            ),
            current_user=AuthenticatedUser(uid="user-1", claims={}),
            orchestrator=orchestrator,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "code": "AI_CHAT_DISABLED",
        "message": "AI Chat v2 is temporarily disabled.",
    }
    assert orchestrator.calls == 0


async def test_v2_endpoint_runs_normally_when_kill_switch_enabled(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(settings, "AI_CHAT_ENABLED", True)
    expected_response = ChatRunResponseDto.model_validate(
        {
            "runId": "run-1",
            "threadId": "thread-1",
            "clientMessageId": "msg-1",
            "assistantMessageId": "assistant-1",
            "reply": "ok",
            "usage": {"promptTokens": 1, "completionTokens": 1, "totalTokens": 2},
            "contextStats": {
                "usedSummary": False,
                "historyTurns": 1,
                "truncated": False,
                "scopeDecision": "ALLOW_APP",
            },
            "credits": _credits_payload(balance=9),
            "persistence": "backend_owned",
        }
    )
    orchestrator = _RecordingOrchestrator(response=expected_response)

    response = await create_chat_run(
        payload=ChatRunRequestDto.model_validate(
            {
                "threadId": "thread-1",
                "clientMessageId": "msg-1",
                "message": "test",
                "language": "pl",
            }
        ),
        current_user=AuthenticatedUser(uid="user-1", claims={}),
        orchestrator=orchestrator,  # type: ignore[arg-type]
    )

    assert response == expected_response
    assert orchestrator.calls == 1


async def test_v2_endpoint_maps_domain_error_to_api_contract() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await create_chat_run(
            payload=ChatRunRequestDto.model_validate(
                {
                    "threadId": "thread-1",
                    "clientMessageId": "msg-1",
                    "message": "test",
                    "language": "pl",
                }
            ),
            current_user=AuthenticatedUser(uid="user-1", claims={}),
            orchestrator=_FailingRetryableOrchestrator(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "code": "AI_CHAT_PROVIDER_UNAVAILABLE",
        "message": "AI provider is temporarily unavailable.",
    }


async def test_v2_endpoint_maps_timeout_error_to_api_contract() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await create_chat_run(
            payload=ChatRunRequestDto.model_validate(
                {
                    "threadId": "thread-1",
                    "clientMessageId": "msg-1",
                    "message": "test",
                    "language": "pl",
                }
            ),
            current_user=AuthenticatedUser(uid="user-1", claims={}),
            orchestrator=_FailingTimeoutOrchestrator(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail == {
        "code": "AI_CHAT_TIMEOUT",
        "message": "AI provider timed out before a response was generated.",
    }


async def test_v2_endpoint_maps_idempotency_conflict_to_api_contract() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await create_chat_run(
            payload=ChatRunRequestDto.model_validate(
                {
                    "threadId": "thread-1",
                    "clientMessageId": "msg-1",
                    "message": "test",
                    "language": "pl",
                }
            ),
            current_user=AuthenticatedUser(uid="user-1", claims={}),
            orchestrator=_FailingIdempotencyConflictOrchestrator(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "code": "AI_CHAT_IDEMPOTENCY_CONFLICT",
        "message": "AI Chat run is already in progress or missing a replayable assistant response.",
    }


class _FailingUnexpectedOrchestrator:
    async def run(self, *, user_id: str, request: ChatRunRequestDto):
        del user_id, request
        raise RuntimeError("unexpected")


async def test_v2_endpoint_maps_unexpected_error_to_internal_contract() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await create_chat_run(
            payload=ChatRunRequestDto.model_validate(
                {
                    "threadId": "thread-1",
                    "clientMessageId": "msg-1",
                    "message": "test",
                    "language": "pl",
                }
            ),
            current_user=AuthenticatedUser(uid="user-1", claims={}),
            orchestrator=_FailingUnexpectedOrchestrator(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == {
        "code": "AI_CHAT_INTERNAL_ERROR",
        "message": "AI Chat v2 run failed.",
    }
