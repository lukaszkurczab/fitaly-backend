from typing import Literal
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.ai_credits import CreditCosts

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

    user_id: str = Field(alias="userId")
    balance: int
    allocation: int
    tier: Literal["free", "premium"]
    period_start_at: datetime = Field(alias="periodStartAt")
    period_end_at: datetime = Field(alias="periodEndAt")
    costs: CreditCosts
    renewal_anchor_source: str | None = Field(default=None, alias="renewalAnchorSource")
    revenue_cat_entitlement_id: str | None = Field(
        default=None,
        alias="revenueCatEntitlementId",
    )
    revenue_cat_expiration_at: datetime | None = Field(
        default=None,
        alias="revenueCatExpirationAt",
    )
    last_revenue_cat_event_id: str | None = Field(default=None, alias="lastRevenueCatEventId")

class ChatRunResponseDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    run_id: str = Field(alias="runId")
    thread_id: str = Field(alias="threadId")
    client_message_id: str = Field(alias="clientMessageId")
    assistant_message_id: str = Field(alias="assistantMessageId")
    reply: str
    usage: UsageDto
    context_stats: ContextStatsDto = Field(alias="contextStats")
    credits: CreditsDto
    persistence: Literal["backend_owned"]
