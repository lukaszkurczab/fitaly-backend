"""AI Chat thread/message projection reads.

This service exposes read-only projection helpers for v2 routes.
Canonical v2 chat writes live in `app/domain/chat_memory/*`.
"""

import logging
from typing import Any

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.coercion import coerce_int, coerce_optional_int
from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import (
    CHAT_THREADS_SUBCOLLECTION,
    MESSAGES_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.db.firebase import get_firestore

logger = logging.getLogger(__name__)


def _threads_collection(user_id: str) -> firestore.CollectionReference:
    client: firestore.Client = get_firestore()
    return (
        client.collection(USERS_COLLECTION)
        .document(user_id)
        .collection(CHAT_THREADS_SUBCOLLECTION)
    )


def _thread_ref(user_id: str, thread_id: str) -> firestore.DocumentReference:
    return _threads_collection(user_id).document(thread_id)


def _messages_collection(
    user_id: str,
    thread_id: str,
) -> firestore.CollectionReference:
    return _thread_ref(user_id, thread_id).collection(MESSAGES_SUBCOLLECTION)


def _normalize_thread(
    snapshot: firestore.DocumentSnapshot,
) -> dict[str, Any]:
    data = dict(snapshot.to_dict() or {})
    return {
        "id": snapshot.id,
        "title": str(data.get("title") or ""),
        "createdAt": coerce_int(data.get("createdAt")),
        "updatedAt": coerce_int(data.get("updatedAt")),
        "lastMessage": str(data.get("lastMessage") or "") or None,
        "lastMessageAt": coerce_optional_int(data.get("lastMessageAt")),
    }


def _normalize_message(
    snapshot: firestore.DocumentSnapshot,
) -> dict[str, Any]:
    data = dict(snapshot.to_dict() or {})
    role = str(data.get("role") or "assistant")
    if role not in {"user", "assistant", "system"}:
        role = "assistant"

    created_at = coerce_int(data.get("createdAt"))
    return {
        "id": snapshot.id,
        "role": role,
        "content": str(data.get("content") or ""),
        "createdAt": created_at,
        "lastSyncedAt": coerce_int(data.get("lastSyncedAt"), fallback=created_at),
        "deleted": bool(data.get("deleted") or False),
    }


async def list_threads(
    user_id: str,
    *,
    limit_count: int = 20,
    before_updated_at: int | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    threads_ref = _threads_collection(user_id)

    try:
        query = threads_ref.order_by("updatedAt", direction=firestore.Query.DESCENDING)
        if before_updated_at is not None:
            query = query.where("updatedAt", "<", before_updated_at)
        snapshots = list(query.limit(limit_count).stream())
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to list chat threads.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to list chat threads.") from exc

    items = [_normalize_thread(snapshot) for snapshot in snapshots]
    next_before_updated_at = items[-1]["updatedAt"] if len(items) == limit_count else None
    return items, next_before_updated_at


async def list_messages(
    user_id: str,
    thread_id: str,
    *,
    limit_count: int = 50,
    before_created_at: int | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    messages_ref = _messages_collection(user_id, thread_id)

    try:
        query = messages_ref.order_by("createdAt", direction=firestore.Query.DESCENDING)
        if before_created_at is not None:
            query = query.where("createdAt", "<", before_created_at)
        snapshots = list(query.limit(limit_count).stream())
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to list chat messages.",
            extra={"user_id": user_id, "thread_id": thread_id},
        )
        raise FirestoreServiceError("Failed to list chat messages.") from exc

    items = [_normalize_message(snapshot) for snapshot in snapshots]
    next_before_created_at = items[-1]["createdAt"] if len(items) == limit_count else None
    return items, next_before_created_at

