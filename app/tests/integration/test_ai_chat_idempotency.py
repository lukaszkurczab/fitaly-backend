from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.deps.auth import AuthenticatedUser
from app.api.v2.endpoints.ai_chat import create_chat_run
from app.core.config import settings
from app.core.errors import (
    AiProviderNonRetryableError,
    AiProviderRetryableError,
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


class _FailingRetryableOrchestrator:
    async def run(self, *, user_id: str, request: ChatRunRequestDto):
        del user_id, request
        raise AiProviderRetryableError("AI provider timed out or is temporarily unavailable.")


class _RecordingOrchestrator:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def run(self, *, user_id: str, request: ChatRunRequestDto):
        del user_id, request
        self.calls += 1
        return self.response


async def test_v2_endpoint_rejects_when_kill_switch_disabled(mocker) -> None:
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


async def test_v2_endpoint_runs_normally_when_kill_switch_enabled(mocker) -> None:
    mocker.patch.object(settings, "AI_CHAT_ENABLED", True)
    expected_response = ChatRunResponseDto(
        runId="run-1",
        threadId="thread-1",
        clientMessageId="msg-1",
        assistantMessageId="assistant-1",
        reply="ok",
        usage={"promptTokens": 1, "completionTokens": 1, "totalTokens": 2},
        contextStats={
            "usedSummary": False,
            "historyTurns": 1,
            "truncated": False,
            "scopeDecision": "ALLOW_APP",
        },
        credits=None,
        persistence="backend_owned",
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
        "code": "ai_provider_retryable_failed",
        "message": "AI provider timed out or is temporarily unavailable.",
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
        "code": "ai_chat_v2_internal_error",
        "message": "AI Chat v2 run failed.",
    }
