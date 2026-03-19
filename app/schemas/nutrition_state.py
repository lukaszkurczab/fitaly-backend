from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.ai_credits import CreditCosts
from app.schemas.habits import (
    CoachPriority,
    DayCoverage14,
    HabitBehavior,
    HabitDataQuality,
    HabitTimingPatterns14,
    MealTypeCoverage14,
    MealTypeFrequency14,
    ProteinDaysHit14,
    TopRisk,
)


class NutritionTargets(BaseModel):
    kcal: float | None = None
    protein: float | None = None
    carbs: float | None = None
    fat: float | None = None


class NutritionConsumed(BaseModel):
    kcal: float = 0
    protein: float = 0
    carbs: float = 0
    fat: float = 0


class NutritionRemaining(BaseModel):
    kcal: float | None = None
    protein: float | None = None
    carbs: float | None = None
    fat: float | None = None


class NutritionOverTarget(BaseModel):
    kcal: float | None = None
    protein: float | None = None
    carbs: float | None = None
    fat: float | None = None


class NutritionQuality(BaseModel):
    mealsLogged: int = Field(default=0, ge=0)
    missingNutritionMeals: int = Field(default=0, ge=0)
    dataCompletenessScore: float = Field(default=0, ge=0, le=1)


class NutritionHabitsSummary(BaseModel):
    available: bool = False
    behavior: HabitBehavior = Field(
        default_factory=lambda: HabitBehavior(
            loggingDays7=0,
            validLoggingDays7=0,
            loggingConsistency28=0,
            validLoggingConsistency28=0,
            avgMealsPerLoggedDay14=0,
            avgValidMealsPerValidLoggedDay14=0,
            mealTypeCoverage14=MealTypeCoverage14(),
            mealTypeFrequency14=MealTypeFrequency14(),
            dayCoverage14=DayCoverage14(),
            kcalAdherence14=None,
            kcalUnderTargetRatio14=None,
            proteinDaysHit14=ProteinDaysHit14(),
            timingPatterns14=HabitTimingPatterns14(),
        )
    )
    dataQuality: HabitDataQuality = Field(default_factory=HabitDataQuality)
    topRisk: TopRisk = "none"
    coachPriority: CoachPriority = "maintain"


class NutritionStreakSummary(BaseModel):
    available: bool = False
    current: int = Field(default=0, ge=0)
    lastDate: str | None = None


class NutritionAiSummary(BaseModel):
    available: bool = False
    tier: Literal["free", "premium"] | None = None
    balance: int | None = Field(default=None, ge=0)
    allocation: int | None = Field(default=None, ge=0)
    usedThisPeriod: int | None = Field(default=None, ge=0)
    periodStartAt: str | None = None
    periodEndAt: str | None = None
    costs: CreditCosts = Field(
        default_factory=lambda: CreditCosts(chat=0, textMeal=0, photo=0)
    )


NutritionComponentState = Literal["ok", "disabled", "error"]


class NutritionComponentStatus(BaseModel):
    habits: NutritionComponentState = "disabled"
    streak: NutritionComponentState = "disabled"
    ai: NutritionComponentState = "disabled"


class NutritionStateMeta(BaseModel):
    isDegraded: bool = False
    componentStatus: NutritionComponentStatus = Field(
        default_factory=NutritionComponentStatus
    )


class NutritionStateResponse(BaseModel):
    computedAt: str
    dayKey: str
    targets: NutritionTargets
    consumed: NutritionConsumed
    remaining: NutritionRemaining
    overTarget: NutritionOverTarget
    quality: NutritionQuality
    habits: NutritionHabitsSummary
    streak: NutritionStreakSummary
    ai: NutritionAiSummary
    meta: NutritionStateMeta
