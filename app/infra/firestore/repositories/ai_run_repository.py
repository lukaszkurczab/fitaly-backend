from typing import Any

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import AI_RUNS_COLLECTION
from app.db.firebase import get_firestore


class AiRunRepository:
    def __init__(self, firestore_client: firestore.Client | None = None) -> None:
        self._db = firestore_client or get_firestore()

    def _run_ref(self, *, run_id: str) -> firestore.DocumentReference:
        return self._db.collection(AI_RUNS_COLLECTION).document(run_id)

    async def get(self, *, run_id: str) -> dict[str, Any] | None:
        try:
            snapshot = self._run_ref(run_id=run_id).get()
        except (FirebaseError, GoogleAPICallError, RetryError) as exc:
            raise FirestoreServiceError("Failed to read ai run.") from exc
        if not snapshot.exists:
            return None
        return dict(snapshot.to_dict() or {})

    async def upsert(self, *, run_id: str, payload: dict[str, Any]) -> None:
        try:
            self._run_ref(run_id=run_id).set(payload, merge=True)
        except (FirebaseError, GoogleAPICallError, RetryError) as exc:
            raise FirestoreServiceError("Failed to upsert ai run.") from exc

    async def list_recent_for_user(
        self, *, user_id: str, limit: int
    ) -> list[tuple[str, dict[str, Any]]]:
        try:
            query = (
                self._db.collection(AI_RUNS_COLLECTION)
                .where("userId", "==", user_id)
                .order_by("createdAt", direction=firestore.Query.DESCENDING)
                .limit(limit)
            )
            snapshots = list(query.stream())
        except (FirebaseError, GoogleAPICallError, RetryError) as exc:
            raise FirestoreServiceError("Failed to list ai runs.") from exc

        items: list[tuple[str, dict[str, Any]]] = []
        for snapshot in snapshots:
            items.append((snapshot.id, dict(snapshot.to_dict() or {})))
        return items
