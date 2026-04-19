from app.domain.meals.models.meal_record import MealRecord
from app.domain.meals.services.nutrition_summary_service import NutritionSummaryService
from app.domain.tools.get_meal_logging_quality import GetMealLoggingQualityTool
from app.domain.tools.get_nutrition_period_summary import GetNutritionPeriodSummaryTool


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


async def test_get_nutrition_period_summary_handles_low_coverage_without_guessing() -> None:
    meals = [
        MealRecord(
            id="meal-1",
            day_key="2026-04-14",
            timestamp="2026-04-14T12:00:00Z",
            kcal=640.0,
            protein_g=30.0,
            fat_g=20.0,
            carbs_g=80.0,
        )
    ]
    service = NutritionSummaryService(_FakeMealQueryService(meals))  # type: ignore[arg-type]
    tool = GetNutritionPeriodSummaryTool(service)

    result = await tool.execute(
        user_id="user-1",
        args={
            "type": "calendar_week",
            "startDate": "2026-04-13",
            "endDate": "2026-04-19",
            "timezone": "Europe/Warsaw",
            "isPartial": True,
        },
    )

    assert result["loggingCoverage"]["daysInPeriod"] == 7
    assert result["loggingCoverage"]["daysWithEntries"] == 1
    assert result["loggingCoverage"]["coverageLevel"] == "low"
    assert result["reliability"]["summaryConfidence"] == "low"
    assert result["signals"] == ["logging_sparse"]


async def test_get_nutrition_period_summary_and_logging_quality_for_medium_coverage() -> None:
    meals = [
        MealRecord(
            id="meal-1",
            day_key="2026-04-13",
            timestamp="2026-04-13T12:00:00Z",
            kcal=1200.0,
            protein_g=62.0,
            fat_g=35.0,
            carbs_g=140.0,
        ),
        MealRecord(
            id="meal-2",
            day_key="2026-04-14",
            timestamp="2026-04-14T12:00:00Z",
            kcal=1300.0,
            protein_g=64.0,
            fat_g=30.0,
            carbs_g=150.0,
        ),
        MealRecord(
            id="meal-3",
            day_key="2026-04-16",
            timestamp="2026-04-16T12:00:00Z",
            kcal=1250.0,
            protein_g=70.0,
            fat_g=28.0,
            carbs_g=145.0,
        ),
    ]
    service = NutritionSummaryService(_FakeMealQueryService(meals))  # type: ignore[arg-type]
    summary_tool = GetNutritionPeriodSummaryTool(service)
    quality_tool = GetMealLoggingQualityTool(service)

    summary = await summary_tool.execute(
        user_id="user-1",
        args={
            "type": "rolling_7d",
            "startDate": "2026-04-13",
            "endDate": "2026-04-19",
            "timezone": "Europe/Warsaw",
            "isPartial": True,
        },
    )
    quality = await quality_tool.execute(
        user_id="user-1",
        args={
            "startDate": "2026-04-13",
            "endDate": "2026-04-19",
            "timezone": "Europe/Warsaw",
        },
    )

    assert summary["loggingCoverage"]["coverageLevel"] == "medium"
    assert summary["reliability"]["summaryConfidence"] == "medium"
    assert "logging_partial" in summary["signals"]
    assert quality["coverageLevel"] == "medium"
    assert quality["canSupportTrendAnalysis"] is True
