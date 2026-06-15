from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.api.http_errors import raise_bad_request
from app.schemas.smart_memory import (
    SmartMemoryCandidate,
    SmartMemoryCandidateResponse,
    SmartMemoryCandidatesPageResponse,
    SmartMemoryCandidateUpsertRequest,
    SmartMemoryItem,
    SmartMemoryItemMutationResponse,
    SmartMemoryItemPatchRequest,
    SmartMemoryItemResponse,
    SmartMemoryItemsPageResponse,
    SmartMemoryMutationRequest,
    SmartMemorySettings,
    SmartMemorySettingsResponse,
    SmartMemorySettingsUpdateRequest,
    SmartMemorySourceDeletedRequest,
)
from app.services import smart_memory_service
from app.services.smart_memory_service import (
    SmartMemoryMutationDedupeConflictError,
    SmartMemoryNotFoundError,
)

router = APIRouter()


def _raise_not_found(exc: Exception) -> NoReturn:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def _raise_conflict(exc: Exception) -> NoReturn:
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get(
    "/users/me/smart-memory/items",
    response_model=SmartMemoryItemsPageResponse,
)
async def list_smart_memory_items_me(
    limit: int = Query(default=100, ge=1, le=250),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> SmartMemoryItemsPageResponse:
    items = await smart_memory_service.list_items(current_user.uid, limit_count=limit)
    return SmartMemoryItemsPageResponse(
        items=[SmartMemoryItem.model_validate(item) for item in items]
    )


@router.get(
    "/users/me/smart-memory/items/{memoryItemId}",
    response_model=SmartMemoryItemResponse,
)
async def get_smart_memory_item_me(
    memoryItemId: str,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> SmartMemoryItemResponse:
    try:
        item = await smart_memory_service.get_item(current_user.uid, memoryItemId)
    except SmartMemoryNotFoundError as exc:
        _raise_not_found(exc)
    except ValueError as exc:
        raise_bad_request(exc)
    return SmartMemoryItemResponse(item=SmartMemoryItem.model_validate(item))


@router.patch(
    "/users/me/smart-memory/items/{memoryItemId}",
    response_model=SmartMemoryItemMutationResponse,
)
async def patch_smart_memory_item_me(
    memoryItemId: str,
    request: SmartMemoryItemPatchRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> SmartMemoryItemMutationResponse:
    try:
        result = await smart_memory_service.patch_item(
            current_user.uid,
            memoryItemId,
            request,
        )
    except SmartMemoryNotFoundError as exc:
        _raise_not_found(exc)
    except SmartMemoryMutationDedupeConflictError as exc:
        _raise_conflict(exc)
    except ValueError as exc:
        raise_bad_request(exc)
    return SmartMemoryItemMutationResponse(
        item=SmartMemoryItem.model_validate(result["document"]),
        updated=result["applied"],
    )


@router.post(
    "/users/me/smart-memory/items/{memoryItemId}/mute",
    response_model=SmartMemoryItemMutationResponse,
)
async def mute_smart_memory_item_me(
    memoryItemId: str,
    request: SmartMemoryMutationRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> SmartMemoryItemMutationResponse:
    try:
        result = await smart_memory_service.mute_item(
            current_user.uid,
            memoryItemId,
            client_mutation_id=request.clientMutationId,
        )
    except SmartMemoryNotFoundError as exc:
        _raise_not_found(exc)
    except SmartMemoryMutationDedupeConflictError as exc:
        _raise_conflict(exc)
    except ValueError as exc:
        raise_bad_request(exc)
    return SmartMemoryItemMutationResponse(
        item=SmartMemoryItem.model_validate(result["document"]),
        updated=result["applied"],
    )


@router.post(
    "/users/me/smart-memory/items/{memoryItemId}/restore",
    response_model=SmartMemoryItemMutationResponse,
)
async def restore_smart_memory_item_me(
    memoryItemId: str,
    request: SmartMemoryMutationRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> SmartMemoryItemMutationResponse:
    try:
        result = await smart_memory_service.restore_item(
            current_user.uid,
            memoryItemId,
            client_mutation_id=request.clientMutationId,
        )
    except SmartMemoryNotFoundError as exc:
        _raise_not_found(exc)
    except SmartMemoryMutationDedupeConflictError as exc:
        _raise_conflict(exc)
    except ValueError as exc:
        raise_bad_request(exc)
    return SmartMemoryItemMutationResponse(
        item=SmartMemoryItem.model_validate(result["document"]),
        updated=result["applied"],
    )


@router.post(
    "/users/me/smart-memory/items/{memoryItemId}/delete",
    response_model=SmartMemoryItemMutationResponse,
)
async def delete_smart_memory_item_me(
    memoryItemId: str,
    request: SmartMemoryMutationRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> SmartMemoryItemMutationResponse:
    try:
        result = await smart_memory_service.delete_item(
            current_user.uid,
            memoryItemId,
            client_mutation_id=request.clientMutationId,
        )
    except SmartMemoryNotFoundError as exc:
        _raise_not_found(exc)
    except SmartMemoryMutationDedupeConflictError as exc:
        _raise_conflict(exc)
    except ValueError as exc:
        raise_bad_request(exc)
    return SmartMemoryItemMutationResponse(
        item=SmartMemoryItem.model_validate(result["document"]),
        updated=result["applied"],
    )


@router.post(
    "/users/me/smart-memory/items/{memoryItemId}/source-deleted",
    response_model=SmartMemoryItemMutationResponse,
)
async def mark_smart_memory_item_source_deleted_me(
    memoryItemId: str,
    request: SmartMemorySourceDeletedRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> SmartMemoryItemMutationResponse:
    try:
        result = await smart_memory_service.mark_source_deleted(
            current_user.uid,
            memoryItemId,
            client_mutation_id=request.clientMutationId,
            source_ref=request.sourceRef,
        )
    except SmartMemoryNotFoundError as exc:
        _raise_not_found(exc)
    except SmartMemoryMutationDedupeConflictError as exc:
        _raise_conflict(exc)
    except ValueError as exc:
        raise_bad_request(exc)
    return SmartMemoryItemMutationResponse(
        item=SmartMemoryItem.model_validate(result["document"]),
        updated=result["applied"],
    )


@router.get(
    "/users/me/smart-memory/candidates",
    response_model=SmartMemoryCandidatesPageResponse,
)
async def list_smart_memory_candidates_me(
    limit: int = Query(default=100, ge=1, le=250),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> SmartMemoryCandidatesPageResponse:
    candidates = await smart_memory_service.list_candidates(
        current_user.uid,
        limit_count=limit,
    )
    return SmartMemoryCandidatesPageResponse(
        items=[SmartMemoryCandidate.model_validate(candidate) for candidate in candidates]
    )


@router.get(
    "/users/me/smart-memory/candidates/{candidateId}",
    response_model=SmartMemoryCandidateResponse,
)
async def get_smart_memory_candidate_me(
    candidateId: str,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> SmartMemoryCandidateResponse:
    try:
        candidate = await smart_memory_service.get_candidate(current_user.uid, candidateId)
    except SmartMemoryNotFoundError as exc:
        _raise_not_found(exc)
    except ValueError as exc:
        raise_bad_request(exc)
    return SmartMemoryCandidateResponse(
        candidate=SmartMemoryCandidate.model_validate(candidate),
        updated=False,
    )


@router.post(
    "/users/me/smart-memory/candidates",
    response_model=SmartMemoryCandidateResponse,
)
async def upsert_smart_memory_candidate_me(
    request: SmartMemoryCandidateUpsertRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> SmartMemoryCandidateResponse:
    try:
        result = await smart_memory_service.upsert_candidate(current_user.uid, request)
    except SmartMemoryMutationDedupeConflictError as exc:
        _raise_conflict(exc)
    except ValueError as exc:
        raise_bad_request(exc)
    return SmartMemoryCandidateResponse(
        candidate=SmartMemoryCandidate.model_validate(result["document"]),
        updated=result["applied"],
    )


@router.get(
    "/users/me/smart-memory/settings",
    response_model=SmartMemorySettingsResponse,
)
async def get_smart_memory_settings_me(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> SmartMemorySettingsResponse:
    settings = await smart_memory_service.get_settings(current_user.uid)
    return SmartMemorySettingsResponse(
        settings=SmartMemorySettings.model_validate(settings),
        updated=False,
    )


@router.patch(
    "/users/me/smart-memory/settings",
    response_model=SmartMemorySettingsResponse,
)
async def update_smart_memory_settings_me(
    request: SmartMemorySettingsUpdateRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> SmartMemorySettingsResponse:
    try:
        result = await smart_memory_service.update_settings(current_user.uid, request)
    except SmartMemoryMutationDedupeConflictError as exc:
        _raise_conflict(exc)
    except ValueError as exc:
        raise_bad_request(exc)
    return SmartMemorySettingsResponse(
        settings=SmartMemorySettings.model_validate(result["document"]),
        updated=result["applied"],
    )
