"""Legacy v1 AI run telemetry writer used by `/api/v1/ai/*` routes.

Canonical v2 run persistence is implemented in
`app/domain/ai_runs/services/ai_run_service.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import AI_RUNS_COLLECTION
from app.db.firebase import get_firestore

logger = logging.getLogger(__name__)


async def log_ai_run(run_id: str, payload: dict[str, Any]) -> None:
    client: firestore.Client = get_firestore()
    doc = {
        **payload,
        "runId": run_id,
        "loggedAt": datetime.now(timezone.utc),
    }

    try:
        client.collection(AI_RUNS_COLLECTION).document(run_id).set(doc, merge=True)
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to persist ai run telemetry.",
            extra={"run_id": run_id},
        )
        raise FirestoreServiceError("Failed to persist ai run telemetry.") from exc
