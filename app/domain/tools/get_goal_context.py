from typing import Any

from app.domain.tools.base import DomainTool
from app.domain.users.services.user_profile_service import UserProfileService
from app.schemas.ai_chat.tools import GoalContextDto


class GetGoalContextTool(DomainTool):
    name = "get_goal_context"

    def __init__(self, user_profile_service: UserProfileService) -> None:
        self.user_profile_service = user_profile_service

    async def execute(self, *, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        del args
        result = await self.user_profile_service.get_goal_context(user_id=user_id)
        dto = GoalContextDto.model_validate(result)
        return dto.model_dump(by_alias=True)
