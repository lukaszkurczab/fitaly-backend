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

DEFAULT_MEMORY_DOC_ID = "current"


class MemorySummaryRepository:
    def __init__(self, firestore_client: firestore.Client | None = None) -> None:
        self._db = firestore_client or get_firestore()

    def _summary_ref(
        self, *, user_id: str, thread_id: str, doc_id: str = DEFAULT_MEMORY_DOC_ID
    ) -> firestore.DocumentReference:
        return (
            self._db.collection(USERS_COLLECTION)
            .document(user_id)
            .collection(CHAT_THREADS_SUBCOLLECTION)
            .document(thread_id)
            .collection(MEMORY_SUBCOLLECTION)
            .document(doc_id)
        )

    async def get(
        self, *, user_id: str, thread_id: str, doc_id: str = DEFAULT_MEMORY_DOC_ID
    ) -> dict[str, Any] | None:
        try:
            snapshot = self._summary_ref(
                user_id=user_id, thread_id=thread_id, doc_id=doc_id
            ).get()
        except (FirebaseError, GoogleAPICallError, RetryError) as exc:
            raise FirestoreServiceError("Failed to read memory summary.") from exc
        if not snapshot.exists:
            return None
        return dict(snapshot.to_dict() or {})

    async def upsert(
        self,
        *,
        user_id: str,
        thread_id: str,
        payload: dict[str, Any],
        doc_id: str = DEFAULT_MEMORY_DOC_ID,
    ) -> None:
        try:
            self._summary_ref(user_id=user_id, thread_id=thread_id, doc_id=doc_id).set(
                payload, merge=True
            )
        except (FirebaseError, GoogleAPICallError, RetryError) as exc:
            raise FirestoreServiceError("Failed to upsert memory summary.") from exc
