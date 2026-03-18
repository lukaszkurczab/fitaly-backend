from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.ai_credits import CreditCosts


AiPersistence = Literal["backend_owned"]

BACKEND_OWNED_PERSISTENCE: AiPersistence = "backend_owned"


class BaseAiResponse(BaseModel):
    balance: int
    allocation: int
    tier: Literal["free", "premium"]
    periodStartAt: datetime
    periodEndAt: datetime
    costs: CreditCosts
    version: str
    persistence: AiPersistence
    model: str | None = None
    runId: str | None = None
    confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)
