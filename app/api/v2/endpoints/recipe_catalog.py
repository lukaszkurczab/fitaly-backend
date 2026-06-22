from collections.abc import Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.api.feature_flags import raise_feature_disabled
from app.api.http_errors import raise_bad_request, raise_service_unavailable
from app.core.config import settings
from app.core.exceptions import FirestoreServiceError
from app.domain.users.models.user_profile import UserProfile
from app.domain.users.services.user_profile_service import UserProfileService
from app.schemas.recipes import (
    RecipeCatalogFilterRequest,
    RecipeCatalogFilterResponse,
    RecipeCatalogRecord,
)
from app.schemas.user_account import AllergyValue, ChronicDiseaseValue, PreferenceValue
from app.services.recipe_catalog_content_validator import (
    RecipeCatalogContentValidationError,
    load_recipe_catalog_content,
)
from app.services.recipe_catalog_service import evaluate_recipe_catalog

router = APIRouter(prefix="/users/me/recipes", tags=["Recipes V2"])


def _ensure_recipe_catalog_enabled() -> None:
    if not settings.RECIPE_CATALOG_ENABLED:
        raise_feature_disabled(
            code="recipe_catalog_disabled",
            message="Recipe Catalog is temporarily disabled.",
        )


def _ensure_recipe_catalog_content_approved() -> None:
    if not settings.RECIPE_CATALOG_CONTENT_APPROVED:
        raise_feature_disabled(
            code="recipe_catalog_content_not_approved",
            message="Recipe Catalog content is temporarily unavailable.",
        )


def _load_recipe_catalog_records() -> tuple[RecipeCatalogRecord, ...]:
    try:
        return load_recipe_catalog_content(settings.RECIPE_CATALOG_CONTENT_PATH)
    except RecipeCatalogContentValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "recipe_catalog_content_invalid",
                "message": "Recipe Catalog content pack is invalid.",
                "issueCodes": exc.report.summary.issueCodes,
            },
        ) from exc


def _profile_allergies(profile: UserProfile | None) -> list[str]:
    return list(profile.allergies) if profile is not None else []


def _profile_preferences(profile: UserProfile | None) -> list[str]:
    return list(profile.preferences) if profile is not None else []


def _resolve_profile_filter(
    *,
    query_values: Sequence[str] | None,
    profile_values: Sequence[str],
    use_profile: bool,
) -> list[str]:
    if query_values is not None:
        return list(query_values)
    if use_profile:
        return list(profile_values)
    return []


@router.get("/catalog", response_model=RecipeCatalogFilterResponse)
async def list_recipe_catalog_me(
    allergies: list[AllergyValue] | None = Query(default=None),
    preferences: list[PreferenceValue] | None = Query(default=None),
    chronicDiseases: list[ChronicDiseaseValue] | None = Query(default=None),
    allergiesOther: str | None = Query(default=None, max_length=120),
    lifestyle: str | None = Query(default=None, max_length=160),
    useProfileAllergies: bool = Query(default=True),
    useProfilePreferences: bool = Query(default=True),
    showHidden: bool = Query(default=False),
    revealUnknown: bool = Query(default=False),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> RecipeCatalogFilterResponse:
    _ensure_recipe_catalog_enabled()
    _ensure_recipe_catalog_content_approved()
    catalog = _load_recipe_catalog_records()
    try:
        profile = await UserProfileService().get_profile(user_id=current_user.uid)
    except FirestoreServiceError as exc:
        raise_service_unavailable(
            exc,
            detail="Recipe catalog profile filters are temporarily unavailable",
        )

    try:
        request = RecipeCatalogFilterRequest.model_validate(
            {
                "allergies": _resolve_profile_filter(
                    query_values=allergies,
                    profile_values=_profile_allergies(profile),
                    use_profile=useProfileAllergies,
                ),
                "preferences": _resolve_profile_filter(
                    query_values=preferences,
                    profile_values=_profile_preferences(profile),
                    use_profile=useProfilePreferences,
                ),
                "chronicDiseases": list(chronicDiseases or []),
                "allergiesOther": allergiesOther,
                "lifestyle": lifestyle,
                "showHidden": showHidden,
                "revealUnknown": revealUnknown,
            }
        )
    except ValidationError as exc:
        raise_bad_request(exc)

    return evaluate_recipe_catalog(request, catalog=catalog)
