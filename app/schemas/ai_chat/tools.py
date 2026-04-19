from typing import List, Literal, Optional
from pydantic import BaseModel, Field

class ResolvedScopeDto(BaseModel):
    type: Literal["today", "yesterday", "calendar_week", "rolling_7d", "date_range"]
    start_date: str = Field(alias="startDate")
    end_date: str = Field(alias="endDate")
    timezone: str
    is_partial: bool = Field(alias="isPartial")

class ProfileSummaryDto(BaseModel):
    goal: Optional[str] = None
    activity_level: Optional[str] = Field(default=None, alias="activityLevel")
    preferences: List[str] = Field(default_factory=list)
    allergies: List[str] = Field(default_factory=list)
    language: str = "pl"

class GoalContextDto(BaseModel):
    goal: Optional[str] = None
    calorie_target: Optional[int] = Field(default=None, alias="calorieTarget")
    protein_strategy: Optional[str] = Field(default=None, alias="proteinStrategy")

class LoggingCoverageDto(BaseModel):
    days_in_period: int = Field(alias="daysInPeriod")
    days_with_entries: int = Field(alias="daysWithEntries")
    meal_count: int = Field(alias="mealCount")
    coverage_level: Literal["none", "low", "medium", "high"] = Field(alias="coverageLevel")

class DailyBreakdownItemDto(BaseModel):
    date: str
    meal_count: int = Field(alias="mealCount")
    kcal: float
    protein_g: float = Field(alias="proteinG")
    fat_g: float = Field(alias="fatG")
    carbs_g: float = Field(alias="carbsG")

class ReliabilityDto(BaseModel):
    summary_confidence: Literal["low", "medium", "high"] = Field(alias="summaryConfidence")
    reason: str

class NutritionPeriodSummaryDto(BaseModel):
    period: ResolvedScopeDto
    logging_coverage: LoggingCoverageDto = Field(alias="loggingCoverage")
    totals: dict[str, float]
    daily_breakdown: List[DailyBreakdownItemDto] = Field(alias="dailyBreakdown")
    signals: List[str]
    reliability: ReliabilityDto

class MealLoggingQualityDto(BaseModel):
    coverage_level: Literal["none", "low", "medium", "high"] = Field(alias="coverageLevel")
    days_with_entries: int = Field(alias="daysWithEntries")
    missing_days: int = Field(alias="missingDays")
    can_support_trend_analysis: bool = Field(alias="canSupportTrendAnalysis")

class AppHelpContextDto(BaseModel):
    topic: str
    answer_facts: List[str] = Field(alias="answerFacts")


class DeltaValueDto(BaseModel):
    absolute: float
    percentage: Optional[float] = None


class CoverageGuardDto(BaseModel):
    comparable: bool
    reason: str


class ComparePeriodsResultDto(BaseModel):
    current_period: NutritionPeriodSummaryDto = Field(alias="currentPeriod")
    previous_period: NutritionPeriodSummaryDto = Field(alias="previousPeriod")
    coverage_guard: CoverageGuardDto = Field(alias="coverageGuard")
    delta: dict[str, DeltaValueDto]


class RecentTurnDto(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class RecentChatSummaryDto(BaseModel):
    summary: Optional[str] = None
    resolved_facts: List[str] = Field(default_factory=list, alias="resolvedFacts")
    last_turns: List[RecentTurnDto] = Field(default_factory=list, alias="lastTurns")
    has_summary: bool = Field(alias="hasSummary")
    source: Literal["memory_summary", "recent_turns_fallback"]
