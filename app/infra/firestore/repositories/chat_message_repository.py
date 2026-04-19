from typing import Any

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import (
    CHAT_THREADS_SUBCOLLECTION,
    MESSAGES_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.db.firebase import get_firestore


class ChatMessageRepository:
    def __init__(self, firestore_client: firestore.Client | None = None) -> None:
        self._db = firestore_client or get_firestore()

    def _messages_collection(
        self, *, user_id: str, thread_id: str
    ) -> firestore.CollectionReference:
        return (
            self._db.collection(USERS_COLLECTION)
            .document(user_id)
            .collection(CHAT_THREADS_SUBCOLLECTION)
            .document(thread_id)
            .collection(MESSAGES_SUBCOLLECTION)
        )

    def _message_ref(
        self,
        *,
        user_id: str,
        thread_id: str,
        message_id: str,
    ) -> firestore.DocumentReference:
        return self._messages_collection(user_id=user_id, thread_id=thread_id).document(
            message_id
        )

    async def create(
        self,
        *,
        user_id: str,
        thread_id: str,
        message_id: str,
        payload: dict[str, Any],
        merge: bool = False,
    ) -> None:
        try:
            self._message_ref(
                user_id=user_id, thread_id=thread_id, message_id=message_id
            ).set(payload, merge=merge)
        except (FirebaseError, GoogleAPICallError, RetryError) as exc:
            raise FirestoreServiceError("Failed to create chat message.") from exc

    async def get(
        self,
        *,
        user_id: str,
        thread_id: str,
        message_id: str,
    ) -> dict[str, Any] | None:
        try:
            snapshot = self._message_ref(
                user_id=user_id, thread_id=thread_id, message_id=message_id
            ).get()
        except (FirebaseError, GoogleAPICallError, RetryError) as exc:
            raise FirestoreServiceError("Failed to read chat message.") from exc
        if not snapshot.exists:
            return None
        return dict(snapshot.to_dict() or {})

    async def list_recent(
        self,
        *,
        user_id: str,
        thread_id: str,
        limit: int,
        before_created_at: int | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        try:
            query = self._messages_collection(user_id=user_id, thread_id=thread_id).order_by(
                "createdAt", direction=firestore.Query.DESCENDING
            )
            if before_created_at is not None:
                query = query.where("createdAt", "<", before_created_at)
            snapshots = list(query.limit(limit).stream())
        except (FirebaseError, GoogleAPICallError, RetryError) as exc:
            raise FirestoreServiceError("Failed to list chat messages.") from exc

        items: list[tuple[str, dict[str, Any]]] = []
        for snapshot in snapshots:
            items.append((snapshot.id, dict(snapshot.to_dict() or {})))
        return items

    async def find_by_client_message_id(
        self,
        *,
        user_id: str,
        thread_id: str,
        client_message_id: str,
    ) -> tuple[str, dict[str, Any]] | None:
        try:
            query = (
                self._messages_collection(user_id=user_id, thread_id=thread_id)
                .where("clientMessageId", "==", client_message_id)
                .limit(1)
            )
            snapshots = list(query.stream())
        except (FirebaseError, GoogleAPICallError, RetryError) as exc:
            raise FirestoreServiceError("Failed to lookup chat message idempotency.") from exc

        if not snapshots:
            return None
        snapshot = snapshots[0]
        return snapshot.id, dict(snapshot.to_dict() or {})

    async def list_by_run_id(
        self,
        *,
        user_id: str,
        thread_id: str,
        run_id: str,
        limit: int = 8,
    ) -> list[tuple[str, dict[str, Any]]]:
        try:
            query = (
                self._messages_collection(user_id=user_id, thread_id=thread_id)
                .where("runId", "==", run_id)
                .limit(limit)
            )
            snapshots = list(query.stream())
        except (FirebaseError, GoogleAPICallError, RetryError) as exc:
            raise FirestoreServiceError("Failed to list chat messages by run id.") from exc

        items: list[tuple[str, dict[str, Any]]] = []
        for snapshot in snapshots:
            items.append((snapshot.id, dict(snapshot.to_dict() or {})))
        return items
