from datetime import datetime, timezone

from app.domain.chat_memory.models.memory_summary import MemorySummary
from app.infra.firestore.mappers.chat_mapper import (
    summary_from_document,
    summary_to_document,
)
from app.infra.firestore.repositories.memory_summary_repository import (
    DEFAULT_MEMORY_DOC_ID,
    MemorySummaryRepository,
)


def _utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class SummaryService:
    def __init__(self, memory_repository: MemorySummaryRepository) -> None:
        self._memory_repository = memory_repository

    async def get_current_summary(
        self,
        *,
        user_id: str,
        thread_id: str,
    ) -> MemorySummary | None:
        payload = await self._memory_repository.get(
            user_id=user_id,
            thread_id=thread_id,
            doc_id=DEFAULT_MEMORY_DOC_ID,
        )
        if payload is None:
            return None
        return summary_from_document(user_id=user_id, thread_id=thread_id, data=payload)

    async def upsert_summary(
        self,
        *,
        user_id: str,
        thread_id: str,
        summary: str,
        resolved_facts: list[str],
        covered_until_message_id: str | None,
        summary_model: str | None,
        version: int = 1,
    ) -> MemorySummary:
        existing = await self.get_current_summary(user_id=user_id, thread_id=thread_id)
        now = _utc_now_ms()
        created_at = existing.created_at if existing is not None else now
        memory_summary = MemorySummary(
            user_id=user_id,
            thread_id=thread_id,
            summary=summary,
            resolved_facts=resolved_facts,
            covered_until_message_id=covered_until_message_id,
            version=version,
            summary_model=summary_model,
            created_at=created_at,
            updated_at=now,
        )
        await self._memory_repository.upsert(
            user_id=user_id,
            thread_id=thread_id,
            payload=summary_to_document(memory_summary),
            doc_id=DEFAULT_MEMORY_DOC_ID,
        )
        return memory_summary

    async def maybe_refresh_summary(
        self,
        *,
        user_id: str,
        thread_id: str,
        recent_turns: list[dict[str, str]],
        user_message: str,
        assistant_message: str,
        previous_summary: MemorySummary | None,
        covered_until_message_id: str | None = None,
    ) -> MemorySummary:
        del recent_turns
        if previous_summary is not None:
            summary_text = previous_summary.summary
        else:
            summary_text = "none"

        appended = f"user:{user_message} | assistant:{assistant_message}"
        if summary_text == "none":
            merged_summary = appended
        else:
            merged_summary = f"{summary_text} | {appended}"

        if len(merged_summary) > 1200:
            merged_summary = f"{merged_summary[:1199].rstrip()}…"

        return await self.upsert_summary(
            user_id=user_id,
            thread_id=thread_id,
            summary=merged_summary,
            resolved_facts=previous_summary.resolved_facts if previous_summary else [],
            covered_until_message_id=(
                covered_until_message_id
                or (previous_summary.covered_until_message_id if previous_summary else None)
            ),
            summary_model=previous_summary.summary_model if previous_summary else None,
            version=previous_summary.version if previous_summary else 1,
        )
