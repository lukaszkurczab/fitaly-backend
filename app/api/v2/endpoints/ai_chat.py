"""Canonical AI Chat v2 endpoint surface."""

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.api.v2.deps import get_chat_orchestrator
from app.core.config import settings
from app.core.errors import DomainError
from app.domain.chat.orchestrator import ChatOrchestrator
from app.schemas.ai_chat.request import ChatRunRequestDto
from app.schemas.ai_chat.response import ChatRunResponseDto

router = APIRouter(prefix="/ai/chat", tags=["AI Chat V2"])


@router.post("/runs", response_model=ChatRunResponseDto)
async def create_chat_run(
    payload: ChatRunRequestDto,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
    orchestrator: ChatOrchestrator = Depends(get_chat_orchestrator),
) -> ChatRunResponseDto:
    """Create one backend-owned AI Chat v2 run.

    Error contract:
    - kill switch returns `503 detail = {"code": "AI_CHAT_DISABLED", "message"}`.
    - domain failures are returned as `detail = {"code", "message"}`.
    - unexpected failures are mapped to `ai_chat_v2_internal_error`.
    """
    if not settings.AI_CHAT_ENABLED:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "AI_CHAT_DISABLED",
                "message": "AI Chat v2 is temporarily disabled.",
            },
        )

    try:
        return await orchestrator.run(
            user_id=current_user.uid,
            request=payload,
        )
    except DomainError as exc:
        detail_message = str(exc).strip() or exc.code
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": detail_message},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail={
                "code": "ai_chat_v2_internal_error",
                "message": "AI Chat v2 run failed.",
            },
        ) from exc
