from __future__ import annotations

from app.domain.meals.services.nutrition_summary_service import NutritionSummaryService


class PeriodComparisonService:
    def __init__(self, nutrition_summary_service: NutritionSummaryService) -> None:
        self.nutrition_summary_service = nutrition_summary_service

    @staticmethod
    def _delta(current: float, previous: float) -> dict[str, float | None]:
        absolute = round(current - previous, 2)
        percentage = None
        if previous != 0:
            percentage = round((absolute / previous) * 100.0, 2)
        return {"absolute": absolute, "percentage": percentage}

    async def compare(
        self,
        *,
        user_id: str,
        current_scope: dict,
        previous_scope: dict,
    ) -> dict:
        current = await self.nutrition_summary_service.build_period_summary(
            user_id=user_id,
            start_date=current_scope["startDate"],
            end_date=current_scope["endDate"],
            timezone=current_scope.get("timezone", "Europe/Warsaw"),
            period_type=current_scope.get("type", "date_range"),
            is_partial=bool(current_scope.get("isPartial", False)),
        )
        previous = await self.nutrition_summary_service.build_period_summary(
            user_id=user_id,
            start_date=previous_scope["startDate"],
            end_date=previous_scope["endDate"],
            timezone=previous_scope.get("timezone", "Europe/Warsaw"),
            period_type=previous_scope.get("type", "date_range"),
            is_partial=bool(previous_scope.get("isPartial", False)),
        )

        current_totals = current["totals"]
        previous_totals = previous["totals"]
        current_coverage = current["loggingCoverage"]["coverageLevel"]
        previous_coverage = previous["loggingCoverage"]["coverageLevel"]

        comparable = current_coverage in {"medium", "high"} and previous_coverage in {
            "medium",
            "high",
        }
        reason = "ok" if comparable else "insufficient_logging_coverage"

        return {
            "currentPeriod": current,
            "previousPeriod": previous,
            "coverageGuard": {
                "comparable": comparable,
                "reason": reason,
            },
            "delta": {
                "kcal": self._delta(
                    float(current_totals.get("kcal", 0.0)),
                    float(previous_totals.get("kcal", 0.0)),
                ),
                "proteinG": self._delta(
                    float(current_totals.get("proteinG", 0.0)),
                    float(previous_totals.get("proteinG", 0.0)),
                ),
                "fatG": self._delta(
                    float(current_totals.get("fatG", 0.0)),
                    float(previous_totals.get("fatG", 0.0)),
                ),
                "carbsG": self._delta(
                    float(current_totals.get("carbsG", 0.0)),
                    float(previous_totals.get("carbsG", 0.0)),
                ),
                "daysWithEntries": self._delta(
                    float(current["loggingCoverage"].get("daysWithEntries", 0)),
                    float(previous["loggingCoverage"].get("daysWithEntries", 0)),
                ),
                "mealCount": self._delta(
                    float(current["loggingCoverage"].get("mealCount", 0)),
                    float(previous["loggingCoverage"].get("mealCount", 0)),
                ),
            },
        }
