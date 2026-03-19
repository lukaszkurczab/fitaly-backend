from typing import Literal

from pydantic import BaseModel, Field


TopRisk = Literal[
    "none",
    "under_logging",
    "low_protein_consistency",
    "high_unknown_meal_details",
    "calorie_under_target",
]

CoachPriority = Literal[
    "maintain",
    "logging_foundation",
    "protein_consistency",
    "meal_detail_quality",
    "calorie_adherence",
]


class HabitWindowDays(BaseModel):
    recentActivity: int = 7
    adherence: int = 14
    consistency: int = 28


class MealTypeCoverage14(BaseModel):
    breakfast: bool = False
    lunch: bool = False
    dinner: bool = False
    snack: bool = False
    other: bool = False
    coveredCount: int = Field(default=0, ge=0, le=5)


class MealTypeFrequency14(BaseModel):
    breakfast: int = Field(default=0, ge=0, le=14)
    lunch: int = Field(default=0, ge=0, le=14)
    dinner: int = Field(default=0, ge=0, le=14)
    snack: int = Field(default=0, ge=0, le=14)
    other: int = Field(default=0, ge=0, le=14)


class DayCoverage14(BaseModel):
    loggedDays: int = Field(default=0, ge=0, le=14)
    validLoggedDays: int = Field(default=0, ge=0, le=14)


class ProteinDaysHit14(BaseModel):
    hitDays: int = Field(default=0, ge=0)
    eligibleDays: int = Field(default=0, ge=0)
    unknownDays: int = Field(default=0, ge=0)
    ratio: float | None = Field(default=None, ge=0, le=1)


class HabitTimingPatterns14(BaseModel):
    available: bool = False
    observedDays: int = Field(default=0, ge=0, le=14)
    firstMealMedianHour: float | None = Field(default=None, ge=0, le=24)
    lastMealMedianHour: float | None = Field(default=None, ge=0, le=24)
    eatingWindowHoursMedian: float | None = Field(default=None, ge=0, le=24)
    breakfastMedianHour: float | None = Field(default=None, ge=0, le=24)
    lunchMedianHour: float | None = Field(default=None, ge=0, le=24)
    dinnerMedianHour: float | None = Field(default=None, ge=0, le=24)
    snackMedianHour: float | None = Field(default=None, ge=0, le=24)
    otherMedianHour: float | None = Field(default=None, ge=0, le=24)


class HabitBehavior(BaseModel):
    loggingDays7: int = Field(ge=0, le=7)
    validLoggingDays7: int = Field(default=0, ge=0, le=7)
    loggingConsistency28: float = Field(ge=0, le=1)
    validLoggingConsistency28: float = Field(default=0, ge=0, le=1)
    avgMealsPerLoggedDay14: float = Field(ge=0)
    avgValidMealsPerValidLoggedDay14: float = Field(default=0, ge=0)
    mealTypeCoverage14: MealTypeCoverage14
    mealTypeFrequency14: MealTypeFrequency14
    dayCoverage14: DayCoverage14
    kcalAdherence14: float | None = Field(default=None, ge=0)
    kcalUnderTargetRatio14: float | None = Field(default=None, ge=0, le=1)
    proteinDaysHit14: ProteinDaysHit14
    timingPatterns14: HabitTimingPatterns14 = Field(default_factory=HabitTimingPatterns14)


class HabitDataQuality(BaseModel):
    daysWithUnknownMealDetails14: int = Field(default=0, ge=0, le=14)
    daysUsingTimestampDayFallback14: int = Field(default=0, ge=0, le=14)
    daysUsingTimestampTimingFallback14: int = Field(default=0, ge=0, le=14)


class HabitSignalsResponse(BaseModel):
    computedAt: str
    windowDays: HabitWindowDays = Field(default_factory=HabitWindowDays)
    behavior: HabitBehavior
    dataQuality: HabitDataQuality
    topRisk: TopRisk
    coachPriority: CoachPriority
