from typing import Literal

from pydantic import BaseModel, Field, model_validator


MealType = Literal["breakfast", "lunch", "dinner", "snack", "other"]
MealSource = Literal["ai", "manual", "saved"] | None
MealSyncState = Literal["synced", "pending", "conflict", "failed"]
MealInputMethod = Literal["manual", "photo", "barcode", "text", "saved", "quick_add"]


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
    storagePath: str = Field(min_length=1)
    downloadUrl: str | None = None


class MealImageRefInput(BaseModel):
    imageId: str = Field(min_length=1)
    storagePath: str | None = None
    downloadUrl: str | None = None


def _meal_ingredients_default() -> list[MealIngredient]:
    return []


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
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)
    deleted: bool = False
    totals: MealTotals = Field(default_factory=MealTotals)


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
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        payload.setdefault("id", payload.get("mealId") or payload.get("cloudId"))
        payload.setdefault("loggedAt", payload.get("timestamp"))
        if payload.get("imageRef") is None and payload.get("imageId"):
            payload["imageRef"] = {
                "imageId": payload.get("imageId"),
                "storagePath": f"meals/unknown/{payload.get('imageId')}.jpg",
                "downloadUrl": payload.get("photoUrl"),
            }
        return payload


class MealsHistoryPageResponse(BaseModel):
    items: list[MealItem]
    nextCursor: str | None = None


class MealChangesPageResponse(BaseModel):
    items: list[MealItem]
    nextCursor: str | None = None


class MealUpsertRequest(BaseModel):
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
    imageId: str | None = None
    photoUrl: str | None = None
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)
    deleted: bool = False
    totals: MealTotals | None = None
    userUid: str | None = None


class MealUpsertResponse(BaseModel):
    meal: MealItem
    updated: bool


class MealDeleteRequest(BaseModel):
    updatedAt: str = Field(min_length=1)


class MealDeleteResponse(BaseModel):
    mealId: str
    updatedAt: str
    deleted: bool


class MealPhotoUploadResponse(BaseModel):
    mealId: str | None = None
    imageId: str
    photoUrl: str
