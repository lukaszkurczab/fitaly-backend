from typing import Any, NoReturn

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import ValidationError

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.api.http_errors import raise_bad_request
from app.schemas.meal import (
    MealChangesPageResponse,
    MealDeleteRequest,
    MealDeleteResponse,
    MealItem,
    MealPhotoUploadResponse,
    MealsHistoryPageResponse,
    MealUpsertRequest,
    MealUpsertResponse,
    validate_day_key_format,
)
from app.services import meal_service

router = APIRouter()


def _to_range(min_value: float | None, max_value: float | None) -> tuple[float, float] | None:
    if min_value is None and max_value is None:
        return None
    if min_value is None or max_value is None:
        raise_bad_request(ValueError("Both range values are required"))
    if min_value > max_value:
        raise_bad_request(ValueError("Invalid range"))
    return min_value, max_value


def _validate_day_key_range(
    start: str | None,
    end: str | None,
) -> tuple[str | None, str | None]:
    normalized_start = validate_day_key_format(start) if start is not None else None
    normalized_end = validate_day_key_format(end) if end is not None else None
    if normalized_start is not None and normalized_end is not None and normalized_start > normalized_end:
        raise ValueError("Invalid dayKey range")
    return normalized_start, normalized_end


def _raise_meal_upsert_validation_error(exc: ValidationError) -> NoReturn:
    for error in exc.errors(include_context=False):
        if tuple(error.get("loc", ())) == ("dayKey",):
            raise_bad_request(ValueError("dayKey must use YYYY-MM-DD format"))
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=exc.errors(include_context=False),
    )


@router.get("/users/me/meals/history", response_model=MealsHistoryPageResponse)
async def get_meals_history_me(
    limit: int = Query(default=20, ge=1, le=100),
    beforeCursor: str | None = Query(default=None),
    caloriesMin: float | None = Query(default=None, ge=0),
    caloriesMax: float | None = Query(default=None, ge=0),
    proteinMin: float | None = Query(default=None, ge=0),
    proteinMax: float | None = Query(default=None, ge=0),
    carbsMin: float | None = Query(default=None, ge=0),
    carbsMax: float | None = Query(default=None, ge=0),
    fatMin: float | None = Query(default=None, ge=0),
    fatMax: float | None = Query(default=None, ge=0),
    dayKeyStart: str | None = Query(default=None),
    dayKeyEnd: str | None = Query(default=None),
    # Legacy query params kept for backwards-compatible request parsing only.
    # Canonical history range filtering is dayKey-based.
    loggedAtStart: str | None = Query(default=None),
    loggedAtEnd: str | None = Query(default=None),
    timestampStart: str | None = Query(default=None),
    timestampEnd: str | None = Query(default=None),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealsHistoryPageResponse:
    try:
        day_key_start, day_key_end = _validate_day_key_range(dayKeyStart, dayKeyEnd)
        items, next_cursor = await meal_service.list_history(
            current_user.uid,
            limit_count=limit,
            before_cursor=beforeCursor,
            calories=_to_range(caloriesMin, caloriesMax),
            protein=_to_range(proteinMin, proteinMax),
            carbs=_to_range(carbsMin, carbsMax),
            fat=_to_range(fatMin, fatMax),
            day_key_start=day_key_start,
            day_key_end=day_key_end,
        )
        del loggedAtStart, loggedAtEnd, timestampStart, timestampEnd
    except ValueError as exc:
        raise_bad_request(exc)

    return MealsHistoryPageResponse(
        items=[MealItem.model_validate(item) for item in items],
        nextCursor=next_cursor,
    )


@router.get("/users/me/meals/photo-url", response_model=MealPhotoUploadResponse)
async def get_meal_photo_url_me(
    mealId: str | None = Query(default=None),
    imageId: str | None = Query(default=None),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealPhotoUploadResponse:
    try:
        payload = await meal_service.resolve_photo(
            current_user.uid,
            meal_id=mealId,
            image_id=imageId,
        )
    except ValueError as exc:
        raise_bad_request(exc)

    return MealPhotoUploadResponse(**payload)


@router.get("/users/me/meals/changes", response_model=MealChangesPageResponse)
async def get_meal_changes_me(
    limit: int = Query(default=100, ge=1, le=250),
    afterCursor: str | None = Query(default=None),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealChangesPageResponse:
    try:
        items, next_cursor = await meal_service.list_changes(
            current_user.uid,
            limit_count=limit,
            after_cursor=afterCursor,
        )
    except ValueError as exc:
        raise_bad_request(exc)

    return MealChangesPageResponse(
        items=[MealItem.model_validate(item) for item in items],
        nextCursor=next_cursor,
    )


@router.post("/users/me/meals/photo", response_model=MealPhotoUploadResponse)
async def upload_meal_photo_me(
    file: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealPhotoUploadResponse:
    payload = await meal_service.upload_photo(current_user.uid, file)

    return MealPhotoUploadResponse(**payload)


@router.post("/users/me/meals", response_model=MealUpsertResponse)
async def upsert_meal_me(
    request: dict[str, Any] = Body(...),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealUpsertResponse:
    try:
        parsed_request = MealUpsertRequest.model_validate(request)
        meal = await meal_service.upsert_meal(current_user.uid, parsed_request.model_dump())
    except ValidationError as exc:
        _raise_meal_upsert_validation_error(exc)
    except ValueError as exc:
        raise_bad_request(exc)

    return MealUpsertResponse(meal=MealItem.model_validate(meal), updated=True)


@router.post("/users/me/meals/{mealId}/delete", response_model=MealDeleteResponse)
async def delete_meal_me(
    mealId: str,
    request: MealDeleteRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealDeleteResponse:
    try:
        meal = await meal_service.mark_deleted(
            current_user.uid,
            mealId,
            updated_at=request.updatedAt,
        )
    except ValueError as exc:
        raise_bad_request(exc)

    return MealDeleteResponse(
        mealId=meal["id"],
        updatedAt=meal["updatedAt"],
        deleted=True,
    )
