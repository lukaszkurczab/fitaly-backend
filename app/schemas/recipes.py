from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.user_account import AllergyValue, ChronicDiseaseValue, PreferenceValue


RecipeCatalogLifecycleState = Literal["active", "retired"]
RecipeCatalogReviewState = Literal["curated", "needs_review"]
RecipeCatalogNutritionConfidence = Literal["unknown", "low", "medium", "high", "verified"]
RecipeCatalogProfileFlagState = Literal["complete", "partial", "unknown"]
RecipeCatalogAllergenFlag = Literal["peanuts", "gluten", "lactose"]
RecipeCatalogDietaryFlag = Literal[
    "vegan",
    "vegetarian",
    "pescatarian",
    "gluten_free",
    "dairy_free",
]
RecipeCatalogStyleTag = Literal["balanced", "mediterranean", "paleo"]
RecipeCatalogFilterStatus = Literal[
    "visible",
    "hidden_hard_exclusion",
    "unknown_reveal_required",
]
RecipeCatalogSoftPreferenceStatus = Literal["not_applicable", "match", "miss", "mixed"]
RecipeCatalogReasonCode = Literal[
    "explicit_allergen_match",
    "explicit_restriction_mismatch",
    "unknown_allergen_flag",
    "unknown_restriction_flag",
]
RecipeCatalogReasonType = Literal["allergy", "restriction"]


def _empty_allergies() -> list[AllergyValue]:
    return []


def _empty_preferences() -> list[PreferenceValue]:
    return []


def _empty_chronic_diseases() -> list[ChronicDiseaseValue]:
    return []


class RecipeCatalogSourceAttribution(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sourceType: Literal["internal_curated"]
    sourceId: str = Field(min_length=1, max_length=128)
    sourceName: str = Field(min_length=1, max_length=128)
    reviewedAt: str = Field(min_length=1, max_length=64)


class RecipeCatalogIngredientRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ingredientProductId: str | None = Field(default=None, min_length=1, max_length=128)
    snapshotName: str = Field(min_length=1, max_length=128)
    quantity: int = Field(ge=0)
    unit: Literal["g", "ml", "piece", "serving", "tbsp", "tsp"]


class RecipeCatalogNutritionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kcal: int = Field(ge=0)
    proteinGrams: int = Field(ge=0)
    fatGrams: int = Field(ge=0)
    carbsGrams: int = Field(ge=0)
    confidence: RecipeCatalogNutritionConfidence
    isPartial: bool = False


class RecipeCatalogRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        strict=True,
    )

    recipeId: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    lifecycleState: RecipeCatalogLifecycleState
    locale: str = Field(min_length=2, max_length=16)
    title: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=500)
    servings: int = Field(ge=1, le=24)
    yieldText: str = Field(alias="yield", serialization_alias="yield", min_length=1, max_length=120)
    sourceAttribution: RecipeCatalogSourceAttribution
    updatedAt: str = Field(min_length=1, max_length=64)
    reviewState: RecipeCatalogReviewState
    ingredients: list[RecipeCatalogIngredientRef] = Field(min_length=1)
    steps: list[str] = Field(min_length=1)
    prepTimeMin: int = Field(ge=0, le=1440)
    cookTimeMin: int = Field(ge=0, le=1440)
    nutritionSnapshot: RecipeCatalogNutritionSnapshot
    imageRef: str | None = Field(default=None, max_length=256)
    profileFlagState: RecipeCatalogProfileFlagState
    dietaryFlags: list[RecipeCatalogDietaryFlag] = Field(default_factory=list)
    allergenFlags: list[RecipeCatalogAllergenFlag] = Field(default_factory=list)
    unknownDietaryFlags: list[RecipeCatalogDietaryFlag] = Field(default_factory=list)
    unknownAllergenFlags: list[RecipeCatalogAllergenFlag] = Field(default_factory=list)
    styleTags: list[RecipeCatalogStyleTag] = Field(default_factory=list)

    @field_validator(
        "dietaryFlags",
        "allergenFlags",
        "unknownDietaryFlags",
        "unknownAllergenFlags",
        "styleTags",
    )
    @classmethod
    def _dedupe_flags(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _validate_unknown_flag_overlap(self) -> "RecipeCatalogRecord":
        dietary_overlap = set(self.dietaryFlags).intersection(self.unknownDietaryFlags)
        allergen_overlap = set(self.allergenFlags).intersection(self.unknownAllergenFlags)
        if dietary_overlap:
            raise ValueError("dietary flags cannot also be unknown")
        if allergen_overlap:
            raise ValueError("allergen flags cannot also be unknown")
        return self


class RecipeCatalogFilterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    allergies: list[AllergyValue] = Field(default_factory=_empty_allergies)
    preferences: list[PreferenceValue] = Field(default_factory=_empty_preferences)
    chronicDiseases: list[ChronicDiseaseValue] = Field(
        default_factory=_empty_chronic_diseases
    )
    allergiesOther: str | None = Field(default=None, max_length=120)
    lifestyle: str | None = Field(default=None, max_length=160)
    showHidden: bool = False
    revealUnknown: bool = False

    @field_validator("allergies", "preferences", "chronicDiseases")
    @classmethod
    def _dedupe_profile_lists(cls, value: list[str]) -> list[str]:
        deduped = list(dict.fromkeys(value))
        if len(deduped) > 16:
            raise ValueError("Too many profile filter values.")
        return deduped


class RecipeCatalogFilterReason(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: RecipeCatalogReasonCode
    filterType: RecipeCatalogReasonType
    profileValue: str = Field(min_length=1, max_length=64)
    catalogFlag: str = Field(min_length=1, max_length=64)


class RecipeCatalogFilterResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    recipe: RecipeCatalogRecord
    status: RecipeCatalogFilterStatus
    hardExclusionReasons: list[RecipeCatalogFilterReason] = Field(default_factory=list)
    unknownReasons: list[RecipeCatalogFilterReason] = Field(default_factory=list)
    softPreferenceStatus: RecipeCatalogSoftPreferenceStatus
    softPreferenceMatches: list[PreferenceValue] = Field(default_factory=list)
    softPreferenceMisses: list[PreferenceValue] = Field(default_factory=list)
    softPreferenceScore: int = Field(ge=0)


class RecipeCatalogFilterQueryEcho(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    activeAllergies: list[AllergyValue]
    activeRestrictions: list[PreferenceValue]
    activeSoftPreferences: list[PreferenceValue]
    ignoredChronicDiseases: list[ChronicDiseaseValue]
    ignoredAllergiesOtherPresent: bool
    ignoredLifestylePresent: bool
    showHidden: bool
    revealUnknown: bool
    lowResultsThreshold: int = Field(ge=0)


class RecipeCatalogFilterResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[RecipeCatalogFilterResult]
    queryEcho: RecipeCatalogFilterQueryEcho
    totalCatalogCount: int = Field(ge=0)
    visibleCount: int = Field(ge=0)
    hiddenHardExclusionCount: int = Field(ge=0)
    unknownRevealRequiredCount: int = Field(ge=0)
    lowResults: bool
    emptyCatalog: bool
