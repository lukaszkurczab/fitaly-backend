from typing import Any, cast

from app.core.coercion import coerce_int, coerce_optional_int
from app.domain.chat_memory.models.chat_message import ChatMessage
from app.domain.chat_memory.models.chat_thread import ChatThread
from app.domain.chat_memory.models.memory_summary import MemorySummary


def _as_str(value: object, *, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_as_str(v).strip() for v in value) if item]


def thread_to_document(thread: ChatThread) -> dict[str, Any]:
    return {
        "title": thread.title,
        "status": thread.status,
        "createdAt": thread.created_at,
        "updatedAt": thread.updated_at,
        "lastMessage": thread.last_message,
        "lastMessageAt": thread.last_message_at,
    }


def thread_from_document(
    *,
    user_id: str,
    thread_id: str,
    data: dict[str, Any],
) -> ChatThread:
    created_at = coerce_int(data.get("createdAt"))
    updated_at = coerce_int(data.get("updatedAt"), fallback=created_at)
    return ChatThread(
        id=thread_id,
        user_id=user_id,
        title=_as_str(data.get("title")),
        status=_as_str(data.get("status"), default="active") or "active",
        created_at=created_at,
        updated_at=updated_at,
        last_message=cast(str | None, data.get("lastMessage"))
        if data.get("lastMessage") is None or isinstance(data.get("lastMessage"), str)
        else _as_str(data.get("lastMessage")),
        last_message_at=coerce_optional_int(data.get("lastMessageAt")),
    )


def message_to_document(message: ChatMessage) -> dict[str, Any]:
    last_synced_at = message.last_synced_at
    if last_synced_at is None:
        last_synced_at = message.updated_at
    return {
        "role": message.role,
        "content": message.content,
        "status": message.status,
        "runId": message.run_id,
        "clientMessageId": message.client_message_id,
        "language": message.language,
        "deleted": message.deleted,
        "createdAt": message.created_at,
        "updatedAt": message.updated_at,
        "lastSyncedAt": last_synced_at,
    }


def message_from_document(
    *,
    user_id: str,
    thread_id: str,
    message_id: str,
    data: dict[str, Any],
) -> ChatMessage:
    role = _as_str(data.get("role"), default="assistant")
    if role not in {"user", "assistant", "system"}:
        role = "assistant"
    status = _as_str(data.get("status"), default="completed")
    if status not in {"accepted", "completed", "failed"}:
        status = "completed"

    created_at = coerce_int(data.get("createdAt"))
    updated_at = coerce_int(data.get("updatedAt"), fallback=created_at)
    return ChatMessage(
        id=message_id,
        user_id=user_id,
        thread_id=thread_id,
        role=role,
        content=_as_str(data.get("content")),
        status=status,
        run_id=_as_str(data.get("runId")).strip() or None,
        client_message_id=_as_str(data.get("clientMessageId")).strip() or None,
        language=_as_str(data.get("language")).strip() or None,
        deleted=bool(data.get("deleted") or False),
        created_at=created_at,
        updated_at=updated_at,
        last_synced_at=coerce_int(data.get("lastSyncedAt"), fallback=updated_at),
    )


def summary_to_document(summary: MemorySummary) -> dict[str, Any]:
    return {
        "summary": summary.summary,
        "resolvedFacts": summary.resolved_facts,
        "coveredUntilMessageId": summary.covered_until_message_id,
        "version": summary.version,
        "summaryModel": summary.summary_model,
        "createdAt": summary.created_at,
        "updatedAt": summary.updated_at,
    }


def summary_from_document(
    *,
    user_id: str,
    thread_id: str,
    data: dict[str, Any],
) -> MemorySummary:
    created_at = coerce_int(data.get("createdAt"))
    updated_at = coerce_int(data.get("updatedAt"), fallback=created_at)
    return MemorySummary(
        user_id=user_id,
        thread_id=thread_id,
        summary=_as_str(data.get("summary")),
        resolved_facts=_as_str_list(data.get("resolvedFacts")),
        covered_until_message_id=_as_str(data.get("coveredUntilMessageId")).strip() or None,
        version=coerce_int(data.get("version"), fallback=1),
        summary_model=_as_str(data.get("summaryModel")).strip() or None,
        created_at=created_at,
        updated_at=updated_at,
    )
