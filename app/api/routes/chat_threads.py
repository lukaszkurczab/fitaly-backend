"""AI Chat v2 thread projection endpoints.

These endpoints expose backend-owned AI Chat thread/message projections under
`/api/v2`. The canonical run lifecycle remains `/api/v2/ai/chat/runs`.
"""

from fastapi import APIRouter, Depends, Query

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.schemas.chat_thread import (
    ChatMessageItem,
    ChatMessagesPageResponse,
    ChatThreadItem,
    ChatThreadsPageResponse,
)
from app.services import chat_thread_service

router = APIRouter()


@router.get("/users/me/chat/threads", response_model=ChatThreadsPageResponse)
async def get_chat_threads_me(
    limit: int = Query(default=20, ge=1, le=100),
    beforeUpdatedAt: int | None = Query(default=None, ge=0),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> ChatThreadsPageResponse:
    items, next_before_updated_at = await chat_thread_service.list_threads(
        current_user.uid,
        limit_count=limit,
        before_updated_at=beforeUpdatedAt,
    )

    return ChatThreadsPageResponse(
        items=[ChatThreadItem.model_validate(item) for item in items],
        nextBeforeUpdatedAt=next_before_updated_at,
    )


@router.get(
    "/users/me/chat/threads/{threadId}/messages",
    response_model=ChatMessagesPageResponse,
)
async def get_chat_thread_messages_me(
    threadId: str,
    limit: int = Query(default=50, ge=1, le=200),
    beforeCreatedAt: int | None = Query(default=None, ge=0),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> ChatMessagesPageResponse:
    items, next_before_created_at = await chat_thread_service.list_messages(
        current_user.uid,
        threadId,
        limit_count=limit,
        before_created_at=beforeCreatedAt,
    )

    return ChatMessagesPageResponse(
        items=[ChatMessageItem.model_validate(item) for item in items],
        nextBeforeCreatedAt=next_before_created_at,
    )
