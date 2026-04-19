from typing import Any

from app.domain.chat_memory.services.message_service import MessageService
from app.domain.chat_memory.services.summary_service import SummaryService
from app.domain.tools.base import DomainTool
from app.schemas.ai_chat.tools import RecentChatSummaryDto


class GetRecentChatSummaryTool(DomainTool):
    name = "get_recent_chat_summary"

    def __init__(
        self,
        summary_service: SummaryService,
        message_service: MessageService,
    ) -> None:
        self.summary_service = summary_service
        self.message_service = message_service

    async def execute(self, *, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        thread_id = str(args.get("threadId") or "").strip()
        if not thread_id:
            raise ValueError("threadId is required")
        raw_limit = args.get("fallbackTurnsLimit", 6)
        fallback_limit = int(raw_limit) if isinstance(raw_limit, (int, float, str)) else 6
        fallback_limit = max(1, min(fallback_limit, 24))

        summary = await self.summary_service.get_current_summary(
            user_id=user_id,
            thread_id=thread_id,
        )
        if summary is not None:
            dto = RecentChatSummaryDto.model_validate(
                {
                    "summary": summary.summary,
                    "resolvedFacts": summary.resolved_facts,
                    "lastTurns": [],
                    "hasSummary": True,
                    "source": "memory_summary",
                }
            )
            return dto.model_dump(by_alias=True)

        turns = await self.message_service.get_recent_turns(
            user_id=user_id,
            thread_id=thread_id,
            limit=fallback_limit,
        )
        dto = RecentChatSummaryDto.model_validate(
            {
                "summary": None,
                "resolvedFacts": [],
                "lastTurns": turns,
                "hasSummary": False,
                "source": "recent_turns_fallback",
            }
        )
        return dto.model_dump(by_alias=True)
