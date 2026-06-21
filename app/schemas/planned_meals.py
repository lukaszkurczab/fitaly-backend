from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.meal import MealIngredient, MealTotals, MealType


PlannedMealSourceType = Literal[
    "manual",
    "saved_meal",
    "recipe",
    "ingredient_product_draft",
]
PlannedMealTimeBucket = Literal["breakfast", "lunch", "dinner", "snack", "any"]
PlannedMealStatus = Literal[
    "planned",
    "edited",
    "rescheduled",
    "deleted",
    "expired",
    "source_unavailable",
    "converted_to_review",
]
PlannedMealEstimateState = Literal["known", "partial", "unknown"]
PlannedMealNutritionField = Literal["kcal", "protein", "fat", "carbs"]
PlannedMealConfidence = Literal["low", "medium", "high"]
PlannedMealUpdateStatus = Literal[
    "planned",
    "edited",
    "rescheduled",
    "expired",
    "source_unavailable",
]


class PlannedMealSourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sourceId: str = Field(min_length=1, max_length=128)
    sourceVersion: int | None = Field(default=None, ge=1)
    snapshotName: str | None = Field(default=None, max_length=240)


class PlannedMealDraftSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str | None = Field(default=None, max_length=240)
    type: MealType = "other"
    ingredients: list[MealIngredient] = Field(default_factory=list, max_length=50)
    totals: MealTotals | None = None
    notes: str | None = Field(default=None, max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=20)


class PlannedMealNutritionEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    state: PlannedMealEstimateState
    totals: MealTotals | None = None
    missingFields: list[PlannedMealNutritionField] = Field(default_factory=list)
    confidence: PlannedMealConfidence | None = None

    @model_validator(mode="after")
    def validate_estimate_state(self) -> Self:
        if self.state == "known" and (self.totals is None or self.missingFields):
            raise ValueError("Known planned meal estimates require complete totals")
        if self.state == "unknown" and self.totals is not None:
            raise ValueError("Unknown planned meal estimates must not include totals")
        return self


class PlannedMealItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    plannedMealId: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    dateBucket: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    timeBucket: PlannedMealTimeBucket | None = None
    sourceType: PlannedMealSourceType
    sourceRef: PlannedMealSourceRef | None = None
    draftSnapshot: PlannedMealDraftSnapshot
    nutritionEstimate: PlannedMealNutritionEstimate
    status: PlannedMealStatus
    linkedMealId: str | None = Field(default=None, min_length=1)
    convertedAt: str | None = Field(default=None, min_length=1, max_length=64)
    conversionClientMutationId: str | None = Field(
        default=None,
        min_length=1,
    )
    createdAt: str = Field(min_length=1, max_length=64)
    updatedAt: str = Field(min_length=1, max_length=64)


class PlannedMealCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    clientMutationId: str = Field(min_length=1, max_length=128)
    plannedMealId: str = Field(min_length=1, max_length=128)
    dateBucket: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    timeBucket: PlannedMealTimeBucket | None = None
    sourceType: PlannedMealSourceType
    sourceRef: PlannedMealSourceRef | None = None
    draftSnapshot: PlannedMealDraftSnapshot
    nutritionEstimate: PlannedMealNutritionEstimate


class PlannedMealUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    clientMutationId: str = Field(min_length=1, max_length=128)
    expectedVersion: int = Field(ge=1)
    dateBucket: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    timeBucket: PlannedMealTimeBucket | None = None
    sourceType: PlannedMealSourceType | None = None
    sourceRef: PlannedMealSourceRef | None = None
    draftSnapshot: PlannedMealDraftSnapshot | None = None
    nutritionEstimate: PlannedMealNutritionEstimate | None = None
    status: PlannedMealUpdateStatus | None = None

    @model_validator(mode="after")
    def validate_update_payload(self) -> Self:
        update_fields = self.model_fields_set - {"clientMutationId", "expectedVersion"}
        if not update_fields:
            raise ValueError("Planned meal update must include at least one change")
        non_nullable_update_fields = {
            "dateBucket",
            "sourceType",
            "draftSnapshot",
            "nutritionEstimate",
            "status",
        }
        for field_name in non_nullable_update_fields & self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class PlannedMealDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    clientMutationId: str = Field(min_length=1, max_length=128)
    expectedVersion: int = Field(ge=1)


class PlannedMealsListQueryEcho(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    startDate: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    days: int = Field(ge=1, le=3)
    includeDeleted: bool
    returnedItems: int = Field(ge=0)


class PlannedMealsListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[PlannedMealItem]
    queryEcho: PlannedMealsListQueryEcho


class PlannedMealMutationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    item: PlannedMealItem
    updated: bool
