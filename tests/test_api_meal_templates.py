from io import BytesIO

from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from app.services.my_meal_service import StoredMealTemplateDocumentError
from app.services.meal_service import MealMutationDedupeConflictError
from tests.types import AuthHeaders

client = TestClient(app)


def _template_payload(overrides: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "templateId": "saved-1",
        "ownerUserId": "user-1",
        "templateVersion": 1,
        "displayName": "Saved meal",
        "description": None,
        "mealTypeHint": "lunch",
        "draftItems": [],
        "draftTotals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
        "nutritionSnapshot": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
        "imageRef": None,
        "createdAt": "2026-03-03T12:00:00.000Z",
        "updatedAt": "2026-03-03T12:30:00.000Z",
        "deleted": False,
        **(overrides or {}),
    }


def test_get_meal_template_changes_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    list_changes = mocker.patch(
        "app.api.routes.meal_templates.my_meal_service.list_changes",
        return_value=(
            [_template_payload()],
            "2026-03-03T12:30:00.000Z|saved-1",
        ),
    )

    response = client.get(
        "/api/v1/users/me/meal-templates/changes?afterCursor=2026-03-01T00:00:00.000Z",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [_template_payload()],
        "nextCursor": "2026-03-03T12:30:00.000Z|saved-1",
    }
    list_changes.assert_called_once_with(
        "user-1",
        limit_count=100,
        after_cursor="2026-03-01T00:00:00.000Z",
    )


def test_post_meal_template_upsert_uses_backend_service(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    upsert_saved_meal = mocker.patch(
        "app.api.routes.meal_templates.my_meal_service.upsert_saved_meal",
        return_value=_template_payload(),
    )

    response = client.post(
        "/api/v1/users/me/meal-templates",
        json={
            "clientMutationId": "mutation-saved-upsert-route",
            "templateId": "saved-1",
            "displayName": "Saved meal",
            "mealTypeHint": "lunch",
            "draftItems": [],
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json()["updated"] is True
    assert response.json()["template"] == _template_payload()
    assert "meal" not in response.json()
    upsert_saved_meal.assert_called_once()
    assert upsert_saved_meal.call_args.args[0] == "user-1"
    assert (
        upsert_saved_meal.call_args.args[1]["clientMutationId"]
        == "mutation-saved-upsert-route"
    )


def test_post_meal_template_upsert_requires_client_mutation_id(
    auth_headers: AuthHeaders,
) -> None:
    response = client.post(
        "/api/v1/users/me/meal-templates",
        json={
            "templateId": "saved-1",
            "mealTypeHint": "lunch",
            "draftItems": [],
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 422


def test_post_meal_template_upsert_rejects_logged_meal_shaped_request(
    auth_headers: AuthHeaders,
) -> None:
    response = client.post(
        "/api/v1/users/me/meal-templates",
        json={
            "clientMutationId": "mutation-saved-upsert-old-shape",
            "templateId": "saved-1",
            "loggedAt": "2026-03-03T12:00:00.000Z",
            "timestamp": "2026-03-03T12:00:00.000Z",
            "dayKey": "2026-03-03",
            "loggedAtLocalMin": 720,
            "tzOffsetMin": 60,
            "source": "saved",
            "inputMethod": "manual",
            "savedMealRefId": "saved-1",
            "syncState": "synced",
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 422


def test_post_meal_template_upsert_rejects_null_logged_meal_only_fields(
    auth_headers: AuthHeaders,
) -> None:
    response = client.post(
        "/api/v1/users/me/meal-templates",
        json={
            "clientMutationId": "mutation-saved-upsert-null-old-shape",
            "templateId": "saved-1",
            "loggedAt": None,
            "timestamp": None,
            "dayKey": None,
            "loggedAtLocalMin": None,
            "tzOffsetMin": None,
            "source": None,
            "inputMethod": None,
            "savedMealRefId": None,
            "syncState": None,
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 422


def test_post_meal_template_upsert_returns_409_for_client_mutation_conflict(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.meal_templates.my_meal_service.upsert_saved_meal",
        side_effect=MealMutationDedupeConflictError("clientMutationId conflict"),
    )

    response = client.post(
        "/api/v1/users/me/meal-templates",
        json={
            "clientMutationId": "mutation-saved-upsert-conflict",
            "templateId": "saved-1",
            "mealTypeHint": "lunch",
            "draftItems": [],
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "clientMutationId conflict"}


def test_post_meal_template_delete_uses_backend_service(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mark_deleted = mocker.patch(
        "app.api.routes.meal_templates.my_meal_service.mark_deleted",
        return_value=_template_payload(
            {"updatedAt": "2026-03-03T12:00:00.000Z", "deleted": True}
        ),
    )

    response = client.post(
        "/api/v1/users/me/meal-templates/saved-1/delete",
        json={
            "updatedAt": "2026-03-03T12:00:00.000Z",
            "clientMutationId": "mutation-saved-delete-route",
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "templateId": "saved-1",
        "updatedAt": "2026-03-03T12:00:00.000Z",
        "deleted": True,
    }
    mark_deleted.assert_called_once_with(
        "user-1",
        "saved-1",
        updated_at="2026-03-03T12:00:00.000Z",
        client_mutation_id="mutation-saved-delete-route",
    )


def test_post_meal_template_delete_requires_client_mutation_id(
    auth_headers: AuthHeaders,
) -> None:
    response = client.post(
        "/api/v1/users/me/meal-templates/saved-1/delete",
        json={"updatedAt": "2026-03-03T12:00:00.000Z"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 422


def test_post_meal_template_delete_reflects_service_deleted_value(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.meal_templates.my_meal_service.mark_deleted",
        return_value=_template_payload(
            {"updatedAt": "2026-03-03T13:00:00.000Z", "deleted": False}
        ),
    )

    response = client.post(
        "/api/v1/users/me/meal-templates/saved-1/delete",
        json={
            "updatedAt": "2026-03-03T12:00:00.000Z",
            "clientMutationId": "mutation-saved-delete-stale-route",
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "templateId": "saved-1",
        "updatedAt": "2026-03-03T13:00:00.000Z",
        "deleted": False,
    }


def test_post_meal_template_delete_returns_409_for_client_mutation_conflict(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.meal_templates.my_meal_service.mark_deleted",
        side_effect=MealMutationDedupeConflictError("clientMutationId conflict"),
    )

    response = client.post(
        "/api/v1/users/me/meal-templates/saved-1/delete",
        json={
            "updatedAt": "2026-03-03T12:00:00.000Z",
            "clientMutationId": "mutation-saved-delete-conflict",
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "clientMutationId conflict"}


def test_post_meal_template_photo_upload_uses_backend_service(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    upload_photo = mocker.patch(
        "app.api.routes.meal_templates.my_meal_service.upload_photo",
        return_value={
            "templateId": "saved-1",
            "imageId": "image-1",
            "storagePath": "mealTemplates/user-1/saved-1-image-1.jpg",
            "photoUrl": "https://cdn/saved-1.jpg",
        },
    )

    response = client.post(
        "/api/v1/users/me/meal-templates/saved-1/photo",
        files={"file": ("saved-1.jpg", BytesIO(b"jpeg-bytes"), "image/jpeg")},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "templateId": "saved-1",
        "imageId": "image-1",
        "storagePath": "mealTemplates/user-1/saved-1-image-1.jpg",
        "photoUrl": "https://cdn/saved-1.jpg",
    }
    upload_photo.assert_called_once()


def test_get_meal_template_changes_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.meal_templates.my_meal_service.list_changes",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.get(
        "/api/v1/users/me/meal-templates/changes",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}


def test_get_meal_template_changes_returns_500_for_stored_template_corruption(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.meal_templates.my_meal_service.list_changes",
        side_effect=StoredMealTemplateDocumentError(
            "Invalid meal template document saved-1: contains non-canonical fields: "
            "legacyField"
        ),
    )

    response = client.get(
        "/api/v1/users/me/meal-templates/changes",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}


def test_legacy_my_meals_path_is_not_accepted(
    auth_headers: AuthHeaders,
) -> None:
    response = client.get(
        "/api/v1/users/me/my-meals/changes",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 404
