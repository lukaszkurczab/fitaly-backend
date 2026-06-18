from fastapi import APIRouter, Depends, Query

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.api.http_errors import raise_service_unavailable
from app.core.exceptions import FirestoreServiceError
from app.schemas.known_patterns import KnownPatternCandidatesResponse
from app.services.known_pattern_service import list_known_pattern_candidates_for_user

router = APIRouter(prefix="/users/me/known-patterns", tags=["Known Patterns V2"])


@router.get("/candidates", response_model=KnownPatternCandidatesResponse)
async def list_known_pattern_candidates_me(
    limit: int = Query(default=5, ge=1, le=10),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> KnownPatternCandidatesResponse:
    try:
        return await list_known_pattern_candidates_for_user(
            current_user.uid,
            limit=limit,
        )
    except FirestoreServiceError as exc:
        raise_service_unavailable(
            exc,
            detail="Known pattern candidates are temporarily unavailable",
        )
