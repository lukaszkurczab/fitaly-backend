from typing import Optional, List, Literal
from pydantic import BaseModel, Field

class AssistantMessageDto(BaseModel):
    id: str
    content: str

class UsageDto(BaseModel):
    prompt_tokens: int = Field(alias="promptTokens")
    completion_tokens: int = Field(alias="completionTokens")
    total_tokens: int = Field(alias="totalTokens")

class ContextStatsDto(BaseModel):
    planner_used: bool = Field(alias="plannerUsed")
    used_summary: bool = Field(alias="usedSummary")
    history_turns: int = Field(alias="historyTurns")
    tools_used: List[str] = Field(alias="toolsUsed")
    truncated: bool
    scope_resolved: Optional[str] = Field(default=None, alias="scopeResolved")

class CreditsDto(BaseModel):
    balance: int
    tier: Literal["free", "premium"]

class ChatRunResponseDto(BaseModel):
    run_id: str = Field(alias="runId")
    thread_id: str = Field(alias="threadId")
    assistant_message_id: str = Field(alias="assistantMessageId")
    reply: str
    usage: UsageDto
    context_stats: ContextStatsDto = Field(alias="contextStats")
    credits: Optional[CreditsDto] = None