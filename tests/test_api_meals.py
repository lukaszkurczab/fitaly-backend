from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from tests.types import AuthHeaders

client = TestClient(app)


def test_get_meals_history_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    list_history = mocker.patch(
        "app.api.routes.meals.meal_service.list_history",
        return_value=(
            [
                {
                    "id": "meal-1",
                    "loggedAt": "2026-03-03T12:00:00.000Z",
                    "type": "lunch",
                    "name": "Chicken",
                    "ingredients": [],
                    "createdAt": "2026-03-03T12:00:00.000Z",
                    "updatedAt": "2026-03-03T12:00:00.000Z",
                    "syncState": "synced",
                    "source": "manual",
                    "imageId": None,
                    "photoUrl": None,
                    "notes": None,
                    "tags": [],
                    "deleted": False,
                    "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
                }
            ],
            "2026-03-03T12:00:00.000Z|meal-1",
        ),
    )

    response = client.get(
        "/api/v1/users/me/meals/history"
        "?limit=10&caloriesMin=100&caloriesMax=500"
        "&dayKeyStart=2026-03-01&dayKeyEnd=2026-03-31",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json()["nextCursor"] == "2026-03-03T12:00:00.000Z|meal-1"
    list_history.assert_called_once_with(
        "user-1",
        limit_count=10,
        before_cursor=None,
        calories=(100.0, 500.0),
        protein=None,
        carbs=None,
        fat=None,
        day_key_start="2026-03-01",
        day_key_end="2026-03-31",
    )


def test_get_meals_history_rejects_invalid_day_key(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    list_history = mocker.patch("app.api.routes.meals.meal_service.list_history")

    response = client.get(
        "/api/v1/users/me/meals/history?dayKeyStart=2026/03/01",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "dayKey must use YYYY-MM-DD format"}
    list_history.assert_not_called()


def test_get_meal_changes_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    list_changes = mocker.patch(
        "app.api.routes.meals.meal_service.list_changes",
        return_value=([], "2026-03-03T12:00:00.000Z|meal-1"),
    )

    response = client.get(
        "/api/v1/users/me/meals/changes?afterCursor=2026-03-01T00:00:00.000Z",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "nextCursor": "2026-03-03T12:00:00.000Z|meal-1",
    }
    list_changes.assert_called_once_with(
        "user-1",
        limit_count=100,
        after_cursor="2026-03-01T00:00:00.000Z",
    )


def test_get_meal_photo_url_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    resolve_photo = mocker.patch(
        "app.api.routes.meals.meal_service.resolve_photo",
        return_value={
            "mealId": "meal-1",
            "imageId": "image-1",
            "photoUrl": "https://cdn/meal.jpg",
        },
    )

    response = client.get(
        "/api/v1/users/me/meals/photo-url?mealId=meal-1&imageId=image-1",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "mealId": "meal-1",
        "imageId": "image-1",
        "photoUrl": "https://cdn/meal.jpg",
    }
    resolve_photo.assert_called_once_with(
        "user-1",
        meal_id="meal-1",
        image_id="image-1",
    )


def test_post_meal_photo_upload_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    upload_photo = mocker.patch(
        "app.api.routes.meals.meal_service.upload_photo",
        return_value={
            "imageId": "image-1",
            "photoUrl": "https://cdn/meal.jpg",
        },
    )

    response = client.post(
        "/api/v1/users/me/meals/photo",
        files={"file": ("meal.jpg", b"meal-bytes", "image/jpeg")},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "mealId": None,
        "imageId": "image-1",
        "photoUrl": "https://cdn/meal.jpg",
    }
    upload_photo.assert_called_once()


def test_post_meal_upsert_persists_via_backend_service(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    upsert_meal = mocker.patch(
        "app.api.routes.meals.meal_service.upsert_meal",
        return_value={
            "id": "meal-1",
            "loggedAt": "2026-03-03T12:00:00.000Z",
            "type": "lunch",
            "name": "Chicken",
            "ingredients": [],
            "createdAt": "2026-03-03T12:00:00.000Z",
            "updatedAt": "2026-03-03T12:00:00.000Z",
            "syncState": "synced",
            "source": "manual",
            "imageId": None,
            "photoUrl": None,
            "notes": None,
            "tags": [],
            "deleted": False,
            "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
        },
    )

    response = client.post(
        "/api/v1/users/me/meals",
        json={
            "mealId": "meal-1",
            "timestamp": "2026-03-03T12:00:00.000Z",
            "type": "lunch",
            "ingredients": [],
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json()["updated"] is True
    upsert_meal.assert_called_once()


def test_post_meal_upsert_accepts_and_returns_input_method_and_ai_meta(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    upsert_meal = mocker.patch(
        "app.api.routes.meals.meal_service.upsert_meal",
        return_value={
            "id": "meal-1",
            "loggedAt": "2026-03-03T12:00:00.000Z",
            "type": "lunch",
            "name": "Chicken",
            "ingredients": [],
            "createdAt": "2026-03-03T12:00:00.000Z",
            "updatedAt": "2026-03-03T12:00:00.000Z",
            "syncState": "synced",
            "source": "ai",
            "inputMethod": "photo",
            "aiMeta": {
                "model": "gpt-4o-mini",
                "runId": "run-1",
                "confidence": 0.83,
                "warnings": ["partial_totals"],
            },
            "imageId": None,
            "photoUrl": None,
            "notes": None,
            "tags": [],
            "deleted": False,
            "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
        },
    )

    response = client.post(
        "/api/v1/users/me/meals",
        json={
            "mealId": "meal-1",
            "timestamp": "2026-03-03T12:00:00.000Z",
            "type": "lunch",
            "ingredients": [],
            "inputMethod": "photo",
            "aiMeta": {
                "model": "gpt-4o-mini",
                "runId": "run-1",
                "confidence": 0.83,
                "warnings": ["partial_totals"],
            },
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json()["meal"]["inputMethod"] == "photo"
    assert response.json()["meal"]["aiMeta"] == {
        "model": "gpt-4o-mini",
        "runId": "run-1",
        "confidence": 0.83,
        "warnings": ["partial_totals"],
    }
    upsert_meal.assert_called_once_with(
        "user-1",
        {
            "id": None,
            "mealId": "meal-1",
            "cloudId": None,
            "loggedAt": None,
            "timestamp": "2026-03-03T12:00:00.000Z",
            "dayKey": None,
            "loggedAtLocalMin": None,
            "tzOffsetMin": None,
            "type": "lunch",
            "name": None,
            "ingredients": [],
            "createdAt": None,
            "updatedAt": None,
            "syncState": None,
            "source": None,
            "inputMethod": "photo",
            "aiMeta": {
                "model": "gpt-4o-mini",
                "runId": "run-1",
                "confidence": 0.83,
                "warnings": ["partial_totals"],
            },
            "imageRef": None,
            "imageId": None,
            "photoUrl": None,
            "notes": None,
            "tags": [],
            "deleted": False,
            "totals": None,
            "userUid": None,
        },
    )


def test_post_meal_delete_uses_backend_service(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mark_deleted = mocker.patch(
        "app.api.routes.meals.meal_service.mark_deleted",
        return_value={
            "id": "meal-1",
            "updatedAt": "2026-03-03T12:00:00.000Z",
        },
    )

    response = client.post(
        "/api/v1/users/me/meals/meal-1/delete",
        json={"updatedAt": "2026-03-03T12:00:00.000Z"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "mealId": "meal-1",
        "updatedAt": "2026-03-03T12:00:00.000Z",
        "deleted": True,
    }
    mark_deleted.assert_called_once_with(
        "user-1",
        "meal-1",
        updated_at="2026-03-03T12:00:00.000Z",
    )


def test_get_meals_history_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.meals.meal_service.list_history",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.get(
        "/api/v1/users/me/meals/history",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}


def test_get_meal_photo_url_returns_400_for_missing_identifiers(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.meals.meal_service.resolve_photo",
        side_effect=ValueError("Missing meal photo identifier"),
    )

    response = client.get(
        "/api/v1/users/me/meals/photo-url",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Missing meal photo identifier"}
