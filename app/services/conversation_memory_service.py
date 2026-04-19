"""Legacy v1 conversation memory for `/api/v1/ai/ask`.

Canonical v2 thread memory is implemented in `app/domain/chat_memory/*`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import (
    CHAT_THREADS_SUBCOLLECTION,
    MEMORY_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.db.firebase import get_firestore

logger = logging.getLogger(__name__)

_MEMORY_DOC_ID = "v1"


def _memory_ref(user_id: str, thread_id: str) -> firestore.DocumentReference:
    client: firestore.Client = get_firestore()
    return (
        client.collection(USERS_COLLECTION)
        .document(user_id)
        .collection(CHAT_THREADS_SUBCOLLECTION)
        .document(thread_id)
        .collection(MEMORY_SUBCOLLECTION)
        .document(_MEMORY_DOC_ID)
    )


def _summarize_messages(messages: list[dict[str, Any]]) -> str:
    if not messages:
        return "none"

    lines: list[str] = []
    for item in messages[-16:]:
        role = str(item.get("role") or "assistant").strip().lower()
        if role not in {"assistant", "user", "system"}:
            role = "assistant"
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        clipped = content if len(content) <= 140 else f"{content[:139].rstrip()}…"
        lines.append(f"{role}:{clipped}")

    if not lines:
        return "none"

    summary = " | ".join(lines)
    return summary if len(summary) <= 1_200 else f"{summary[:1199].rstrip()}…"


async def get_thread_summary(user_id: str, thread_id: str) -> str | None:
    ref = _memory_ref(user_id, thread_id)

    try:
        snapshot = ref.get()
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to read conversation memory summary.",
            extra={"user_id": user_id, "thread_id": thread_id},
        )
        raise FirestoreServiceError("Failed to read conversation memory summary.") from exc

    if not snapshot.exists:
        return None

    data = dict(snapshot.to_dict() or {})
    summary = data.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    return None


async def upsert_thread_summary(
    user_id: str,
    thread_id: str,
    *,
    summary: str,
    covered_until_message_id: str | None = None,
) -> None:
    ref = _memory_ref(user_id, thread_id)
    payload: dict[str, Any] = {
        "summary": summary,
        "updatedAt": int(datetime.now(timezone.utc).timestamp() * 1000),
    }
    if covered_until_message_id:
        payload["coveredUntilMessageId"] = covered_until_message_id

    try:
        ref.set(payload, merge=True)
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to store conversation memory summary.",
            extra={"user_id": user_id, "thread_id": thread_id},
        )
        raise FirestoreServiceError("Failed to store conversation memory summary.") from exc


async def refresh_summary_from_history(
    user_id: str,
    thread_id: str,
    history_messages: list[dict[str, Any]],
    *,
    covered_until_message_id: str | None = None,
) -> str:
    summary = _summarize_messages(history_messages)
    await upsert_thread_summary(
        user_id,
        thread_id,
        summary=summary,
        covered_until_message_id=covered_until_message_id,
    )
    return summary
