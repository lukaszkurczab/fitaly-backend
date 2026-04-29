from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

class UsageDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    prompt_tokens: int = Field(alias="promptTokens")
    completion_tokens: int = Field(alias="completionTokens")
    total_tokens: int = Field(alias="totalTokens")

class ContextStatsDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    used_summary: bool = Field(alias="usedSummary")
    history_turns: int = Field(alias="historyTurns")
    truncated: bool
    scope_decision: str = Field(alias="scopeDecision")

class CreditsDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    balance: int
    tier: Literal["free", "premium"]

class ChatRunResponseDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    run_id: str = Field(alias="runId")
    thread_id: str = Field(alias="threadId")
    client_message_id: str = Field(alias="clientMessageId")
    assistant_message_id: str = Field(alias="assistantMessageId")
    reply: str
    usage: UsageDto
    context_stats: ContextStatsDto = Field(alias="contextStats")
    credits: CreditsDto | None
    persistence: Literal["backend_owned"]
