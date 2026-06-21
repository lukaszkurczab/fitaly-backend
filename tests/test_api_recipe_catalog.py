from fastapi.testclient import TestClient
import pytest
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.domain.users.models.user_profile import UserProfile
from app.main import app
from app.schemas.recipes import RecipeCatalogFilterRequest
from app.services.recipe_catalog_service import evaluate_recipe_catalog
from tests.types import AuthHeaders

client = TestClient(app)


@pytest.fixture(autouse=True)
def enable_recipe_catalog_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.api.v2.endpoints.recipe_catalog.settings.RECIPE_CATALOG_ENABLED",
        True,
    )


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
    ) -> object:
        captured_requests.append(request)
        return evaluate_recipe_catalog(request, catalog=[])

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
    ) -> object:
        captured_requests.append(request)
        return evaluate_recipe_catalog(request, catalog=[])

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
    ) -> object:
        captured_requests.append(request)
        return evaluate_recipe_catalog(request, catalog=[])

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


def test_recipe_catalog_route_returns_default_catalog_shape(
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
    assert body["totalCatalogCount"] == 7
    assert body["visibleCount"] == 7
    assert body["emptyCatalog"] is False
    assert body["items"][0]["recipe"]["recipeId"] == "berry-yogurt-bowl"


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
