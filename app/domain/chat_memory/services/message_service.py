from datetime import datetime, timezone
from typing import Literal, cast
from uuid import uuid4

from app.domain.chat_memory.models.chat_message import ChatMessage, MessageStatus
from app.domain.chat_memory.services.thread_service import ThreadService
from app.infra.firestore.mappers.chat_mapper import (
    message_from_document,
    message_to_document,
)
from app.infra.firestore.repositories.chat_message_repository import ChatMessageRepository


def _utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _normalize_language(language: str) -> Literal["pl", "en"]:
    return "en" if language == "en" else "pl"


def _normalize_status(status: str) -> MessageStatus:
    if status in {"accepted", "completed", "failed"}:
        return cast(MessageStatus, status)
    return "completed"


class MessageService:
    def __init__(
        self,
        message_repository: ChatMessageRepository,
        thread_service: ThreadService,
    ) -> None:
        self._message_repository = message_repository
        self._thread_service = thread_service

    async def find_by_client_message_id(
        self,
        *,
        user_id: str,
        thread_id: str,
        client_message_id: str,
    ) -> ChatMessage | None:
        lookup = await self._message_repository.find_by_client_message_id(
            user_id=user_id,
            thread_id=thread_id,
            client_message_id=client_message_id,
        )
        if lookup is None:
            return None
        message_id, payload = lookup
        return message_from_document(
            user_id=user_id,
            thread_id=thread_id,
            message_id=message_id,
            data=payload,
        )

    async def create_user_message(
        self,
        *,
        user_id: str,
        thread_id: str,
        run_id: str,
        client_message_id: str,
        content: str,
        language: str,
    ) -> ChatMessage:
        existing = await self.find_by_client_message_id(
            user_id=user_id,
            thread_id=thread_id,
            client_message_id=client_message_id,
        )
        if existing is not None:
            return existing

        now = _utc_now_ms()
        message = ChatMessage(
            id=f"msg_{uuid4().hex}",
            user_id=user_id,
            thread_id=thread_id,
            role="user",
            content=content,
            status="accepted",
            run_id=run_id,
            client_message_id=client_message_id,
            language=_normalize_language(language),
            deleted=False,
            created_at=now,
            updated_at=now,
            last_synced_at=now,
        )
        await self._message_repository.create(
            user_id=user_id,
            thread_id=thread_id,
            message_id=message.id,
            payload=message_to_document(message),
        )
        await self._thread_service.touch_with_message(
            user_id=user_id,
            thread_id=thread_id,
            message_content=content,
            message_created_at=now,
            title=content[:42].strip() if content.strip() else None,
        )
        return message

    async def create_assistant_message(
        self,
        *,
        user_id: str,
        thread_id: str,
        run_id: str,
        content: str,
        status: str = "completed",
    ) -> ChatMessage:
        now = _utc_now_ms()
        message = ChatMessage(
            id=f"msg_{uuid4().hex}",
            user_id=user_id,
            thread_id=thread_id,
            role="assistant",
            content=content,
            status=_normalize_status(status),
            run_id=run_id,
            client_message_id=None,
            language=None,
            deleted=False,
            created_at=now,
            updated_at=now,
            last_synced_at=now,
        )
        await self._message_repository.create(
            user_id=user_id,
            thread_id=thread_id,
            message_id=message.id,
            payload=message_to_document(message),
        )
        await self._thread_service.touch_with_message(
            user_id=user_id,
            thread_id=thread_id,
            message_content=content,
            message_created_at=now,
        )
        return message

    async def get_recent_turns(
        self,
        *,
        user_id: str,
        thread_id: str,
        limit: int = 6,
    ) -> list[dict[str, str]]:
        items = await self._message_repository.list_recent(
            user_id=user_id,
            thread_id=thread_id,
            limit=limit,
        )
        messages: list[ChatMessage] = []
        for message_id, payload in items:
            message = message_from_document(
                user_id=user_id,
                thread_id=thread_id,
                message_id=message_id,
                data=payload,
            )
            if message.deleted:
                continue
            messages.append(message)

        messages.sort(
            key=lambda item: (
                item.created_at,
                0 if item.role == "user" else 1,
                item.id,
            )
        )
        return [{"role": msg.role, "content": msg.content} for msg in messages]

    async def get_assistant_message_by_run_id(
        self,
        *,
        user_id: str,
        thread_id: str,
        run_id: str,
    ) -> ChatMessage | None:
        rows = await self._message_repository.list_by_run_id(
            user_id=user_id,
            thread_id=thread_id,
            run_id=run_id,
            limit=8,
        )
        candidates: list[ChatMessage] = []
        for message_id, payload in rows:
            message = message_from_document(
                user_id=user_id,
                thread_id=thread_id,
                message_id=message_id,
                data=payload,
            )
            if message.deleted:
                continue
            if message.role != "assistant":
                continue
            candidates.append(message)

        if not candidates:
            return None
        candidates.sort(key=lambda item: (item.created_at, item.updated_at, item.id))
        return candidates[-1]
