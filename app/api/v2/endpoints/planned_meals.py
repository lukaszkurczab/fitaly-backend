from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.api.http_errors import raise_bad_request, raise_service_unavailable
from app.core.exceptions import FirestoreServiceError
from app.schemas.planned_meals import (
    PlannedMealCreateRequest,
    PlannedMealDeleteRequest,
    PlannedMealMutationResponse,
    PlannedMealsListResponse,
    PlannedMealUpdateRequest,
)
from app.services.planned_meal_service import (
    PlannedMealMutationDedupeConflictError,
    PlannedMealNotFoundError,
    PlannedMealVersionConflictError,
    create_planned_meal_for_user,
    delete_planned_meal_for_user,
    list_planned_meals_for_user,
    response_from_result,
    update_planned_meal_for_user,
)

router = APIRouter(prefix="/users/me/planned-meals", tags=["Planned Meals V2"])


def _raise_not_found(exc: Exception) -> NoReturn:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def _raise_conflict(exc: Exception) -> NoReturn:
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("", response_model=PlannedMealsListResponse)
async def list_planned_meals_me(
    startDate: str | None = Query(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    ),
    days: int = Query(default=3, ge=1, le=3),
    includeDeleted: bool = Query(default=False),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> PlannedMealsListResponse:
    try:
        return await list_planned_meals_for_user(
            current_user.uid,
            start_date=startDate,
            days=days,
            include_deleted=includeDeleted,
        )
    except ValueError as exc:
        raise_bad_request(exc)
    except FirestoreServiceError as exc:
        raise_service_unavailable(
            exc,
            detail="Planned meals are temporarily unavailable",
        )


@router.post(
    "",
    response_model=PlannedMealMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_planned_meal_me(
    request: PlannedMealCreateRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> PlannedMealMutationResponse:
    try:
        result = await create_planned_meal_for_user(current_user.uid, request)
    except PlannedMealMutationDedupeConflictError as exc:
        _raise_conflict(exc)
    except PlannedMealVersionConflictError as exc:
        _raise_conflict(exc)
    except ValueError as exc:
        raise_bad_request(exc)
    except FirestoreServiceError as exc:
        raise_service_unavailable(
            exc,
            detail="Planned meal could not be created",
        )
    return response_from_result(result)


@router.patch("/{planned_meal_id}", response_model=PlannedMealMutationResponse)
async def update_planned_meal_me(
    planned_meal_id: str,
    request: PlannedMealUpdateRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> PlannedMealMutationResponse:
    try:
        result = await update_planned_meal_for_user(
            current_user.uid,
            planned_meal_id,
            request,
        )
    except PlannedMealNotFoundError as exc:
        _raise_not_found(exc)
    except PlannedMealMutationDedupeConflictError as exc:
        _raise_conflict(exc)
    except PlannedMealVersionConflictError as exc:
        _raise_conflict(exc)
    except ValueError as exc:
        raise_bad_request(exc)
    except FirestoreServiceError as exc:
        raise_service_unavailable(
            exc,
            detail="Planned meal could not be updated",
        )
    return response_from_result(result)


@router.delete("/{planned_meal_id}", response_model=PlannedMealMutationResponse)
async def delete_planned_meal_me(
    planned_meal_id: str,
    clientMutationId: str = Query(min_length=1, max_length=128),
    expectedVersion: int = Query(ge=1),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> PlannedMealMutationResponse:
    try:
        result = await delete_planned_meal_for_user(
            current_user.uid,
            planned_meal_id,
            PlannedMealDeleteRequest(
                clientMutationId=clientMutationId,
                expectedVersion=expectedVersion,
            ),
        )
    except PlannedMealNotFoundError as exc:
        _raise_not_found(exc)
    except PlannedMealMutationDedupeConflictError as exc:
        _raise_conflict(exc)
    except PlannedMealVersionConflictError as exc:
        _raise_conflict(exc)
    except ValueError as exc:
        raise_bad_request(exc)
    except FirestoreServiceError as exc:
        raise_service_unavailable(
            exc,
            detail="Planned meal could not be deleted",
        )
    return response_from_result(result)
