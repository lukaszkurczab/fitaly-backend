from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from app.schemas.food_library import (
    IngredientProductSearchQueryEcho,
    IngredientProductSearchResponse,
)
from tests.types import AuthHeaders

client = TestClient(app)


def _empty_response() -> IngredientProductSearchResponse:
    return IngredientProductSearchResponse(
        items=[],
        queryEcho=IngredientProductSearchQueryEcho(
            normalizedQuery="owies",
            queryLength=5,
            limit=8,
            includeUserScoped=True,
            includeGlobal=True,
            locale=None,
        ),
        warnings=[],
    )


def test_search_ingredient_products_requires_authentication() -> None:
    response = client.get("/api/v2/users/me/ingredient-products/search?query=owies")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_search_ingredient_products_rejects_short_query(
    auth_headers: AuthHeaders,
) -> None:
    response = client.get(
        "/api/v2/users/me/ingredient-products/search?query=a",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Ingredient/Product search query is too short"}


def test_search_ingredient_products_uses_authenticated_user(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    search = mocker.patch(
        "app.api.routes.food_library.food_library_service.search_ingredient_products",
        return_value=_empty_response(),
    )

    response = client.get(
        "/api/v2/users/me/ingredient-products/search"
        "?query=owies&locale=pl-PL&limit=99&includeGlobal=false",
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 200
    assert response.json()["queryEcho"]["normalizedQuery"] == "owies"
    search.assert_awaited_once_with(
        "route-user-1",
        query="owies",
        locale="pl-PL",
        limit_count=99,
        include_user_scoped=True,
        include_global=False,
    )


def test_search_ingredient_products_maps_firestore_failure_to_typed_degraded_response(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.food_library.food_library_service.search_ingredient_products",
        side_effect=FirestoreServiceError("database unavailable"),
    )

    response = client.get(
        "/api/v2/users/me/ingredient-products/search?query=owies&limit=99",
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["warnings"] == ["backend_degraded"]
    assert body["queryEcho"]["limit"] == 12
    assert body["queryEcho"]["normalizedQuery"] == "owies"
