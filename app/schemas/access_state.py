from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.ai_credits import AiCreditsStatus

AccessTier = Literal["free", "premium", "unknown"]
AccessEntitlementStatus = Literal["active", "inactive", "degraded", "unknown"]
AccessFeatureStatus = Literal["enabled", "disabled", "unknown"]
AccessFeatureReason = Literal[
    "insufficient_credits",
    "requires_premium",
    "degraded",
    "feature_disabled",
]


class AccessFeatureState(BaseModel):
    enabled: bool
    status: AccessFeatureStatus
    reason: AccessFeatureReason | None = None
    requiredCredits: int | None = Field(default=None, ge=0)
    remainingCredits: int | None = Field(default=None, ge=0)


class AccessFeatures(BaseModel):
    aiChat: AccessFeatureState
    photoAnalysis: AccessFeatureState
    textMealAnalysis: AccessFeatureState
    weeklyReport: AccessFeatureState
    fullHistory: AccessFeatureState
    cloudBackup: AccessFeatureState


class AccessStateResponse(BaseModel):
    tier: AccessTier
    entitlementStatus: AccessEntitlementStatus
    credits: AiCreditsStatus | None
    features: AccessFeatures
    refreshedAt: datetime
