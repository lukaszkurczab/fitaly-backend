from typing import Any

from app.schemas.weekly_reports import WeeklyReportPeriod
from app.services.weekly_report_aggregation import build_weekly_aggregate_from_meals
from app.services.weekly_report_signals import derive_weekly_signals


def _meal(
    *,
    meal_id: str,
    day_key: str,
    timestamp: str,
    meal_type: str = "lunch",
    kcal: float = 500,
    protein: float = 25,
    ingredients: list[dict[str, Any]] | None = None,
    logged_at_local_min: int | None = None,
) -> dict[str, Any]:
    payload = {
        "mealId": meal_id,
        "cloudId": meal_id,
        "dayKey": day_key,
        "timestamp": timestamp,
        "type": meal_type,
        "deleted": False,
        "totals": {"kcal": kcal, "protein": protein, "carbs": 0, "fat": 0},
        "ingredients": ingredients
        if ingredients is not None
        else [
            {
                "id": f"{meal_id}-1",
                "name": "Ingredient",
                "amount": 100,
                "kcal": kcal,
                "protein": protein,
                "carbs": 0,
                "fat": 0,
            }
        ],
    }
    if logged_at_local_min is not None:
        payload["loggedAtLocalMin"] = logged_at_local_min
    return payload


def _period() -> WeeklyReportPeriod:
    return WeeklyReportPeriod(startDay="2026-03-09", endDay="2026-03-15")


def test_derive_weekly_signals_with_sufficient_data_is_deterministic() -> None:
    aggregate = build_weekly_aggregate_from_meals(
        period=_period(),
        meals=[
            _meal(
                meal_id="d1-breakfast",
                day_key="2026-03-09",
                timestamp="2026-03-09T07:00:00Z",
                meal_type="breakfast",
                kcal=300,
                protein=20,
                logged_at_local_min=8 * 60,
            ),
            _meal(
                meal_id="d1-lunch",
                day_key="2026-03-09",
                timestamp="2026-03-09T12:00:00Z",
                meal_type="lunch",
                kcal=600,
                protein=30,
                logged_at_local_min=13 * 60,
            ),
            _meal(
                meal_id="d1-dinner",
                day_key="2026-03-09",
                timestamp="2026-03-09T18:00:00Z",
                meal_type="dinner",
                kcal=700,
                protein=35,
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
                timestamp="2026-03-10T18:10:00Z",
                meal_type="dinner",
                logged_at_local_min=19 * 60 + 10,
            ),
            _meal(
                meal_id="d3-breakfast",
                day_key="2026-03-11",
                timestamp="2026-03-11T07:30:00Z",
                meal_type="breakfast",
                logged_at_local_min=8 * 60 + 30,
            ),
            _meal(
                meal_id="d3-dinner",
                day_key="2026-03-11",
                timestamp="2026-03-11T18:20:00Z",
                meal_type="dinner",
                logged_at_local_min=19 * 60 + 20,
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
                meal_id="d5-breakfast",
                day_key="2026-03-14",
                timestamp="2026-03-14T09:00:00Z",
                meal_type="breakfast",
                logged_at_local_min=10 * 60,
            ),
            _meal(
                meal_id="d5-lunch",
                day_key="2026-03-14",
                timestamp="2026-03-14T13:00:00Z",
                meal_type="lunch",
                logged_at_local_min=14 * 60,
            ),
            _meal(
                meal_id="d6-breakfast",
                day_key="2026-03-15",
                timestamp="2026-03-15T09:30:00Z",
                meal_type="breakfast",
                logged_at_local_min=10 * 60 + 30,
            ),
        ],
    )

    signals_a = derive_weekly_signals(aggregate)
    signals_b = derive_weekly_signals(aggregate)

    assert signals_a == signals_b
    assert signals_a.has_sufficient_data is True
    assert signals_a.consistency.valid_logged_days == 6
    assert signals_a.consistency.level == "strong"
    assert signals_a.logging_coverage.level == "high"
    assert signals_a.start_of_day_stability.available is True
    assert signals_a.start_of_day_stability.level == "variable"
    assert signals_a.day_completion_tendency.available is True
    assert signals_a.day_completion_tendency.complete_days == 3
    assert signals_a.weekend_drift.available is True
    assert signals_a.weekend_drift.pattern == "none"
    assert signals_a.improving_vs_previous_week.available is False


def test_derive_weekly_signals_marks_insufficient_data_for_sparse_week() -> None:
    aggregate = build_weekly_aggregate_from_meals(
        period=_period(),
        meals=[
            _meal(
                meal_id="d1",
                day_key="2026-03-09",
                timestamp="2026-03-09T12:00:00Z",
            ),
            _meal(
                meal_id="d2",
                day_key="2026-03-11",
                timestamp="2026-03-11T12:00:00Z",
            ),
        ],
    )

    signals = derive_weekly_signals(aggregate)

    assert signals.has_sufficient_data is False
    assert signals.sufficiency_reason == "too_few_valid_days"
    assert signals.consistency.level == "weak"
    assert signals.start_of_day_stability.available is False
    assert signals.day_completion_tendency.available is False
    assert signals.weekend_drift.available is False


def test_derive_weekly_signals_marks_three_valid_days_as_insufficient() -> None:
    aggregate = build_weekly_aggregate_from_meals(
        period=_period(),
        meals=[
            _meal(
                meal_id="d1",
                day_key="2026-03-09",
                timestamp="2026-03-09T12:00:00Z",
            ),
            _meal(
                meal_id="d2",
                day_key="2026-03-11",
                timestamp="2026-03-11T12:00:00Z",
            ),
            _meal(
                meal_id="d3",
                day_key="2026-03-13",
                timestamp="2026-03-13T12:00:00Z",
            ),
        ],
    )

    signals = derive_weekly_signals(aggregate)

    assert signals.has_sufficient_data is False
    assert signals.sufficiency_reason == "too_few_valid_days"


def test_derive_weekly_signals_handles_empty_data_without_crashing() -> None:
    aggregate = build_weekly_aggregate_from_meals(period=_period(), meals=[])

    signals = derive_weekly_signals(aggregate)

    assert signals.has_sufficient_data is False
    assert signals.logging_coverage.logged_days == 0
    assert signals.logging_coverage.valid_logged_days == 0
    assert signals.day_completion_tendency.completion_ratio is None
    assert signals.improving_vs_previous_week.available is False


def test_derive_weekly_signals_uses_previous_week_when_available() -> None:
    aggregate = build_weekly_aggregate_from_meals(
        period=_period(),
        meals=[
            _meal(
                meal_id="prev-1",
                day_key="2026-03-03",
                timestamp="2026-03-03T12:00:00Z",
            ),
            _meal(
                meal_id="prev-2",
                day_key="2026-03-05",
                timestamp="2026-03-05T12:00:00Z",
            ),
            _meal(
                meal_id="cur-1",
                day_key="2026-03-09",
                timestamp="2026-03-09T12:00:00Z",
            ),
            _meal(
                meal_id="cur-2",
                day_key="2026-03-10",
                timestamp="2026-03-10T12:00:00Z",
            ),
            _meal(
                meal_id="cur-3",
                day_key="2026-03-11",
                timestamp="2026-03-11T12:00:00Z",
            ),
            _meal(
                meal_id="cur-4",
                day_key="2026-03-12",
                timestamp="2026-03-12T12:00:00Z",
            ),
        ],
    )

    signals = derive_weekly_signals(aggregate)

    assert signals.improving_vs_previous_week.available is True
    assert signals.improving_vs_previous_week.previous_valid_logged_days == 2
    assert signals.improving_vs_previous_week.current_valid_logged_days == 4
    assert signals.improving_vs_previous_week.delta == 2
    assert signals.improving_vs_previous_week.direction == "improving"
