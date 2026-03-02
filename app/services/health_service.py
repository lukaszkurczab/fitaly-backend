from datetime import datetime, timezone
import logging

from app.core.config import settings
from app.db.firebase import get_firestore
from app.schemas.health import FirestoreHealthResponse, HealthResponse

logger = logging.getLogger(__name__)


class FirestoreHealthcheckError(Exception):
    """Raised when the Firestore healthcheck cannot complete successfully."""


def build_health_response() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="fitaly-backend",
        timestamp=datetime.now(timezone.utc),
    )


def check_firestore_health() -> FirestoreHealthResponse:
    """Verify Firestore connectivity for infrastructure health monitoring."""
    try:
        client = get_firestore()
        list(client.collection("_healthcheck").limit(1).stream())
    except Exception as exc:
        logger.exception("Firestore healthcheck failed.")
        raise FirestoreHealthcheckError("Firestore healthcheck failed.") from exc

    return FirestoreHealthResponse(
        status="ok",
        service="fitaly-backend",
        database="firestore",
        project_id=settings.FIREBASE_PROJECT_ID,
        timestamp=datetime.now(timezone.utc),
    )
