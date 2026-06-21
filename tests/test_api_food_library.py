from fastapi.testclient import TestClient
import pytest
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from app.schemas.food_library import (
    IngredientProductCreateResponse,
    IngredientProductPullResponse,
    IngredientProductSearchQueryEcho,
    IngredientProductSearchResponse,
    IngredientProductUpdateResponse,
)
from app.services.food_library_service import (
    IngredientProductMutationConflictError,
    IngredientProductNotFoundError,
)
from tests.types import AuthHeaders

client = TestClient(app)


@pytest.fixture(autouse=True)
def enable_food_library_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.api.routes.food_library.settings.FOOD_LIBRARY_ENABLED",
        True,
    )


def test_food_library_disabled_returns_stable_503_without_service_work(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: AuthHeaders,
) -> None:
    monkeypatch.setattr(
        "app.api.routes.food_library.settings.FOOD_LIBRARY_ENABLED",
        False,
    )
    search = mocker.patch(
        "app.api.routes.food_library.food_library_service.search_ingredient_products"
    )

    response = client.get(
        "/api/v2/users/me/ingredient-products/search?query=owies",
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "food_library_disabled",
            "message": "Food Library is temporarily disabled.",
        }
    }
    search.assert_not_awaited()


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


def _delete_payload() -> dict[str, object]:
    return {"clientMutationId": "delete-mutation-1"}


def _update_payload() -> dict[str, object]:
    return {
        "clientMutationId": "update-mutation-1",
        "displayName": "Owsianka po edycji",
        "defaultServing": {"quantity": 60, "unit": "g"},
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


def _update_response() -> IngredientProductUpdateResponse:
    response = _create_response().model_dump(mode="json")
    response["updated"] = True
    response["item"]["displayName"] = "Owsianka po edycji"  # type: ignore[index]
    response["item"]["defaultServing"] = {"quantity": 60, "unit": "g"}  # type: ignore[index]
    return IngredientProductUpdateResponse.model_validate(response)


def _pull_response() -> IngredientProductPullResponse:
    return IngredientProductPullResponse.model_validate(
        {
            "records": [
                {
                    "item": _create_response().item.model_dump(mode="json"),
                    "updatedAt": "2026-06-16T10:00:00.000Z",
                    "creationClientMutationId": "mutation-1",
                }
            ],
            "removedRecords": [
                {
                    "ingredientProductId": "user-oats-rejected",
                    "updatedAt": "2026-06-16T11:00:00.000Z",
                    "removalReason": "rejected",
                }
            ],
            "nextUpdatedAfter": "2026-06-16T10:00:00.000Z|user-oats-1",
        }
    )


def test_search_ingredient_products_requires_authentication() -> None:
    response = client.get("/api/v2/users/me/ingredient-products/search?query=owies")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_pull_ingredient_products_requires_authentication() -> None:
    response = client.get("/api/v2/users/me/ingredient-products/pull")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_pull_ingredient_products_uses_authenticated_user(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    pull = mocker.patch(
        "app.api.routes.food_library.food_library_service.pull_user_ingredient_products",
        return_value=_pull_response(),
    )

    response = client.get(
        "/api/v2/users/me/ingredient-products/pull"
        "?updatedAfter=2026-06-15T10%3A00%3A00.000Z&limit=999",
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 200
    assert response.json()["nextUpdatedAfter"] == "2026-06-16T10:00:00.000Z|user-oats-1"
    assert response.json()["removedRecords"] == [
        {
            "ingredientProductId": "user-oats-rejected",
            "updatedAt": "2026-06-16T11:00:00.000Z",
            "removalReason": "rejected",
        }
    ]
    pull.assert_awaited_once_with(
        "route-user-1",
        updated_after="2026-06-15T10:00:00.000Z",
        limit_count=999,
    )


def test_pull_ingredient_products_firestore_failure_returns_503(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.food_library.food_library_service.pull_user_ingredient_products",
        side_effect=FirestoreServiceError("down"),
    )

    response = client.get(
        "/api/v2/users/me/ingredient-products/pull",
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Ingredient/Product pull is temporarily unavailable"
    }


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


def test_update_ingredient_product_requires_authentication() -> None:
    response = client.post(
        "/api/v2/users/me/ingredient-products/user-oats-1/update",
        json=_update_payload(),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_update_ingredient_product_uses_authenticated_user(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    update = mocker.patch(
        "app.api.routes.food_library.food_library_service.update_user_ingredient_product",
        return_value=(_update_response().item, True),
    )

    response = client.post(
        "/api/v2/users/me/ingredient-products/user-oats-1/update",
        json=_update_payload(),
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 200
    assert response.json()["updated"] is True
    assert response.json()["item"]["displayName"] == "Owsianka po edycji"
    update.assert_awaited_once()
    assert update.await_args is not None
    assert update.await_args.args[0] == "route-user-1"
    assert update.await_args.kwargs["ingredient_product_id"] == "user-oats-1"
    assert update.await_args.kwargs["request"].clientMutationId == "update-mutation-1"


def test_update_ingredient_product_not_found_returns_404(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.food_library.food_library_service.update_user_ingredient_product",
        side_effect=IngredientProductNotFoundError("Ingredient/Product record was not found."),
    )

    response = client.post(
        "/api/v2/users/me/ingredient-products/user-oats-1/update",
        json=_update_payload(),
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Ingredient/Product record was not found."}


def test_update_ingredient_product_conflict_returns_409(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.food_library.food_library_service.update_user_ingredient_product",
        side_effect=IngredientProductMutationConflictError("conflict"),
    )

    response = client.post(
        "/api/v2/users/me/ingredient-products/user-oats-1/update",
        json=_update_payload(),
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "conflict"}


def test_update_ingredient_product_firestore_failure_returns_503(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.food_library.food_library_service.update_user_ingredient_product",
        side_effect=FirestoreServiceError("down"),
    )

    response = client.post(
        "/api/v2/users/me/ingredient-products/user-oats-1/update",
        json=_update_payload(),
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Ingredient/Product update is temporarily unavailable"
    }


def test_delete_ingredient_product_requires_authentication() -> None:
    response = client.post(
        "/api/v2/users/me/ingredient-products/user-oats-1/delete",
        json=_delete_payload(),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_delete_ingredient_product_uses_authenticated_user(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    delete = mocker.patch(
        "app.api.routes.food_library.food_library_service.delete_user_ingredient_product",
        return_value=("user-oats-1", "2026-06-16T12:00:00.000Z", True),
    )

    response = client.post(
        "/api/v2/users/me/ingredient-products/user-oats-1/delete",
        json=_delete_payload(),
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "ingredientProductId": "user-oats-1",
        "updatedAt": "2026-06-16T12:00:00.000Z",
        "updated": True,
    }
    delete.assert_awaited_once_with(
        "route-user-1",
        ingredient_product_id="user-oats-1",
        client_mutation_id="delete-mutation-1",
    )


def test_delete_ingredient_product_not_found_returns_404(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.food_library.food_library_service.delete_user_ingredient_product",
        side_effect=IngredientProductNotFoundError("Ingredient/Product record was not found."),
    )

    response = client.post(
        "/api/v2/users/me/ingredient-products/user-oats-1/delete",
        json=_delete_payload(),
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Ingredient/Product record was not found."}


def test_delete_ingredient_product_firestore_failure_returns_503(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.food_library.food_library_service.delete_user_ingredient_product",
        side_effect=FirestoreServiceError("down"),
    )

    response = client.post(
        "/api/v2/users/me/ingredient-products/user-oats-1/delete",
        json=_delete_payload(),
        headers=auth_headers("route-user-1"),
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Ingredient/Product delete is temporarily unavailable"
    }
