from collections.abc import Sequence
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest
from pytest_mock import MockerFixture

from app.core.config import Settings
from app.core.exceptions import FirestoreServiceError
from app.domain.users.models.user_profile import UserProfile
from app.main import app
from app.schemas.recipes import RecipeCatalogFilterRequest, RecipeCatalogRecord
from app.services.recipe_catalog_service import evaluate_recipe_catalog
from tests.types import AuthHeaders

client = TestClient(app)


@pytest.fixture(autouse=True)
def enable_recipe_catalog_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.api.v2.endpoints.recipe_catalog.settings.RECIPE_CATALOG_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "app.api.v2.endpoints.recipe_catalog.settings.RECIPE_CATALOG_CONTENT_APPROVED",
        True,
    )
    monkeypatch.setattr(
        "app.api.v2.endpoints.recipe_catalog.settings.RECIPE_CATALOG_CONTENT_PATH",
        "",
    )


def test_recipe_catalog_content_approval_setting_defaults_safe_off() -> None:
    assert Settings.model_fields["RECIPE_CATALOG_CONTENT_APPROVED"].default is False
    assert Settings.model_fields["RECIPE_CATALOG_CONTENT_PATH"].default == ""


def _valid_content_record(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "recipeId": "kasza-warzywa-r1c-route",
        "version": 1,
        "lifecycleState": "active",
        "locale": "pl-PL",
        "title": "Kasza z warzywami",
        "description": "Przejrzany obiad z kaszy i warzyw.",
        "servings": 2,
        "yield": "2 porcje",
        "sourceAttribution": {
            "sourceType": "internal_curated",
            "sourceId": "r1c-route-review-kasza-warzywa",
            "sourceName": "Fitaly R1C review",
            "reviewedAt": "2026-06-20T10:00:00.000Z",
        },
        "updatedAt": "2026-06-20T10:00:00.000Z",
        "reviewState": "curated",
        "ingredients": [
            {
                "ingredientProductId": None,
                "snapshotName": "Kasza gryczana",
                "quantity": 120,
                "unit": "g",
            }
        ],
        "steps": ["Ugotuj kasze i wymieszaj z warzywami."],
        "prepTimeMin": 10,
        "cookTimeMin": 20,
        "nutritionSnapshot": {
            "kcal": 420,
            "proteinGrams": 18,
            "fatGrams": 12,
            "carbsGrams": 58,
            "confidence": "medium",
            "isPartial": False,
        },
        "imageRef": None,
        "profileFlagState": "complete",
        "dietaryFlags": ["vegetarian"],
        "allergenFlags": [],
        "unknownDietaryFlags": [],
        "unknownAllergenFlags": [],
        "styleTags": ["balanced"],
    }
    if overrides:
        record.update(overrides)
    return record


def _valid_content_pack(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    pack: dict[str, Any] = {
        "schemaVersion": 1,
        "contentVersion": "r1c-route-pack-v1",
        "locale": "pl-PL",
        "approval": {
            "approved": True,
            "approvedAt": "2026-06-20T10:30:00.000Z",
            "approvedBy": "nutrition-review",
        },
        "review": {
            "reviewedAt": "2026-06-20T10:30:00.000Z",
            "reviewedBy": "nutrition-review",
            "reviewSource": "r1c-review",
        },
        "records": [_valid_content_record()],
    }
    if overrides:
        pack.update(overrides)
    return pack


def _write_content_pack(path: Path, pack: dict[str, Any]) -> None:
    path.write_text(json.dumps(pack), encoding="utf-8")


def test_recipe_catalog_disabled_returns_stable_503_without_profile_or_catalog_work(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: AuthHeaders,
) -> None:
    monkeypatch.setattr(
        "app.api.v2.endpoints.recipe_catalog.settings.RECIPE_CATALOG_ENABLED",
        False,
    )
    get_profile = mocker.patch(
        "app.api.v2.endpoints.recipe_catalog.UserProfileService.get_profile"
    )
    evaluate = mocker.patch(
        "app.api.v2.endpoints.recipe_catalog.evaluate_recipe_catalog"
    )

    response = client.get(
        "/api/v2/users/me/recipes/catalog",
        headers=auth_headers("recipe-user-1"),
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "recipe_catalog_disabled",
            "message": "Recipe Catalog is temporarily disabled.",
        }
    }
    get_profile.assert_not_awaited()
    evaluate.assert_not_called()


def test_recipe_catalog_unapproved_content_returns_stable_503_without_profile_or_catalog_work(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: AuthHeaders,
) -> None:
    monkeypatch.setattr(
        "app.api.v2.endpoints.recipe_catalog.settings.RECIPE_CATALOG_CONTENT_APPROVED",
        False,
    )
    get_profile = mocker.patch(
        "app.api.v2.endpoints.recipe_catalog.UserProfileService.get_profile"
    )
    evaluate = mocker.patch(
        "app.api.v2.endpoints.recipe_catalog.evaluate_recipe_catalog"
    )

    response = client.get(
        "/api/v2/users/me/recipes/catalog",
        headers=auth_headers("recipe-user-1"),
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "recipe_catalog_content_not_approved",
            "message": "Recipe Catalog content is temporarily unavailable.",
        }
    }
    get_profile.assert_not_awaited()
    evaluate.assert_not_called()


def test_recipe_catalog_requires_authentication() -> None:
    response = client.get("/api/v2/users/me/recipes/catalog")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def _profile(
    *,
    allergies: list[str] | None = None,
    preferences: list[str] | None = None,
) -> UserProfile:
    return UserProfile(
        user_id="recipe-user-1",
        preferences=preferences or [],
        allergies=allergies or [],
    )


def test_recipe_catalog_route_defaults_to_authenticated_profile_filters(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    captured_requests: list[RecipeCatalogFilterRequest] = []

    def _evaluate(
        request: RecipeCatalogFilterRequest,
        *,
        catalog: Sequence[RecipeCatalogRecord] | None = None,
    ) -> object:
        captured_requests.append(request)
        return evaluate_recipe_catalog(request, catalog=catalog)

    evaluate = mocker.patch(
        "app.api.v2.endpoints.recipe_catalog.evaluate_recipe_catalog",
        side_effect=_evaluate,
    )
    get_profile = mocker.patch(
        "app.api.v2.endpoints.recipe_catalog.UserProfileService.get_profile",
        return_value=_profile(
            allergies=["peanuts"],
            preferences=["vegan", "highProtein"],
        ),
    )

    response = client.get(
        "/api/v2/users/me/recipes/catalog",
        headers=auth_headers("recipe-user-1"),
    )

    assert response.status_code == 200
    assert response.json()["emptyCatalog"] is True
    get_profile.assert_awaited_once_with(user_id="recipe-user-1")
    evaluate.assert_called_once()
    assert captured_requests[0].allergies == ["peanuts"]
    assert captured_requests[0].preferences == ["vegan", "highProtein"]


def test_recipe_catalog_route_query_filters_override_profile_defaults(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    captured_requests: list[RecipeCatalogFilterRequest] = []

    def _evaluate(
        request: RecipeCatalogFilterRequest,
        *,
        catalog: Sequence[RecipeCatalogRecord] | None = None,
    ) -> object:
        captured_requests.append(request)
        return evaluate_recipe_catalog(request, catalog=catalog)

    evaluate = mocker.patch(
        "app.api.v2.endpoints.recipe_catalog.evaluate_recipe_catalog",
        side_effect=_evaluate,
    )
    mocker.patch(
        "app.api.v2.endpoints.recipe_catalog.UserProfileService.get_profile",
        return_value=_profile(
            allergies=["lactose"],
            preferences=["balanced"],
        ),
    )

    response = client.get(
        "/api/v2/users/me/recipes/catalog"
        "?allergies=peanuts"
        "&preferences=vegan"
        "&preferences=highProtein"
        "&chronicDiseases=diabetes"
        "&allergiesOther=nightshade"
        "&lifestyle=night%20shifts"
        "&showHidden=true"
        "&revealUnknown=true",
        headers=auth_headers("recipe-user-1"),
    )

    assert response.status_code == 200
    assert response.json()["emptyCatalog"] is True
    evaluate.assert_called_once()
    assert captured_requests[0].allergies == ["peanuts"]
    assert captured_requests[0].preferences == ["vegan", "highProtein"]
    assert captured_requests[0].chronicDiseases == ["diabetes"]
    assert captured_requests[0].allergiesOther == "nightshade"
    assert captured_requests[0].lifestyle == "night shifts"
    assert captured_requests[0].showHidden is True
    assert captured_requests[0].revealUnknown is True


def test_recipe_catalog_route_can_clear_profile_default_filters(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    captured_requests: list[RecipeCatalogFilterRequest] = []

    def _evaluate(
        request: RecipeCatalogFilterRequest,
        *,
        catalog: Sequence[RecipeCatalogRecord] | None = None,
    ) -> object:
        captured_requests.append(request)
        return evaluate_recipe_catalog(request, catalog=catalog)

    mocker.patch(
        "app.api.v2.endpoints.recipe_catalog.evaluate_recipe_catalog",
        side_effect=_evaluate,
    )
    mocker.patch(
        "app.api.v2.endpoints.recipe_catalog.UserProfileService.get_profile",
        return_value=_profile(
            allergies=["lactose"],
            preferences=["balanced"],
        ),
    )

    response = client.get(
        "/api/v2/users/me/recipes/catalog"
        "?useProfileAllergies=false"
        "&useProfilePreferences=false",
        headers=auth_headers("recipe-user-1"),
    )

    assert response.status_code == 200
    assert response.json()["emptyCatalog"] is True
    assert captured_requests[0].allergies == []
    assert captured_requests[0].preferences == []


def test_recipe_catalog_route_returns_default_empty_catalog_shape(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.v2.endpoints.recipe_catalog.UserProfileService.get_profile",
        return_value=None,
    )

    response = client.get(
        "/api/v2/users/me/recipes/catalog",
        headers=auth_headers("recipe-user-1"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["totalCatalogCount"] == 0
    assert body["visibleCount"] == 0
    assert body["hiddenHardExclusionCount"] == 0
    assert body["unknownRevealRequiredCount"] == 0
    assert body["emptyCatalog"] is True
    assert body["lowResults"] is False


def test_recipe_catalog_route_uses_configured_valid_content_path(
    tmp_path: Path,
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: AuthHeaders,
) -> None:
    path = tmp_path / "recipe-catalog-pack.json"
    _write_content_pack(path, _valid_content_pack())
    monkeypatch.setattr(
        "app.api.v2.endpoints.recipe_catalog.settings.RECIPE_CATALOG_CONTENT_PATH",
        str(path),
    )
    captured_catalogs: list[tuple[RecipeCatalogRecord, ...]] = []

    def _evaluate(
        request: RecipeCatalogFilterRequest,
        *,
        catalog: Sequence[RecipeCatalogRecord] | None = None,
    ) -> object:
        captured_catalogs.append(tuple(catalog or ()))
        return evaluate_recipe_catalog(request, catalog=catalog)

    mocker.patch(
        "app.api.v2.endpoints.recipe_catalog.evaluate_recipe_catalog",
        side_effect=_evaluate,
    )
    mocker.patch(
        "app.api.v2.endpoints.recipe_catalog.UserProfileService.get_profile",
        return_value=None,
    )

    response = client.get(
        "/api/v2/users/me/recipes/catalog",
        headers=auth_headers("recipe-user-1"),
    )

    assert response.status_code == 200
    assert response.json()["totalCatalogCount"] == 1
    assert len(captured_catalogs) == 1
    assert captured_catalogs[0][0].recipeId == "kasza-warzywa-r1c-route"


def test_recipe_catalog_invalid_configured_content_returns_503_before_profile_lookup(
    tmp_path: Path,
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: AuthHeaders,
) -> None:
    path = tmp_path / "recipe-catalog-pack.json"
    pack = _valid_content_pack(
        {
            "approval": {
                "approved": False,
                "approvedAt": "2026-06-20T10:30:00.000Z",
                "approvedBy": "nutrition-review",
            }
        }
    )
    _write_content_pack(path, pack)
    monkeypatch.setattr(
        "app.api.v2.endpoints.recipe_catalog.settings.RECIPE_CATALOG_CONTENT_PATH",
        str(path),
    )
    get_profile = mocker.patch(
        "app.api.v2.endpoints.recipe_catalog.UserProfileService.get_profile"
    )
    evaluate = mocker.patch(
        "app.api.v2.endpoints.recipe_catalog.evaluate_recipe_catalog"
    )

    response = client.get(
        "/api/v2/users/me/recipes/catalog",
        headers=auth_headers("recipe-user-1"),
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "recipe_catalog_content_invalid",
            "message": "Recipe Catalog content pack is invalid.",
            "issueCodes": ["unapproved_content_pack"],
        }
    }
    assert "approvedAt" not in response.text
    get_profile.assert_not_awaited()
    evaluate.assert_not_called()


def test_recipe_catalog_profile_failure_is_explicit_503(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.v2.endpoints.recipe_catalog.UserProfileService.get_profile",
        side_effect=FirestoreServiceError("profile unavailable"),
    )

    response = client.get(
        "/api/v2/users/me/recipes/catalog",
        headers=auth_headers("recipe-user-1"),
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Recipe catalog profile filters are temporarily unavailable"
    }
