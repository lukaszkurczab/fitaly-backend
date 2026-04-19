from datetime import datetime, timezone

from app.domain.chat_memory.models.chat_thread import ChatThread
from app.infra.firestore.mappers.chat_mapper import (
    thread_from_document,
    thread_to_document,
)
from app.infra.firestore.repositories.chat_thread_repository import ChatThreadRepository


def _utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class ThreadService:
    def __init__(self, thread_repository: ChatThreadRepository) -> None:
        self._thread_repository = thread_repository

    async def get_thread(self, *, user_id: str, thread_id: str) -> ChatThread | None:
        payload = await self._thread_repository.get(user_id=user_id, thread_id=thread_id)
        if payload is None:
            return None
        return thread_from_document(user_id=user_id, thread_id=thread_id, data=payload)

    async def ensure_thread(
        self,
        *,
        user_id: str,
        thread_id: str,
        title: str | None = None,
    ) -> ChatThread:
        existing = await self.get_thread(user_id=user_id, thread_id=thread_id)
        if existing is not None:
            if title and not existing.title:
                existing.title = title
                existing.updated_at = _utc_now_ms()
                await self._thread_repository.upsert(
                    user_id=user_id,
                    thread_id=thread_id,
                    payload=thread_to_document(existing),
                    merge=True,
                )
            return existing

        now = _utc_now_ms()
        thread = ChatThread(
            id=thread_id,
            user_id=user_id,
            title=title or "",
            status="active",
            created_at=now,
            updated_at=now,
            last_message=None,
            last_message_at=None,
        )
        await self._thread_repository.upsert(
            user_id=user_id,
            thread_id=thread_id,
            payload=thread_to_document(thread),
            merge=False,
        )
        return thread

    async def touch_with_message(
        self,
        *,
        user_id: str,
        thread_id: str,
        message_content: str,
        message_created_at: int,
        title: str | None = None,
    ) -> None:
        payload: dict[str, int | str | None] = {
            "updatedAt": message_created_at,
            "lastMessage": message_content,
            "lastMessageAt": message_created_at,
        }
        if title:
            payload["title"] = title
        existing = await self._thread_repository.get(user_id=user_id, thread_id=thread_id)
        if existing is None:
            payload["createdAt"] = message_created_at
            payload["status"] = "active"
        await self._thread_repository.upsert(
            user_id=user_id,
            thread_id=thread_id,
            payload=payload,
            merge=True,
        )
