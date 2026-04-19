from app.domain.ai_runs.models.ai_run import AiRun
from app.domain.chat_memory.models.chat_message import ChatMessage
from app.domain.chat_memory.models.chat_thread import ChatThread
from app.domain.chat_memory.models.memory_summary import MemorySummary
from app.infra.firestore.mappers.ai_run_mapper import run_from_document, run_to_document
from app.infra.firestore.mappers.chat_mapper import (
    message_from_document,
    message_to_document,
    summary_from_document,
    summary_to_document,
    thread_from_document,
    thread_to_document,
)


def test_chat_mapper_roundtrip_for_thread_message_and_summary() -> None:
    thread = ChatThread(
        id="thread-1",
        user_id="user-1",
        title="Tytul",
        status="active",
        created_at=100,
        updated_at=120,
        last_message="Czesc",
        last_message_at=120,
    )
    thread_doc = thread_to_document(thread)
    thread_loaded = thread_from_document(
        user_id="user-1",
        thread_id="thread-1",
        data=thread_doc,
    )
    assert thread_loaded.id == "thread-1"
    assert thread_loaded.last_message == "Czesc"

    message = ChatMessage(
        id="msg-1",
        user_id="user-1",
        thread_id="thread-1",
        role="assistant",
        content="Odpowiedz",
        status="completed",
        run_id="run-1",
        client_message_id=None,
        language="pl",
        deleted=False,
        created_at=110,
        updated_at=120,
        last_synced_at=120,
    )
    message_doc = message_to_document(message)
    message_loaded = message_from_document(
        user_id="user-1",
        thread_id="thread-1",
        message_id="msg-1",
        data=message_doc,
    )
    assert message_loaded.role == "assistant"
    assert message_loaded.run_id == "run-1"
    assert message_loaded.status == "completed"

    summary = MemorySummary(
        user_id="user-1",
        thread_id="thread-1",
        summary="Podsumowanie",
        resolved_facts=["fakt-1"],
        covered_until_message_id="msg-1",
        version=2,
        summary_model="gpt-4o-mini",
        created_at=150,
        updated_at=160,
    )
    summary_doc = summary_to_document(summary)
    summary_loaded = summary_from_document(
        user_id="user-1",
        thread_id="thread-1",
        data=summary_doc,
    )
    assert summary_loaded.version == 2
    assert summary_loaded.resolved_facts == ["fakt-1"]


def test_ai_run_mapper_roundtrip() -> None:
    run = AiRun(
        id="run-1",
        user_id="user-1",
        thread_id="thread-1",
        status="completed",
        outcome="completed",
        failure_reason=None,
        planner_used=True,
        tools_used=["resolve_time_scope"],
        tool_metrics=[{"name": "resolve_time_scope", "durationMs": 12}],
        summary_used=True,
        truncated=False,
        retry_count=1,
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        total_latency_ms=140,
        created_at=1000,
        updated_at=1010,
        metadata={"source": "test"},
    )
    document = run_to_document(run)
    loaded = run_from_document(run_id="run-1", data=document)
    assert loaded.id == "run-1"
    assert loaded.status == "completed"
    assert loaded.tools_used == ["resolve_time_scope"]
    assert loaded.total_tokens == 30
