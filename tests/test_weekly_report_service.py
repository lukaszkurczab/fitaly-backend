import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pytest_mock import MockerFixture

from app.schemas.weekly_reports import WeeklyReportResponse
from app.services.weekly_report_aggregation import build_weekly_aggregate_from_meals
from app.services.weekly_report_service import (
    build_weekly_report_period,
    get_weekly_report,
    resolve_requested_week_end,
)

FIXTURES_DIR = Path(__file__).parent / "contract_fixtures"


class _FilterLike(Protocol):
    field_path: str
    op_string: str
    value: Any


class _FakeQuery:
    def __init__(
        self,
        collection: "_FakeMealsCollection",
        filters: list[_FilterLike] | None = None,
    ) -> None:
        self._collection = collection
        self._filters = filters or []

    def where(self, *, filter: _FilterLike) -> "_FakeQuery":
        return _FakeQuery(self._collection, [*self._filters, filter])

    def stream(self):
        self._collection.calls.append(
            [(flt.field_path, flt.op_string, flt.value) for flt in self._filters]
        )
        return self._collection.snapshots


class _FakeMealsCollection:
    def __init__(self, snapshots: list[object]) -> None:
        self.snapshots = snapshots
        self.calls: list[list[tuple[str, str, Any]]] = []

    def where(self, *, filter: _FilterLike) -> _FakeQuery:
        return _FakeQuery(self, [filter])


def _make_snapshot(mocker: MockerFixture, meal: dict[str, Any]) -> Any:
    snapshot = mocker.Mock()
    snapshot.id = meal["mealId"]
    snapshot.to_dict.return_value = meal
    return snapshot


def _meal(
    *,
    meal_id: str,
    day_key: str,
    timestamp: str,
    meal_type: str = "lunch",
    logged_at_local_min: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mealId": meal_id,
        "cloudId": meal_id,
        "dayKey": day_key,
        "timestamp": timestamp,
        "type": meal_type,
        "deleted": False,
        "totals": {"kcal": 500, "protein": 25, "carbs": 0, "fat": 0},
        "ingredients": [
            {
                "id": f"{meal_id}-1",
                "name": "Ingredient",
                "amount": 100,
                "kcal": 500,
                "protein": 25,
                "carbs": 0,
                "fat": 0,
            }
        ],
    }
    if logged_at_local_min is not None:
        payload["loggedAtLocalMin"] = logged_at_local_min
    return payload


def _mock_firestore(
    mocker: MockerFixture,
    *,
    meals: list[dict[str, Any]],
):
    meal_snapshots = [_make_snapshot(mocker, meal) for meal in meals]
    meals_collection = _FakeMealsCollection(meal_snapshots)

    user_snapshot = mocker.Mock()
    user_snapshot.exists = True
    user_snapshot.to_dict.return_value = {}

    user_ref = mocker.Mock()
    user_ref.get.return_value = user_snapshot
    user_ref.collection.return_value = meals_collection

    client = mocker.Mock()
    client.collection.return_value.document.return_value = user_ref
    return client, meals_collection


def _load_fixture() -> WeeklyReportResponse:
    payload = json.loads((FIXTURES_DIR / "weekly_report.json").read_text(encoding="utf-8"))
    return WeeklyReportResponse.model_validate(payload)


def test_resolve_requested_week_end_defaults_to_yesterday() -> None:
    resolved = resolve_requested_week_end(
        None,
        now=datetime(2026, 3, 21, 8, 0, tzinfo=UTC),
    )

    assert resolved == "2026-03-20"


def test_resolve_requested_week_end_rejects_open_day() -> None:
    try:
        resolve_requested_week_end(
            "2026-03-21",
            now=datetime(2026, 3, 21, 8, 0, tzinfo=UTC),
        )
    except ValueError as exc:
        assert str(exc) == "weekEnd must be a closed day before today."
    else:
        raise AssertionError("Expected ValueError for open weekEnd")


def test_build_weekly_report_period_builds_closed_7_day_window() -> None:
    period = build_weekly_report_period("2026-03-15")

    assert period.startDay == "2026-03-09"
    assert period.endDay == "2026-03-15"


def test_get_weekly_report_returns_insufficient_data_placeholder(
    mocker: MockerFixture,
) -> None:
    fixture = _load_fixture()
    mocker.patch(
        "app.services.weekly_report_service.collect_weekly_aggregate",
        return_value=build_weekly_aggregate_from_meals(
            period=fixture.period,
            meals=[],
        ),
    )

    response = asyncio.run(
        get_weekly_report(
            "user-1",
            week_end=fixture.period.endDay,
            now=datetime(2026, 3, 21, 8, 0, tzinfo=UTC),
        )
    )

    assert response == fixture


def test_get_weekly_report_returns_ready_when_week_has_enough_valid_days(
    mocker: MockerFixture,
) -> None:
    meals = [
        _meal(
            meal_id="meal-1",
            day_key="2026-03-09",
            timestamp="2026-03-09T07:00:00Z",
            meal_type="breakfast",
            logged_at_local_min=8 * 60,
        ),
        _meal(
            meal_id="meal-2",
            day_key="2026-03-10",
            timestamp="2026-03-10T12:00:00Z",
            meal_type="lunch",
            logged_at_local_min=13 * 60,
        ),
        _meal(
            meal_id="meal-3",
            day_key="2026-03-11",
            timestamp="2026-03-11T18:00:00Z",
            meal_type="dinner",
            logged_at_local_min=19 * 60,
        ),
        _meal(
            meal_id="meal-4",
            day_key="2026-03-12",
            timestamp="2026-03-12T12:00:00Z",
            meal_type="lunch",
            logged_at_local_min=13 * 60,
        ),
    ]
    client, meals_collection = _mock_firestore(mocker, meals=meals)
    mocker.patch("app.services.weekly_report_aggregation.get_firestore", return_value=client)

    response = asyncio.run(
        get_weekly_report(
            "user-1",
            week_end="2026-03-15",
            now=datetime(2026, 3, 21, 8, 0, tzinfo=UTC),
        )
    )

    assert response.status == "ready"
    assert response.period.startDay == "2026-03-09"
    assert response.period.endDay == "2026-03-15"
    assert 2 <= len(response.insights) <= 4
    assert 1 <= len(response.priorities) <= 2
    assert response.summary is not None
    assert len(meals_collection.calls) == 3


def test_get_weekly_report_returns_insufficient_data_for_three_valid_days(
    mocker: MockerFixture,
) -> None:
    meals = [
        _meal(
            meal_id="meal-1",
            day_key="2026-03-09",
            timestamp="2026-03-09T12:00:00Z",
            meal_type="lunch",
            logged_at_local_min=13 * 60,
        ),
        _meal(
            meal_id="meal-2",
            day_key="2026-03-10",
            timestamp="2026-03-10T12:00:00Z",
            meal_type="lunch",
            logged_at_local_min=13 * 60,
        ),
        _meal(
            meal_id="meal-3",
            day_key="2026-03-11",
            timestamp="2026-03-11T18:00:00Z",
            meal_type="dinner",
            logged_at_local_min=19 * 60,
        ),
    ]
    client, _ = _mock_firestore(mocker, meals=meals)
    mocker.patch("app.services.weekly_report_aggregation.get_firestore", return_value=client)

    response = asyncio.run(
        get_weekly_report(
            "user-1",
            week_end="2026-03-15",
            now=datetime(2026, 3, 21, 8, 0, tzinfo=UTC),
        )
    )

    assert response.status == "insufficient_data"
    assert response.insights == []
    assert response.priorities == []


def test_get_weekly_report_builds_expected_payload_for_strong_week(
    mocker: MockerFixture,
) -> None:
    meals = [
        _meal(
            meal_id="d1-breakfast",
            day_key="2026-03-09",
            timestamp="2026-03-09T07:00:00Z",
            meal_type="breakfast",
            logged_at_local_min=8 * 60,
        ),
        _meal(
            meal_id="d1-lunch",
            day_key="2026-03-09",
            timestamp="2026-03-09T12:00:00Z",
            meal_type="lunch",
            logged_at_local_min=13 * 60,
        ),
        _meal(
            meal_id="d1-dinner",
            day_key="2026-03-09",
            timestamp="2026-03-09T18:00:00Z",
            meal_type="dinner",
            logged_at_local_min=19 * 60,
        ),
        _meal(
            meal_id="d2-breakfast",
            day_key="2026-03-10",
            timestamp="2026-03-10T07:15:00Z",
            meal_type="breakfast",
            logged_at_local_min=8 * 60 + 15,
        ),
        _meal(
            meal_id="d2-lunch",
            day_key="2026-03-10",
            timestamp="2026-03-10T12:15:00Z",
            meal_type="lunch",
            logged_at_local_min=13 * 60 + 15,
        ),
        _meal(
            meal_id="d2-dinner",
            day_key="2026-03-10",
            timestamp="2026-03-10T18:15:00Z",
            meal_type="dinner",
            logged_at_local_min=19 * 60 + 15,
        ),
        _meal(
            meal_id="d3-breakfast",
            day_key="2026-03-11",
            timestamp="2026-03-11T07:30:00Z",
            meal_type="breakfast",
            logged_at_local_min=8 * 60 + 30,
        ),
        _meal(
            meal_id="d3-lunch",
            day_key="2026-03-11",
            timestamp="2026-03-11T12:30:00Z",
            meal_type="lunch",
            logged_at_local_min=13 * 60 + 30,
        ),
        _meal(
            meal_id="d3-dinner",
            day_key="2026-03-11",
            timestamp="2026-03-11T18:30:00Z",
            meal_type="dinner",
            logged_at_local_min=19 * 60 + 30,
        ),
        _meal(
            meal_id="d4-breakfast",
            day_key="2026-03-12",
            timestamp="2026-03-12T07:10:00Z",
            meal_type="breakfast",
            logged_at_local_min=8 * 60 + 10,
        ),
        _meal(
            meal_id="d4-lunch",
            day_key="2026-03-12",
            timestamp="2026-03-12T12:10:00Z",
            meal_type="lunch",
            logged_at_local_min=13 * 60 + 10,
        ),
        _meal(
            meal_id="d4-dinner",
            day_key="2026-03-12",
            timestamp="2026-03-12T18:10:00Z",
            meal_type="dinner",
            logged_at_local_min=19 * 60 + 10,
        ),
        _meal(
            meal_id="d5-breakfast",
            day_key="2026-03-14",
            timestamp="2026-03-14T09:00:00Z",
            meal_type="breakfast",
            logged_at_local_min=8 * 60 + 45,
        ),
        _meal(
            meal_id="d5-lunch",
            day_key="2026-03-14",
            timestamp="2026-03-14T13:00:00Z",
            meal_type="lunch",
            logged_at_local_min=14 * 60,
        ),
        _meal(
            meal_id="d5-dinner",
            day_key="2026-03-14",
            timestamp="2026-03-14T18:20:00Z",
            meal_type="dinner",
            logged_at_local_min=19 * 60 + 20,
        ),
        _meal(
            meal_id="d6-breakfast",
            day_key="2026-03-15",
            timestamp="2026-03-15T09:30:00Z",
            meal_type="breakfast",
            logged_at_local_min=9 * 60,
        ),
        _meal(
            meal_id="d6-lunch",
            day_key="2026-03-15",
            timestamp="2026-03-15T13:30:00Z",
            meal_type="lunch",
            logged_at_local_min=14 * 60 + 30,
        ),
        _meal(
            meal_id="d6-dinner",
            day_key="2026-03-15",
            timestamp="2026-03-15T18:30:00Z",
            meal_type="dinner",
            logged_at_local_min=19 * 60 + 30,
        ),
    ]
    client, _ = _mock_firestore(mocker, meals=meals)
    mocker.patch("app.services.weekly_report_aggregation.get_firestore", return_value=client)

    response = asyncio.run(
        get_weekly_report(
            "user-1",
            week_end="2026-03-15",
            now=datetime(2026, 3, 21, 8, 0, tzinfo=UTC),
        )
    )

    assert response.status == "ready"
    assert response.summary == (
        "Logging stayed steady across the week. Next focus: keep the same logging rhythm on most days."
    )
    assert [insight.type for insight in response.insights] == [
        "consistency",
        "day_completion_pattern",
        "start_of_day_pattern",
    ]
    assert [priority.type for priority in response.priorities] == [
        "maintain_consistency",
    ]


def test_get_weekly_report_builds_expected_payload_for_weekend_drift_week(
    mocker: MockerFixture,
) -> None:
    meals = [
        _meal(
            meal_id="mon-breakfast",
            day_key="2026-03-09",
            timestamp="2026-03-09T07:00:00Z",
            meal_type="breakfast",
            logged_at_local_min=8 * 60,
        ),
        _meal(
            meal_id="mon-dinner",
            day_key="2026-03-09",
            timestamp="2026-03-09T18:00:00Z",
            meal_type="dinner",
            logged_at_local_min=19 * 60,
        ),
        _meal(
            meal_id="tue-breakfast",
            day_key="2026-03-10",
            timestamp="2026-03-10T07:15:00Z",
            meal_type="breakfast",
            logged_at_local_min=8 * 60 + 15,
        ),
        _meal(
            meal_id="tue-dinner",
            day_key="2026-03-10",
            timestamp="2026-03-10T18:10:00Z",
            meal_type="dinner",
            logged_at_local_min=19 * 60 + 10,
        ),
        _meal(
            meal_id="wed-breakfast",
            day_key="2026-03-11",
            timestamp="2026-03-11T07:30:00Z",
            meal_type="breakfast",
            logged_at_local_min=8 * 60 + 30,
        ),
        _meal(
            meal_id="wed-dinner",
            day_key="2026-03-11",
            timestamp="2026-03-11T18:20:00Z",
            meal_type="dinner",
            logged_at_local_min=19 * 60 + 20,
        ),
        _meal(
            meal_id="thu-breakfast",
            day_key="2026-03-12",
            timestamp="2026-03-12T07:10:00Z",
            meal_type="breakfast",
            logged_at_local_min=8 * 60 + 10,
        ),
        _meal(
            meal_id="thu-dinner",
            day_key="2026-03-12",
            timestamp="2026-03-12T18:15:00Z",
            meal_type="dinner",
            logged_at_local_min=19 * 60 + 15,
        ),
        _meal(
            meal_id="fri-breakfast",
            day_key="2026-03-13",
            timestamp="2026-03-13T07:20:00Z",
            meal_type="breakfast",
            logged_at_local_min=8 * 60 + 20,
        ),
        _meal(
            meal_id="fri-dinner",
            day_key="2026-03-13",
            timestamp="2026-03-13T18:25:00Z",
            meal_type="dinner",
            logged_at_local_min=19 * 60 + 25,
        ),
        _meal(
            meal_id="sat-breakfast",
            day_key="2026-03-14",
            timestamp="2026-03-14T09:00:00Z",
            meal_type="breakfast",
            logged_at_local_min=10 * 60,
        ),
        _meal(
            meal_id="sat-dinner",
            day_key="2026-03-14",
            timestamp="2026-03-14T18:30:00Z",
            meal_type="dinner",
            logged_at_local_min=19 * 60 + 30,
        ),
    ]
    client, _ = _mock_firestore(mocker, meals=meals)
    mocker.patch("app.services.weekly_report_aggregation.get_firestore", return_value=client)

    response = asyncio.run(
        get_weekly_report(
            "user-1",
            week_end="2026-03-15",
            now=datetime(2026, 3, 21, 8, 0, tzinfo=UTC),
        )
    )

    assert response.status == "ready"
    assert response.summary == (
        "Logging stayed steady across the week. Next focus: protect Saturday and Sunday so they do not break the week."
    )
    assert [insight.type for insight in response.insights] == [
        "consistency",
        "weekend_drift",
        "day_completion_pattern",
    ]
    assert [priority.type for priority in response.priorities] == [
        "reduce_weekend_drift",
        "maintain_consistency",
    ]


def test_get_weekly_report_returns_insufficient_data_for_empty_week(
    mocker: MockerFixture,
) -> None:
    client, _ = _mock_firestore(mocker, meals=[])
    mocker.patch("app.services.weekly_report_aggregation.get_firestore", return_value=client)

    response = asyncio.run(
        get_weekly_report(
            "user-1",
            week_end="2026-03-15",
            now=datetime(2026, 3, 21, 8, 0, tzinfo=UTC),
        )
    )

    assert response.status == "insufficient_data"
    assert response.insights == []
    assert response.priorities == []
