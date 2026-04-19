from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.ai_common import BaseAiResponse


class AiAskRequest(BaseModel):
    threadId: str = Field(min_length=1, max_length=160)
    message: str
    clientMessageId: str = Field(min_length=1, max_length=160)
    language: Literal["pl", "en"] | None = None


class AiAskUsage(BaseModel):
    promptTokens: int | None = None
    completionTokens: int | None = None
    totalTokens: int | None = None


class AiAskContextStats(BaseModel):
    usedSummary: bool
    historyTurns: int
    truncated: bool
    scopeDecision: Literal["ALLOW_APP", "ALLOW_USER_DATA", "ALLOW_NUTRITION", "DENY_OTHER"]


class AiAskResponse(BaseAiResponse):
    reply: str
    threadId: str
    assistantMessageId: str
    usage: AiAskUsage
    contextStats: AiAskContextStats
    scopeDecision: Literal["ALLOW_APP", "ALLOW_USER_DATA", "ALLOW_NUTRITION", "DENY_OTHER"]
