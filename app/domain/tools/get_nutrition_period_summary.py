from typing import Any

from app.domain.tools.base import DomainTool
from app.domain.meals.services.nutrition_summary_service import NutritionSummaryService
from app.schemas.ai_chat.tools import NutritionPeriodSummaryDto


class GetNutritionPeriodSummaryTool(DomainTool):
    name = "get_nutrition_period_summary"

    def __init__(self, nutrition_summary_service: NutritionSummaryService) -> None:
        self.nutrition_summary_service = nutrition_summary_service

    async def execute(self, *, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        result = await self.nutrition_summary_service.build_period_summary(
            user_id=user_id,
            start_date=str(args["startDate"]),
            end_date=str(args["endDate"]),
            timezone=str(args.get("timezone", "Europe/Warsaw")),
            period_type=str(args["type"]),
            is_partial=bool(args.get("isPartial")) if args.get("isPartial") is not None else None,
        )
        dto = NutritionPeriodSummaryDto.model_validate(result)
        return dto.model_dump(by_alias=True)
