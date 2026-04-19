from app.domain.meals.models.meal_record import MealRecord
from app.domain.meals.services.nutrition_summary_service import NutritionSummaryService
from app.domain.meals.services.period_comparison_service import PeriodComparisonService
from app.domain.tools.compare_periods import ComparePeriodsTool


class _FakeMealQueryService:
    def __init__(self, meals: list[MealRecord]) -> None:
        self._meals = meals

    async def get_meals_in_range(
        self,
        *,
        user_id: str,
        start_date: str,
        end_date: str,
        timezone: str,
    ) -> list[MealRecord]:
        del user_id, timezone
        return [meal for meal in self._meals if start_date <= meal.day_key <= end_date]


async def test_compare_periods_returns_delta_and_coverage_guard() -> None:
    meals = [
        MealRecord(
            id="old-1",
            day_key="2026-04-01",
            timestamp="2026-04-01T12:00:00Z",
            kcal=1100,
            protein_g=50,
            fat_g=20,
            carbs_g=130,
        ),
        MealRecord(
            id="old-2",
            day_key="2026-04-03",
            timestamp="2026-04-03T12:00:00Z",
            kcal=1150,
            protein_g=55,
            fat_g=22,
            carbs_g=140,
        ),
        MealRecord(
            id="new-1",
            day_key="2026-04-08",
            timestamp="2026-04-08T12:00:00Z",
            kcal=1300,
            protein_g=70,
            fat_g=30,
            carbs_g=150,
        ),
        MealRecord(
            id="new-2",
            day_key="2026-04-09",
            timestamp="2026-04-09T12:00:00Z",
            kcal=1400,
            protein_g=75,
            fat_g=32,
            carbs_g=165,
        ),
        MealRecord(
            id="new-3",
            day_key="2026-04-10",
            timestamp="2026-04-10T12:00:00Z",
            kcal=1350,
            protein_g=72,
            fat_g=29,
            carbs_g=158,
        ),
    ]

    summary_service = NutritionSummaryService(_FakeMealQueryService(meals))  # type: ignore[arg-type]
    comparison_service = PeriodComparisonService(summary_service)
    tool = ComparePeriodsTool(comparison_service)

    result = await tool.execute(
        user_id="user-1",
        args={
            "currentScope": {
                "type": "date_range",
                "startDate": "2026-04-08",
                "endDate": "2026-04-10",
                "timezone": "Europe/Warsaw",
                "isPartial": False,
            },
            "previousScope": {
                "type": "date_range",
                "startDate": "2026-04-01",
                "endDate": "2026-04-03",
                "timezone": "Europe/Warsaw",
                "isPartial": False,
            },
        },
    )

    assert result["coverageGuard"]["comparable"] is True
    assert result["delta"]["kcal"]["absolute"] > 0
    assert result["delta"]["proteinG"]["absolute"] > 0


async def test_compare_periods_marks_not_comparable_for_low_coverage() -> None:
    meals = [
        MealRecord(
            id="one",
            day_key="2026-04-10",
            timestamp="2026-04-10T12:00:00Z",
            kcal=800,
            protein_g=20,
            fat_g=10,
            carbs_g=90,
        )
    ]
    summary_service = NutritionSummaryService(_FakeMealQueryService(meals))  # type: ignore[arg-type]
    comparison_service = PeriodComparisonService(summary_service)
    tool = ComparePeriodsTool(comparison_service)

    result = await tool.execute(
        user_id="user-1",
        args={
            "currentScope": {
                "type": "date_range",
                "startDate": "2026-04-08",
                "endDate": "2026-04-14",
                "timezone": "Europe/Warsaw",
            },
            "previousScope": {
                "type": "date_range",
                "startDate": "2026-04-01",
                "endDate": "2026-04-07",
                "timezone": "Europe/Warsaw",
            },
        },
    )

    assert result["coverageGuard"] == {
        "comparable": False,
        "reason": "insufficient_logging_coverage",
    }
