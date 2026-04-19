from typing import Any

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import CHAT_THREADS_SUBCOLLECTION, USERS_COLLECTION
from app.db.firebase import get_firestore


class ChatThreadRepository:
    def __init__(self, firestore_client: firestore.Client | None = None) -> None:
        self._db = firestore_client or get_firestore()

    def _threads_collection(self, *, user_id: str) -> firestore.CollectionReference:
        return (
            self._db.collection(USERS_COLLECTION)
            .document(user_id)
            .collection(CHAT_THREADS_SUBCOLLECTION)
        )

    def _thread_ref(
        self, *, user_id: str, thread_id: str
    ) -> firestore.DocumentReference:
        return self._threads_collection(user_id=user_id).document(thread_id)

    async def get(self, *, user_id: str, thread_id: str) -> dict[str, Any] | None:
        try:
            snapshot = self._thread_ref(user_id=user_id, thread_id=thread_id).get()
        except (FirebaseError, GoogleAPICallError, RetryError) as exc:
            raise FirestoreServiceError("Failed to read chat thread.") from exc

        if not snapshot.exists:
            return None
        return dict(snapshot.to_dict() or {})

    async def upsert(
        self,
        *,
        user_id: str,
        thread_id: str,
        payload: dict[str, Any],
        merge: bool = True,
    ) -> None:
        try:
            self._thread_ref(user_id=user_id, thread_id=thread_id).set(payload, merge=merge)
        except (FirebaseError, GoogleAPICallError, RetryError) as exc:
            raise FirestoreServiceError("Failed to upsert chat thread.") from exc

    async def list_recent(
        self,
        *,
        user_id: str,
        limit: int,
        before_updated_at: int | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        try:
            query = self._threads_collection(user_id=user_id).order_by(
                "updatedAt", direction=firestore.Query.DESCENDING
            )
            if before_updated_at is not None:
                query = query.where("updatedAt", "<", before_updated_at)
            snapshots = list(query.limit(limit).stream())
        except (FirebaseError, GoogleAPICallError, RetryError) as exc:
            raise FirestoreServiceError("Failed to list chat threads.") from exc

        items: list[tuple[str, dict[str, Any]]] = []
        for snapshot in snapshots:
            items.append((snapshot.id, dict(snapshot.to_dict() or {})))
        return items
