from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.api.http_errors import raise_bad_request
from app.schemas.meal import (
    MealTemplate,
    MealTemplateChangesPageResponse,
    MealTemplateDeleteResponse,
    MealTemplatePhotoUploadResponse,
    MealTemplateUpsertResponse,
    SavedMealDeleteRequest,
    SavedMealUpsertRequest,
)
from app.services import my_meal_service
from app.services.meal_service import MealMutationDedupeConflictError

router = APIRouter()


@router.get(
    "/users/me/meal-templates/changes",
    response_model=MealTemplateChangesPageResponse,
)
async def get_meal_template_changes_me(
    limit: int = Query(default=100, ge=1, le=250),
    afterCursor: str | None = Query(default=None),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealTemplateChangesPageResponse:
    try:
        items, next_cursor = await my_meal_service.list_changes(
            current_user.uid,
            limit_count=limit,
            after_cursor=afterCursor,
        )
    except ValueError as exc:
        raise_bad_request(exc)

    return MealTemplateChangesPageResponse(
        items=[MealTemplate.model_validate(item) for item in items],
        nextCursor=next_cursor,
    )


@router.post("/users/me/meal-templates", response_model=MealTemplateUpsertResponse)
async def upsert_meal_template_me(
    request: SavedMealUpsertRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealTemplateUpsertResponse:
    try:
        template = await my_meal_service.upsert_saved_meal(
            current_user.uid,
            request.model_dump(),
        )
    except MealMutationDedupeConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise_bad_request(exc)

    return MealTemplateUpsertResponse(
        template=MealTemplate.model_validate(template),
        updated=True,
    )


@router.post(
    "/users/me/meal-templates/{templateId}/delete",
    response_model=MealTemplateDeleteResponse,
)
async def delete_meal_template_me(
    templateId: str,
    request: SavedMealDeleteRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealTemplateDeleteResponse:
    try:
        template = await my_meal_service.mark_deleted(
            current_user.uid,
            templateId,
            updated_at=request.updatedAt,
            client_mutation_id=request.clientMutationId,
        )
    except MealMutationDedupeConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise_bad_request(exc)

    return MealTemplateDeleteResponse(
        templateId=template["templateId"],
        updatedAt=template["updatedAt"],
        deleted=bool(template["deleted"]),
    )


@router.post(
    "/users/me/meal-templates/{templateId}/photo",
    response_model=MealTemplatePhotoUploadResponse,
)
async def upload_meal_template_photo_me(
    templateId: str,
    file: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> MealTemplatePhotoUploadResponse:
    payload = await my_meal_service.upload_photo(
        current_user.uid,
        templateId,
        file,
    )

    return MealTemplatePhotoUploadResponse(**payload)
