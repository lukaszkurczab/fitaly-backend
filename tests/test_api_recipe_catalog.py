from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.domain.users.models.user_profile import UserProfile
from app.main import app
from app.schemas.recipes import RecipeCatalogFilterRequest
from app.services.recipe_catalog_service import evaluate_recipe_catalog
from tests.types import AuthHeaders

client = TestClient(app)


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
    assert body["totalCatalogCount"] == 6
    assert body["visibleCount"] == 6
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
