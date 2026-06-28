from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.meal import MealIngredient, MealTotals, MealType


KnownPatternCandidateType = Literal["repeated_meal_snapshot"]
KnownPatternCandidateState = Literal[
    "candidate",
    "shown",
    "declined",
    "edited",
    "expired",
    "unavailable",
    "suppressed",
    "converted_to_review",
]
KnownPatternConfidenceBucket = Literal["medium", "high"]
KnownPatternCountBucket = Literal["3_4", "5_plus"]
KnownPatternSourceType = Literal["meal_snapshot"]
KnownPatternReasonCode = Literal["repeated_meal_recent_distinct_days"]
KnownPatternSuggestedAction = Literal["open_review_draft"]
KnownPatternControlAction = Literal["shown", "declined"]
KnownPatternControlState = Literal["shown", "declined"]


class KnownPatternSourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sourceType: KnownPatternSourceType
    sourceHash: str = Field(min_length=12, max_length=64)


class KnownPatternExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    key: Literal["knownPattern.explanation.repeatedMealSnapshot"]
    reasonCode: KnownPatternReasonCode


class KnownPatternCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    candidateId: str = Field(min_length=12, max_length=64)
    candidateType: KnownPatternCandidateType
    subjectKeyHash: str = Field(min_length=12, max_length=64)
    state: KnownPatternCandidateState
    confidenceBucket: KnownPatternConfidenceBucket
    sourceCountBucket: KnownPatternCountBucket
    distinctDayCountBucket: KnownPatternCountBucket
    firstSeenAt: str = Field(min_length=1, max_length=64)
    lastSeenAt: str = Field(min_length=1, max_length=64)
    expiresAt: str = Field(min_length=1, max_length=64)
    sourceRefs: list[KnownPatternSourceRef] = Field(min_length=1, max_length=5)
    explanation: KnownPatternExplanation
    suggestedAction: KnownPatternSuggestedAction
    createdByRuleVersion: str = Field(min_length=1, max_length=64)


class KnownPatternCandidateQueryEcho(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ruleVersion: str = Field(min_length=1, max_length=64)
    minSourceCount: int = Field(ge=1)
    minDistinctDays: int = Field(ge=1)
    maxHistoryItems: int = Field(ge=1)
    returnedCandidates: int = Field(ge=0)


class KnownPatternCandidatesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[KnownPatternCandidate]
    queryEcho: KnownPatternCandidateQueryEcho


class KnownPatternCandidateControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    clientMutationId: str = Field(min_length=1, max_length=128)
    subjectKeyHash: str = Field(min_length=12, max_length=64)
    createdByRuleVersion: str = Field(min_length=1, max_length=64)
    action: KnownPatternControlAction


class KnownPatternReviewDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    clientMutationId: str = Field(min_length=1, max_length=128)
    subjectKeyHash: str = Field(min_length=12, max_length=64)
    createdByRuleVersion: str = Field(min_length=1, max_length=64)


class KnownPatternCandidateControl(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    controlId: str = Field(min_length=12, max_length=64)
    candidateId: str = Field(min_length=12, max_length=64)
    subjectKeyHash: str = Field(min_length=12, max_length=64)
    state: KnownPatternControlState
    createdByRuleVersion: str = Field(min_length=1, max_length=64)
    expiresAt: str = Field(min_length=1, max_length=64)
    createdAt: str = Field(min_length=1, max_length=64)
    updatedAt: str = Field(min_length=1, max_length=64)


class KnownPatternCandidateControlResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    control: KnownPatternCandidateControl
    updated: bool


class KnownPatternReviewDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str | None = Field(default=None, max_length=240)
    type: MealType
    ingredients: list[MealIngredient] = Field(default_factory=list, max_length=50)
    totals: MealTotals
    notes: None = None
    tags: list[str] = Field(default_factory=list, max_length=20)


class KnownPatternReviewDraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    draft: KnownPatternReviewDraft
    control: KnownPatternCandidateControl
    updated: bool
