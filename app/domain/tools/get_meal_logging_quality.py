from typing import Any

from app.domain.meals.services.nutrition_summary_service import NutritionSummaryService
from app.domain.tools.base import DomainTool
from app.schemas.ai_chat.tools import MealLoggingQualityDto


class GetMealLoggingQualityTool(DomainTool):
    name = "get_meal_logging_quality"

    def __init__(self, nutrition_summary_service: NutritionSummaryService) -> None:
        self.nutrition_summary_service = nutrition_summary_service

    async def execute(self, *, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        result = await self.nutrition_summary_service.build_logging_quality(
            user_id=user_id,
            start_date=str(args["startDate"]),
            end_date=str(args["endDate"]),
            timezone=str(args.get("timezone", "Europe/Warsaw")),
        )
        dto = MealLoggingQualityDto.model_validate(result)
        return dto.model_dump(by_alias=True)
