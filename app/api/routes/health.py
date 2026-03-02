from fastapi import APIRouter, HTTPException, status

from app.schemas.health import FirestoreHealthResponse, HealthResponse
from app.services.health_service import (
    FirestoreHealthcheckError,
    build_health_response,
    check_firestore_health,
)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    return build_health_response()


@router.get("/health/firestore", response_model=FirestoreHealthResponse)
def firestore_health_check() -> FirestoreHealthResponse:
    try:
        return check_firestore_health()
    except FirestoreHealthcheckError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firestore healthcheck failed.",
        ) from exc
