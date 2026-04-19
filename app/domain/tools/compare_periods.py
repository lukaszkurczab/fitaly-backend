from typing import Any, cast

from app.domain.meals.services.period_comparison_service import PeriodComparisonService
from app.domain.tools.base import DomainTool
from app.schemas.ai_chat.tools import ComparePeriodsResultDto


class ComparePeriodsTool(DomainTool):
    name = "compare_periods"

    def __init__(self, period_comparison_service: PeriodComparisonService) -> None:
        self.period_comparison_service = period_comparison_service

    async def execute(self, *, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        current_scope = args.get("currentScope") or args.get("scope")
        previous_scope = args.get("previousScope")
        if not isinstance(current_scope, dict) or not isinstance(previous_scope, dict):
            raise ValueError("compare_periods requires currentScope and previousScope")
        current_scope_map = cast(dict[str, Any], current_scope)
        previous_scope_map = cast(dict[str, Any], previous_scope)

        result = await self.period_comparison_service.compare(
            user_id=user_id,
            current_scope=current_scope_map,
            previous_scope=previous_scope_map,
        )
        dto = ComparePeriodsResultDto.model_validate(result)
        return dto.model_dump(by_alias=True)
