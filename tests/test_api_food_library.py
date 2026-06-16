from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from app.schemas.food_library import (
    IngredientProductCreateResponse,
    IngredientProductSearchQueryEcho,
    IngredientProductSearchResponse,
)
from app.services.food_library_service import IngredientProductMutationConflictError
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


def _create_payload() -> dict[str, object]:
    return {
        "clientMutationId": "mutation-1",
        "ingredientProductId": "user-oats-1",
        "displayName": "Owsianka domowa",
        "kind": "generic_ingredient",
        "defaultServing": {"quantity": 50, "unit": "g"},
        "nutritionPer100": {
            "basis": "per_100g",
            "unit": "g",
            "kcal": 370,
            "protein": 13,
            "fat": 7,
            "carbs": 60,
        },
    }


def _create_response() -> IngredientProductCreateResponse:
    return IngredientProductCreateResponse.model_validate(
        {
            "updated": True,
            "item": {
                "ingredientProductId": "user-oats-1",
                "recordScope": "user_scoped",
                "lifecycleState": "candidate",
                "displayName": "Owsianka domowa",
                "kind": "generic_ingredient",
                "defaultServing": {"quantity": 50, "unit": "g"},
                "nutritionPer100": {
                    "basis": "per_100g",
                    "unit": "g",
                    "kcal": 370,
                    "protein": 13,
                    "fat": 7,
                    "carbs": 60,
                },
                "confidence": {
                    "identity": "low",
                    "nutrition": "low",
                    "profile": "unknown",
                },
                "sourceAttribution": {
                    "sourceType": "user_created",
                    "sourceId": "mutation-1",
                    "sourceName": "manual_entry",
                },
                "profileCompatibility": {
                    "status": "unknown",
                    "dietaryFlags": [],
                    "allergenFlags": [],
                },
                "warningReasonCodes": [
                    "profile_unknown",
                    "nutrition_low_confidence",
                    "pending_user_record",
                ],
                "rankingSignals": [
                    "user_scoped",
                    "exact_user",
                    "profile_warning",
                    "nutrition_warning",
                    "pending_user_record",
                ],
                "ownerUserId": "route-user-1",
            },
        }
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


def test_create_ingredient_product_requires_authentication() -> None:
    response = client.post(
        "/api/v2/users/me/ingredient-products",
        json=_create_payload(),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_create_ingredient_product_uses_authenticated_user(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    create = mocker.patch(
        "app.api.routes.food_library.food_library_service.create_user_ingredient_product",
        return_value=(_create_response().item, True),
    )

    response = client.post(
        "/api/v2/users/me/ingredient-products",
        json=_create_payload(),
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 200
    assert response.json()["updated"] is True
    assert response.json()["item"]["recordScope"] == "user_scoped"
    assert response.json()["item"]["lifecycleState"] == "candidate"
    create.assert_awaited_once()
    assert create.await_args is not None
    assert create.await_args.args[0] == "route-user-1"
    assert create.await_args.args[1].ingredientProductId == "user-oats-1"


def test_create_ingredient_product_rejects_path_like_document_id(
    auth_headers: AuthHeaders,
) -> None:
    payload = _create_payload()
    payload["ingredientProductId"] = "users/user-1/global-oats"

    response = client.post(
        "/api/v2/users/me/ingredient-products",
        json=payload,
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 422


def test_create_ingredient_product_conflict_returns_409(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.food_library.food_library_service.create_user_ingredient_product",
        side_effect=IngredientProductMutationConflictError("conflict"),
    )

    response = client.post(
        "/api/v2/users/me/ingredient-products",
        json=_create_payload(),
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "conflict"}
