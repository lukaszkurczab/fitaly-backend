from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.api.http_errors import raise_bad_request, raise_service_unavailable
from app.core.exceptions import FirestoreServiceError
from app.schemas.known_patterns import (
    KnownPatternCandidateControl,
    KnownPatternCandidateControlRequest,
    KnownPatternCandidateControlResponse,
    KnownPatternCandidatesResponse,
    KnownPatternReviewDraftRequest,
    KnownPatternReviewDraftResponse,
)
from app.services.known_pattern_service import (
    KnownPatternMutationDedupeConflictError,
    KnownPatternNotFoundError,
    list_known_pattern_candidates_for_user,
    mark_known_pattern_candidate_control_for_user,
    open_known_pattern_review_draft_for_user,
)

router = APIRouter(prefix="/users/me/known-patterns", tags=["Known Patterns V2"])


def _raise_not_found(exc: Exception) -> NoReturn:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def _raise_conflict(exc: Exception) -> NoReturn:
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


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


@router.post(
    "/candidates/{candidate_id}/control",
    response_model=KnownPatternCandidateControlResponse,
)
async def control_known_pattern_candidate_me(
    candidate_id: str,
    request: KnownPatternCandidateControlRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> KnownPatternCandidateControlResponse:
    try:
        result = await mark_known_pattern_candidate_control_for_user(
            current_user.uid,
            candidate_id,
            request,
        )
    except KnownPatternNotFoundError as exc:
        _raise_not_found(exc)
    except KnownPatternMutationDedupeConflictError as exc:
        _raise_conflict(exc)
    except ValueError as exc:
        raise_bad_request(exc)
    except FirestoreServiceError as exc:
        raise_service_unavailable(
            exc,
            detail="Known pattern controls are temporarily unavailable",
        )
    return KnownPatternCandidateControlResponse(
        control=KnownPatternCandidateControl.model_validate(result["document"]),
        updated=result["applied"],
    )


@router.post(
    "/candidates/{candidate_id}/review-draft",
    response_model=KnownPatternReviewDraftResponse,
)
async def open_known_pattern_review_draft_me(
    candidate_id: str,
    request: KnownPatternReviewDraftRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> KnownPatternReviewDraftResponse:
    try:
        result = await open_known_pattern_review_draft_for_user(
            current_user.uid,
            candidate_id,
            request,
        )
    except KnownPatternNotFoundError as exc:
        _raise_not_found(exc)
    except KnownPatternMutationDedupeConflictError as exc:
        _raise_conflict(exc)
    except ValueError as exc:
        raise_bad_request(exc)
    except FirestoreServiceError as exc:
        raise_service_unavailable(
            exc,
            detail="Known pattern review drafts are temporarily unavailable",
        )
    return KnownPatternReviewDraftResponse(
        draft=result["draft"],
        control=KnownPatternCandidateControl.model_validate(result["control"]),
        updated=result["applied"],
    )
