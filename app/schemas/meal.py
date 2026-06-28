from datetime import datetime
import re
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MealType = Literal["breakfast", "lunch", "dinner", "snack", "other"]
MealSource = Literal["ai", "manual", "saved"] | None
MealSyncState = Literal["synced", "pending", "conflict", "failed"]
MealInputMethod = Literal["manual", "photo", "barcode", "text"]
MealPlanningSourceType = Literal[
    "manual",
    "saved_meal",
    "recipe",
    "ingredient_product_draft",
]
MealPlanningNutritionEstimateState = Literal["known", "partial", "unknown"]
MealPlanningNutritionField = Literal["kcal", "protein", "fat", "carbs"]
_DAY_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class MealIngredient(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    amount: float
    unit: Literal["g", "ml"] | None = None
    kcal: float = 0
    protein: float = 0
    fat: float = 0
    carbs: float = 0


class MealTotals(BaseModel):
    protein: float = 0
    fat: float = 0
    carbs: float = 0
    kcal: float = 0


class MealAiMeta(BaseModel):
    model: str | None = None
    runId: str | None = None
    confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)


class MealImageRef(BaseModel):
    imageId: str = Field(min_length=1)
    storagePath: str | None = None
    downloadUrl: str | None = None


class MealImageRefInput(BaseModel):
    imageId: str = Field(min_length=1)
    storagePath: str | None = None
    downloadUrl: str | None = None


class MealPlanningSourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sourceId: str = Field(min_length=1, max_length=128)
    sourceVersion: int | None = Field(default=None, ge=1)
    snapshotName: str | None = Field(default=None, max_length=240)


class MealPlanningSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    plannedMealId: str = Field(min_length=1, max_length=128)
    plannedMealVersion: int = Field(ge=1)
    sourceType: MealPlanningSourceType
    sourceRef: MealPlanningSourceRef | None = None
    nutritionEstimateState: MealPlanningNutritionEstimateState
    missingNutritionFields: list[MealPlanningNutritionField] = Field(default_factory=list)


class MealTemplateDraftItem(MealIngredient):
    pass


def _meal_ingredients_default() -> list[MealIngredient]:
    return []


def _str_list_default() -> list[str]:
    return []


def _as_object_map(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    raw_map = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for raw_key, raw_item in raw_map.items():
        if isinstance(raw_key, str):
            result[raw_key] = raw_item
    return result


def validate_day_key_format(value: str) -> str:
    if not _DAY_KEY_RE.match(value):
        raise ValueError("dayKey must use YYYY-MM-DD format")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError("dayKey must use YYYY-MM-DD format") from exc


class MealDocument(BaseModel):
    loggedAt: str
    dayKey: str | None = None
    loggedAtLocalMin: int | None = Field(default=None, ge=0, le=1439)
    tzOffsetMin: int | None = Field(default=None, ge=-840, le=840)
    type: MealType
    name: str | None = None
    ingredients: list[MealIngredient] = Field(default_factory=_meal_ingredients_default)
    createdAt: str
    updatedAt: str
    source: MealSource = None
    inputMethod: MealInputMethod | None = None
    aiMeta: MealAiMeta | None = None
    imageRef: MealImageRef | None = None
    planningSource: MealPlanningSource | None = None
    notes: str | None = None
    tags: list[str] = Field(default_factory=_str_list_default)
    deleted: bool = False
    totals: MealTotals = Field(default_factory=MealTotals)

    @field_validator("dayKey")
    @classmethod
    def _validate_day_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_day_key_format(value)


class MealItem(MealDocument):
    id: str = Field(min_length=1)
    syncState: MealSyncState = "synced"
    mealId: str | None = None
    cloudId: str | None = None
    userUid: str | None = None
    timestamp: str | None = None
    imageId: str | None = None
    photoUrl: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _fill_legacy_aliases(cls, value: object) -> object:
        payload = _as_object_map(value)
        if payload is None:
            return value

        normalized: dict[str, Any] = dict(payload)
        if normalized.get("id") is None:
            normalized["id"] = normalized.get("mealId") or normalized.get("cloudId")
        if normalized.get("loggedAt") is None:
            normalized["loggedAt"] = normalized.get("timestamp")

        return normalized


class MealsHistoryPageResponse(BaseModel):
    items: list[MealItem]
    nextCursor: str | None = None


class MealChangesPageResponse(BaseModel):
    items: list[MealItem]
    nextCursor: str | None = None


class MealUpsertRequest(BaseModel):
    clientMutationId: str = Field(min_length=1)
    id: str | None = Field(default=None, min_length=1)
    mealId: str | None = Field(default=None, min_length=1)
    cloudId: str | None = Field(default=None, min_length=1)
    loggedAt: str | None = None
    timestamp: str | None = None
    dayKey: str | None = None
    loggedAtLocalMin: int | None = Field(default=None, ge=0, le=1439)
    tzOffsetMin: int | None = Field(default=None, ge=-840, le=840)
    type: MealType
    name: str | None = None
    ingredients: list[MealIngredient] = Field(default_factory=_meal_ingredients_default)
    createdAt: str | None = None
    updatedAt: str | None = None
    syncState: MealSyncState | None = None
    source: MealSource = None
    inputMethod: MealInputMethod | None = None
    aiMeta: MealAiMeta | None = None
    imageRef: MealImageRefInput | None = None
    planningSource: MealPlanningSource | None = None
    imageId: str | None = None
    photoUrl: str | None = None
    notes: str | None = None
    tags: list[str] = Field(default_factory=_str_list_default)
    deleted: bool = False
    totals: MealTotals | None = None
    userUid: str | None = None

    @field_validator("dayKey")
    @classmethod
    def _validate_day_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_day_key_format(value)

    @field_validator("clientMutationId")
    @classmethod
    def _validate_client_mutation_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("clientMutationId must be non-empty")
        return normalized

    @model_validator(mode="after")
    def _validate_planned_source_nutrition(self) -> "MealUpsertRequest":
        if self.planningSource is None:
            return self

        totals = self.totals
        has_positive_totals = totals is not None and any(
            value > 0
            for value in (totals.kcal, totals.protein, totals.fat, totals.carbs)
        )
        has_positive_ingredient_nutrition = any(
            ingredient.kcal > 0
            or ingredient.protein > 0
            or ingredient.fat > 0
            or ingredient.carbs > 0
            for ingredient in self.ingredients
        )
        if not has_positive_totals and not has_positive_ingredient_nutrition:
            raise ValueError(
                "Planned meal source requires positive nutrition evidence"
            )
        return self


class MealUpsertResponse(BaseModel):
    meal: MealItem
    updated: bool


class MealTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    templateId: str = Field(min_length=1)
    ownerUserId: str = Field(min_length=1)
    templateVersion: int = Field(ge=1)
    displayName: str | None
    description: str | None
    mealTypeHint: MealType
    draftItems: list[MealTemplateDraftItem]
    draftTotals: MealTotals
    nutritionSnapshot: MealTotals
    imageRef: MealImageRef | None
    createdAt: str
    updatedAt: str
    deleted: bool


class MealTemplateChangesPageResponse(BaseModel):
    items: list[MealTemplate]
    nextCursor: str | None = None


class MealTemplateUpsertResponse(BaseModel):
    template: MealTemplate
    updated: bool


class SavedMealUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clientMutationId: str = Field(min_length=1)
    templateId: str = Field(min_length=1)
    ownerUserId: str | None = Field(default=None, min_length=1)
    templateVersion: int = Field(default=1, ge=1)
    displayName: str | None = None
    description: str | None = None
    mealTypeHint: MealType = "other"
    draftItems: list[MealTemplateDraftItem] = Field(default_factory=list)
    draftTotals: MealTotals | None = None
    nutritionSnapshot: MealTotals | None = None
    imageRef: MealImageRefInput | None = None
    createdAt: str | None = None
    updatedAt: str | None = None
    deleted: bool = False

    @field_validator("clientMutationId")
    @classmethod
    def _validate_template_client_mutation_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("clientMutationId must be non-empty")
        return normalized

    @field_validator("templateId")
    @classmethod
    def _validate_template_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("templateId must be non-empty")
        return normalized


class MealDeleteRequest(BaseModel):
    clientMutationId: str = Field(min_length=1)
    updatedAt: str = Field(min_length=1)

    @field_validator("clientMutationId")
    @classmethod
    def _validate_client_mutation_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("clientMutationId must be non-empty")
        return normalized


class MealDeleteResponse(BaseModel):
    mealId: str
    updatedAt: str
    deleted: bool


class SavedMealDeleteRequest(MealDeleteRequest):
    pass


class MealTemplateDeleteResponse(BaseModel):
    templateId: str
    updatedAt: str
    deleted: bool


class MealPhotoUploadResponse(BaseModel):
    mealId: str | None = None
    imageId: str
    storagePath: str
    photoUrl: str


class MealTemplatePhotoUploadResponse(BaseModel):
    templateId: str
    imageId: str
    storagePath: str
    photoUrl: str
