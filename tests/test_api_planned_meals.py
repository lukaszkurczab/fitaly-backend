from fastapi.testclient import TestClient
import pytest
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from app.schemas.meal import MealIngredient, MealTotals
from app.schemas.planned_meals import (
    PlannedMealDraftSnapshot,
    PlannedMealItem,
    PlannedMealMutationResponse,
    PlannedMealNutritionEstimate,
    PlannedMealStatus,
    PlannedMealsListQueryEcho,
    PlannedMealsListResponse,
)
from app.services.planned_meal_service import (
    PlannedMealNotFoundError,
    PlannedMealVersionConflictError,
)
from tests.types import AuthHeaders

client = TestClient(app)


@pytest.fixture(autouse=True)
def enable_planned_meals_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.api.v2.endpoints.planned_meals.settings.PLANNED_MEALS_ENABLED",
        True,
    )


def test_planned_meals_disabled_returns_stable_503_without_service_work(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: AuthHeaders,
) -> None:
    monkeypatch.setattr(
        "app.api.v2.endpoints.planned_meals.settings.PLANNED_MEALS_ENABLED",
        False,
    )
    list_planned = mocker.patch(
        "app.api.v2.endpoints.planned_meals.list_planned_meals_for_user"
    )

    response = client.get(
        "/api/v2/users/me/planned-meals",
        headers=auth_headers("planner-user-1"),
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "planned_meals_disabled",
            "message": "Planned Meals are temporarily disabled.",
        }
    }
    list_planned.assert_not_awaited()


def _draft_snapshot() -> PlannedMealDraftSnapshot:
    return PlannedMealDraftSnapshot(
        name="Planned oats",
        type="breakfast",
        ingredients=[
            MealIngredient(
                id="ingredient-1",
                name="Oats",
                amount=50,
                unit="g",
                kcal=180,
                protein=6,
                fat=3,
                carbs=32,
            )
        ],
        totals=MealTotals(kcal=180, protein=6, fat=3, carbs=32),
        notes=None,
        tags=[],
    )


def _estimate() -> PlannedMealNutritionEstimate:
    return PlannedMealNutritionEstimate(
        state="known",
        totals=MealTotals(kcal=180, protein=6, fat=3, carbs=32),
        missingFields=[],
        confidence="medium",
    )


def _item(*, status: PlannedMealStatus = "planned", version: int = 1) -> PlannedMealItem:
    return PlannedMealItem(
        plannedMealId="planned-1",
        version=version,
        dateBucket="2026-06-19",
        timeBucket="breakfast",
        sourceType="manual",
        sourceRef=None,
        draftSnapshot=_draft_snapshot(),
        nutritionEstimate=_estimate(),
        status=status,
        createdAt="2026-06-18T08:00:00.000Z",
        updatedAt="2026-06-18T08:00:00.000Z",
    )


def _mutation_result(*, updated: bool = True) -> dict[str, object]:
    return {"item": _item(), "applied": updated}


def _create_payload() -> dict[str, object]:
    return {
        "clientMutationId": "create-1",
        "plannedMealId": "planned-1",
        "dateBucket": "2026-06-19",
        "timeBucket": "breakfast",
        "sourceType": "manual",
        "sourceRef": None,
        "draftSnapshot": _draft_snapshot().model_dump(mode="json"),
        "nutritionEstimate": _estimate().model_dump(mode="json"),
    }


def test_planned_meals_require_authentication() -> None:
    response = client.get("/api/v2/users/me/planned-meals")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_planned_meals_route_lists_bounded_horizon(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    list_planned = mocker.patch(
        "app.api.v2.endpoints.planned_meals.list_planned_meals_for_user",
        return_value=PlannedMealsListResponse(
            items=[_item()],
            queryEcho=PlannedMealsListQueryEcho(
                startDate="2026-06-18",
                days=3,
                includeDeleted=False,
                returnedItems=1,
            ),
        ),
    )

    response = client.get(
        "/api/v2/users/me/planned-meals?startDate=2026-06-18&days=3",
        headers=auth_headers("planner-user-1"),
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["plannedMealId"] == "planned-1"
    list_planned.assert_awaited_once_with(
        "planner-user-1",
        start_date="2026-06-18",
        days=3,
        include_deleted=False,
    )


def test_planned_meals_route_creates_separate_planned_item(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    create_planned = mocker.patch(
        "app.api.v2.endpoints.planned_meals.create_planned_meal_for_user",
        return_value=_mutation_result(),
    )

    response = client.post(
        "/api/v2/users/me/planned-meals",
        headers=auth_headers("planner-user-1"),
        json=_create_payload(),
    )

    assert response.status_code == 201
    assert response.json() == PlannedMealMutationResponse(
        item=_item(),
        updated=True,
    ).model_dump(mode="json")
    create_planned.assert_awaited_once()


def test_planned_meals_route_updates_with_version_guard(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    update_planned = mocker.patch(
        "app.api.v2.endpoints.planned_meals.update_planned_meal_for_user",
        return_value=_mutation_result(),
    )

    response = client.patch(
        "/api/v2/users/me/planned-meals/planned-1",
        headers=auth_headers("planner-user-1"),
        json={
            "clientMutationId": "update-1",
            "expectedVersion": 1,
            "dateBucket": "2026-06-20",
        },
    )

    assert response.status_code == 200
    update_planned.assert_awaited_once()
    await_args = update_planned.await_args
    assert await_args is not None
    assert await_args.args[0] == "planner-user-1"
    assert await_args.args[1] == "planned-1"


def test_planned_meals_route_marks_deleted_without_logged_meal_delete(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    delete_planned = mocker.patch(
        "app.api.v2.endpoints.planned_meals.delete_planned_meal_for_user",
        return_value={"item": _item(status="deleted", version=2), "applied": True},
    )

    response = client.delete(
        "/api/v2/users/me/planned-meals/planned-1"
        "?clientMutationId=delete-1&expectedVersion=1",
        headers=auth_headers("planner-user-1"),
    )

    assert response.status_code == 200
    assert response.json()["item"]["status"] == "deleted"
    delete_planned.assert_awaited_once()


def test_planned_meals_route_maps_missing_item_to_404(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.v2.endpoints.planned_meals.update_planned_meal_for_user",
        side_effect=PlannedMealNotFoundError("missing"),
    )

    response = client.patch(
        "/api/v2/users/me/planned-meals/missing",
        headers=auth_headers("planner-user-1"),
        json={
            "clientMutationId": "update-1",
            "expectedVersion": 1,
            "dateBucket": "2026-06-20",
        },
    )

    assert response.status_code == 404


def test_planned_meals_route_maps_version_conflict_to_409(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.v2.endpoints.planned_meals.update_planned_meal_for_user",
        side_effect=PlannedMealVersionConflictError("stale"),
    )

    response = client.patch(
        "/api/v2/users/me/planned-meals/planned-1",
        headers=auth_headers("planner-user-1"),
        json={
            "clientMutationId": "update-1",
            "expectedVersion": 1,
            "dateBucket": "2026-06-20",
        },
    )

    assert response.status_code == 409


def test_planned_meals_route_maps_firestore_failure_to_503(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.v2.endpoints.planned_meals.list_planned_meals_for_user",
        side_effect=FirestoreServiceError("offline"),
    )

    response = client.get(
        "/api/v2/users/me/planned-meals",
        headers=auth_headers("planner-user-1"),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Planned meals are temporarily unavailable"}
