from typing import Literal

from pydantic import BaseModel, Field


class MessageTurnDto(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ThreadMemoryDto(BaseModel):
    last_turns: list[MessageTurnDto] = Field(alias="lastTurns")
    resolved_facts: list[str] = Field(alias="resolvedFacts")


class MemorySummaryDto(BaseModel):
    summary: str
    resolved_facts: list[str] = Field(alias="resolvedFacts")
    covered_until_message_id: str | None = Field(
        default=None, alias="coveredUntilMessageId"
    )
    version: int
    summary_model: str | None = Field(default=None, alias="summaryModel")
