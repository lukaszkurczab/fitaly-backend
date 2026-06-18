from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.api.http_errors import raise_bad_request, raise_service_unavailable
from app.core.exceptions import FirestoreServiceError
from app.domain.users.models.user_profile import UserProfile
from app.domain.users.services.user_profile_service import UserProfileService
from app.schemas.recipes import RecipeCatalogFilterRequest, RecipeCatalogFilterResponse
from app.schemas.user_account import AllergyValue, ChronicDiseaseValue, PreferenceValue
from app.services.recipe_catalog_service import evaluate_recipe_catalog

router = APIRouter(prefix="/users/me/recipes", tags=["Recipes V2"])


def _profile_allergies(profile: UserProfile | None) -> list[str]:
    return list(profile.allergies) if profile is not None else []


def _profile_preferences(profile: UserProfile | None) -> list[str]:
    return list(profile.preferences) if profile is not None else []


@router.get("/catalog", response_model=RecipeCatalogFilterResponse)
async def list_recipe_catalog_me(
    allergies: list[AllergyValue] | None = Query(default=None),
    preferences: list[PreferenceValue] | None = Query(default=None),
    chronicDiseases: list[ChronicDiseaseValue] | None = Query(default=None),
    allergiesOther: str | None = Query(default=None, max_length=120),
    lifestyle: str | None = Query(default=None, max_length=160),
    showHidden: bool = Query(default=False),
    revealUnknown: bool = Query(default=False),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> RecipeCatalogFilterResponse:
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
                "allergies": (
                    list(allergies) if allergies is not None else _profile_allergies(profile)
                ),
                "preferences": (
                    list(preferences)
                    if preferences is not None
                    else _profile_preferences(profile)
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

    return evaluate_recipe_catalog(request)
