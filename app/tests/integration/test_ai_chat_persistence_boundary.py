from __future__ import annotations

from typing import Any, cast

from app.core.config import settings
from app.core.firestore_constants import (
    AI_RUNS_COLLECTION,
    CHAT_THREADS_SUBCOLLECTION,
    MEMORY_SUBCOLLECTION,
    MESSAGES_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.schemas.ai_chat.request import ChatRunRequestDto
from app.tests.integration._ai_chat_v2_fixtures import (
    build_orchestrator_harness,
    generation_result,
    planner_result_payload,
)

_FORBIDDEN_PERSISTED_KEYS = {
    "rawPrompt",
    "rawResponse",
    "fullToolDump",
    "providerMessages",
    "rawToolOutput",
    "rawImage",
    "fullPayload",
    "profile",
    "history",
    "chat",
    "logs",
    "debug",
}

_FORBIDDEN_PERSISTED_SENTINELS = (
    "secret-provider-prompt",
    "secret-provider-response",
    "secret-tool-dump",
    "secret-debug-log",
    "secret-raw-image",
    "secret-full-payload",
    "secret-profile",
    "secret-history",
    "secret-chat",
    "secret-user-id",
)


def _assert_no_forbidden_persisted_payload(value: Any, *, path: str = "$") -> None:
    if isinstance(value, dict):
        payload = cast(dict[object, Any], value)
        for raw_key, item in payload.items():
            key = str(raw_key)
            assert key not in _FORBIDDEN_PERSISTED_KEYS, f"{path}.{key}"
            _assert_no_forbidden_persisted_payload(item, path=f"{path}.{key}")
        return

    if isinstance(value, list):
        for index, item in enumerate(cast(list[object], value)):
            _assert_no_forbidden_persisted_payload(item, path=f"{path}[{index}]")
        return

    if isinstance(value, str):
        for sentinel in _FORBIDDEN_PERSISTED_SENTINELS:
            assert sentinel not in value, f"{path} contains {sentinel}"


async def test_ai_chat_v2_persistence_excludes_raw_provider_and_tool_payloads() -> None:
    planner_result = planner_result_payload(
        task_type="data_grounded_answer",
        capabilities=[
            {"name": "resolve_time_scope", "priority": 1, "args": {"label": "today"}},
            {
                "name": "get_nutrition_period_summary",
                "priority": 2,
                "args": {
                    "type": "$tool.resolve_time_scope.type",
                    "startDate": "$tool.resolve_time_scope.startDate",
                    "endDate": "$tool.resolve_time_scope.endDate",
                    "timezone": "$tool.resolve_time_scope.timezone",
                    "isPartial": "$tool.resolve_time_scope.isPartial",
                },
            },
            {
                "name": "get_recent_chat_summary",
                "priority": 3,
                "args": {"threadId": "thread-boundary"},
            },
        ],
        response_mode="assessment_plus_guidance",
        topics=["nutrition", "memory"],
    )
    provider_result = generation_result(
        text="Visible assistant answer about today stays persisted.",
        prompt_tokens=210,
        completion_tokens=35,
        total_tokens=245,
    )
    object.__setattr__(
        provider_result,
        "rawResponse",
        {"content": "secret-provider-response", "debug": "secret-debug-log"},
    )
    object.__setattr__(
        provider_result,
        "providerMessages",
        [{"role": "developer", "content": "secret-provider-prompt"}],
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
                "rawPrompt": "secret-provider-prompt",
                "rawToolOutput": {"value": "secret-tool-dump"},
                "debug": {"logs": ["secret-debug-log"]},
            },
            "get_nutrition_period_summary": {
                "period": {
                    "type": "today",
                    "startDate": "2026-04-19",
                    "endDate": "2026-04-19",
                    "timezone": "Europe/Warsaw",
                    "isPartial": True,
                    "providerMessages": [
                        {"role": "developer", "content": "secret-provider-prompt"}
                    ],
                },
                "loggingCoverage": {
                    "daysInPeriod": 1,
                    "daysWithEntries": 1,
                    "mealCount": 3,
                    "coverageLevel": "high",
                    "rawToolOutput": "secret-tool-dump",
                },
                "totals": {
                    "kcal": 2100.0,
                    "proteinG": 140.0,
                    "fatG": 70.0,
                    "carbsG": 210.0,
                    "fullPayload": "secret-full-payload",
                },
                "dailyBreakdown": [],
                "signals": ["logging_consistent"],
                "reliability": {
                    "summaryConfidence": "high",
                    "reason": "sufficient_logging_coverage",
                    "debug": "secret-debug-log",
                },
                "rawResponse": "secret-provider-response",
                "fullToolDump": "secret-tool-dump",
                "rawImage": "secret-raw-image",
                "profile": {"email": "secret-profile", "userId": "secret-user-id"},
                "history": ["secret-history"],
                "chat": ["secret-chat"],
                "logs": ["secret-debug-log"],
            },
            "get_recent_chat_summary": {
                "summary": "Bounded summary.",
                "resolvedFacts": ["prefers quick dinners"],
                "lastTurns": [
                    {"role": "user", "content": "Allowed bounded recent user text."}
                ],
                "hasSummary": True,
                "source": "memory_summary",
                "rawToolOutput": "secret-tool-dump",
                "rawResponse": "secret-provider-response",
                "debug": {"logs": ["secret-debug-log"]},
            },
        },
        generator_script=[provider_result],
    )

    response = await harness.orchestrator.run(
        user_id="user-boundary",
        request=ChatRunRequestDto.model_validate(
            {
                "threadId": "thread-boundary",
                "clientMessageId": "client-boundary",
                "message": "Summarize my visible nutrition day.",
                "language": "en",
            }
        ),
    )

    run_doc = harness.db.docs[(AI_RUNS_COLLECTION, response.run_id)]
    thread_doc = harness.db.docs[
        (USERS_COLLECTION, "user-boundary", CHAT_THREADS_SUBCOLLECTION, "thread-boundary")
    ]
    message_docs = [
        payload
        for key, payload in harness.db.docs.items()
        if key[:5]
        == (
            USERS_COLLECTION,
            "user-boundary",
            CHAT_THREADS_SUBCOLLECTION,
            "thread-boundary",
            MESSAGES_SUBCOLLECTION,
        )
    ]
    summary_doc = harness.db.docs[
        (
            USERS_COLLECTION,
            "user-boundary",
            CHAT_THREADS_SUBCOLLECTION,
            "thread-boundary",
            MEMORY_SUBCOLLECTION,
            "current",
        )
    ]

    persisted_docs = [run_doc, thread_doc, summary_doc, *message_docs]
    for doc in persisted_docs:
        _assert_no_forbidden_persisted_payload(doc)

    assert run_doc["userId"] == "user-boundary"
    assert run_doc["threadId"] == "thread-boundary"
    assert run_doc["status"] == "completed"
    assert run_doc["outcome"] == "completed"
    assert run_doc["promptTokens"] == 210
    assert run_doc["completionTokens"] == 35
    assert run_doc["totalTokens"] == 245
    assert run_doc["toolsUsed"] == [
        "resolve_time_scope",
        "get_nutrition_period_summary",
        "get_recent_chat_summary",
    ]
    assert all(
        set(cast(dict[str, Any], metric)) == {"name", "durationMs", "success"}
        for metric in run_doc["toolMetrics"]
    )
    assert all(cast(dict[str, Any], metric)["success"] is True for metric in run_doc["toolMetrics"])

    metadata = cast(dict[str, Any], run_doc["metadata"])
    assert metadata["taskType"] == "data_grounded_answer"
    assert metadata["responseMode"] == "assessment_plus_guidance"
    assert metadata["scopeResolved"] == "today"
    assert metadata["historyTurns"] == 1
    assert metadata["followUpRequired"] is False
    assert metadata["scopeDecision"] == "ALLOW_NUTRITION"
    assert metadata["clientMessageId"] == "client-boundary"
    assert metadata["threadId"] == "thread-boundary"
    assert metadata["language"] == "en"
    assert metadata["creditCost"] == settings.AI_CREDIT_COST_CHAT
    assert metadata["creditDeducted"] is True
    assert metadata["creditRefunded"] is False
    assert metadata["balanceAfter"] == 9
    assert metadata["idempotentReplay"] is False
    assert "userId=user-boundary" in metadata["creditIdempotencyKey"]

    assert len(message_docs) == 2
    user_message = next(doc for doc in message_docs if doc["role"] == "user")
    assistant_message = next(doc for doc in message_docs if doc["role"] == "assistant")
    assert user_message["content"] == "Summarize my visible nutrition day."
    assert user_message["status"] == "accepted"
    assert user_message["clientMessageId"] == "client-boundary"
    assert user_message["language"] == "en"
    assert assistant_message["content"] == "Visible assistant answer about today stays persisted."
    assert assistant_message["status"] == "completed"
    assert assistant_message["runId"] == response.run_id

    assert thread_doc["title"] == "Summarize my visible nutrition day."
    assert thread_doc["lastMessage"] == "Visible assistant answer about today stays persisted."
    assert summary_doc["summary"].startswith(
        "user:Summarize my visible nutrition day. | assistant:Visible assistant answer"
    )
    assert summary_doc["coveredUntilMessageId"] == response.assistant_message_id
