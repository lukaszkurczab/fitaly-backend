from typing import Any
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EmailPendingRequest(BaseModel):
    email: str = Field(min_length=3)


class EmailPendingResponse(BaseModel):
    emailPending: str
    updated: bool


class DeleteAccountResponse(BaseModel):
    deleted: bool


class AvatarRefResponse(BaseModel):
    storagePath: str


class AvatarMetadataResponse(BaseModel):
    avatarUrl: str
    avatarlastSyncedAt: str
    avatarRef: AvatarRefResponse
    updated: bool


def _dict_items_default() -> list[dict[str, Any]]:
    return []


class UserExportResponse(BaseModel):
    profile: dict[str, Any] | None
    meals: list[dict[str, Any]]
    myMeals: list[dict[str, Any]] = Field(default_factory=_dict_items_default)
    chatMessages: list[dict[str, Any]]
    chatMemory: list[dict[str, Any]] = Field(default_factory=_dict_items_default)
    aiRuns: list[dict[str, Any]] = Field(default_factory=_dict_items_default)
    notifications: list[dict[str, Any]] = Field(default_factory=_dict_items_default)
    notificationPrefs: dict[str, Any] = Field(default_factory=dict)
    feedback: list[dict[str, Any]] = Field(default_factory=_dict_items_default)
    mealMutationDedupe: list[dict[str, Any]] = Field(default_factory=_dict_items_default)
    billing: list[dict[str, Any]] = Field(default_factory=_dict_items_default)
    aiCredits: list[dict[str, Any]] = Field(default_factory=_dict_items_default)
    aiCreditTransactions: list[dict[str, Any]] = Field(default_factory=_dict_items_default)
    aiCreditIdempotency: list[dict[str, Any]] = Field(default_factory=_dict_items_default)
    badges: list[dict[str, Any]] = Field(default_factory=_dict_items_default)
    streak: list[dict[str, Any]] = Field(default_factory=_dict_items_default)
    reminderDailyStats: list[dict[str, Any]] = Field(default_factory=_dict_items_default)
    telemetryEvents: list[dict[str, Any]] = Field(default_factory=_dict_items_default)


class UserProfileResponse(BaseModel):
    profile: dict[str, Any] | None


class UserProfileUpdateResponse(UserProfileResponse):
    updated: bool


PreferenceValue = Literal[
    "lowCarb",
    "keto",
    "highProtein",
    "highCarb",
    "lowFat",
    "balanced",
    "vegetarian",
    "vegan",
    "pescatarian",
    "mediterranean",
    "glutenFree",
    "dairyFree",
    "paleo",
]

ChronicDiseaseValue = Literal["none", "diabetes", "hypertension", "asthma", "other"]
AllergyValue = Literal["none", "peanuts", "gluten", "lactose", "other"]
AiPersonaValue = Literal[
    "calm_guide",
    "cheerful_companion",
    "focused_coach",
    "mediterranean_friend",
]
UnitsSystemValue = Literal["metric", "imperial"]
ActivityLevelValue = Literal["sedentary", "light", "moderate", "active", "very_active", ""]
GoalValue = Literal["lose", "maintain", "increase", ""]
SexValue = Literal["male", "female"]
LanguageValue = Literal["en", "pl"]
ReadinessStatusValue = Literal["needs_profile", "needs_ai_consent", "ready"]
AiConsentStatusValue = Literal["not_granted", "granted", "revoked"]


def _empty_preferences() -> list[PreferenceValue]:
    return []


def _empty_chronic_diseases() -> list[ChronicDiseaseValue]:
    return []


def _empty_allergies() -> list[AllergyValue]:
    return []


class UserReadinessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ReadinessStatusValue
    onboardingCompletedAt: str | None = Field(default=None, max_length=64)
    readyAt: str | None = Field(default=None, max_length=64)


class UserNutritionProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unitsSystem: UnitsSystemValue | None = Field(default=None)
    age: str | None = Field(default=None, max_length=4)
    sex: SexValue | None = Field(default=None)
    height: str | None = Field(default=None, max_length=8)
    heightInch: str | None = Field(default=None, max_length=8)
    weight: str | None = Field(default=None, max_length=8)
    preferences: list[PreferenceValue] | None = Field(default=None)
    activityLevel: ActivityLevelValue | None = Field(default=None)
    goal: GoalValue | None = Field(default=None)
    chronicDiseases: list[ChronicDiseaseValue] | None = Field(default=None)
    chronicDiseasesOther: str | None = Field(default=None, max_length=120)
    allergies: list[AllergyValue] | None = Field(default=None)
    allergiesOther: str | None = Field(default=None, max_length=120)
    lifestyle: str | None = Field(default=None, max_length=160)
    calorieTarget: int | None = Field(default=None, ge=0, le=10000)

    @field_validator("preferences", "chronicDiseases", "allergies")
    @classmethod
    def _normalize_string_lists(
        cls,
        value: list[str] | None,
    ) -> list[str] | None:
        if value is None:
            return value
        # Keep payload deterministic and bound write size.
        deduped = list(dict.fromkeys(value))
        if len(deduped) > 16:
            raise ValueError("Too many items in profile list field.")
        return deduped


class UserAiPreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stylePersona: AiPersonaValue | None = Field(default=None)


class UserCanonicalProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: LanguageValue | None = Field(default=None)
    nutritionProfile: UserNutritionProfileRequest | None = Field(default=None)
    aiPreferences: UserAiPreferencesRequest | None = Field(default=None)

    def to_editable_patch(self) -> dict[str, Any]:
        profile: dict[str, Any] = {}
        if self.language is not None:
            profile["language"] = self.language
        if self.nutritionProfile is not None:
            nutrition_patch = self.nutritionProfile.model_dump(exclude_unset=True)
            if nutrition_patch:
                profile["nutritionProfile"] = nutrition_patch
        if self.aiPreferences is not None:
            ai_preferences_patch = self.aiPreferences.model_dump(exclude_unset=True)
            if ai_preferences_patch:
                profile["aiPreferences"] = ai_preferences_patch
        return profile


class UserProfilePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clientMutationId: str = Field(min_length=1, max_length=256)
    profile: UserCanonicalProfileRequest | None = Field(default=None)

    @field_validator("clientMutationId", mode="before")
    @classmethod
    def _normalize_client_mutation_id(cls, value: object) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def _ensure_non_empty_patch(self) -> "UserProfilePatchRequest":
        editable_fields = set(self.model_fields_set)
        editable_fields.discard("clientMutationId")
        if not editable_fields:
            raise ValueError("Profile patch payload must not be empty.")
        if self.profile is None or not self.profile.to_editable_patch():
            raise ValueError("Profile patch payload has no editable canonical fields.")
        return self

    def to_patch(self) -> dict[str, Any]:
        assert self.profile is not None
        profile_patch = self.profile.to_editable_patch()
        return {"profile": profile_patch}


class UserOnboardingRequest(BaseModel):
    username: str = Field(min_length=1)
    language: LanguageValue | None = Field(default=None)


class UserOnboardingResponse(BaseModel):
    username: str
    profile: dict[str, Any]
    updated: bool


class UserOnboardingCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unitsSystem: UnitsSystemValue = "metric"
    age: str = Field(max_length=4)
    sex: SexValue
    height: str = Field(max_length=8)
    heightInch: str | None = Field(default="", max_length=8)
    weight: str = Field(max_length=8)
    preferences: list[PreferenceValue] = Field(default_factory=_empty_preferences)
    activityLevel: ActivityLevelValue
    goal: GoalValue
    calorieAdjustment: float | None = Field(default=None, ge=0.1, le=0.5)
    chronicDiseases: list[ChronicDiseaseValue] = Field(default_factory=_empty_chronic_diseases)
    chronicDiseasesOther: str = Field(default="", max_length=120)
    allergies: list[AllergyValue] = Field(default_factory=_empty_allergies)
    allergiesOther: str = Field(default="", max_length=120)
    lifestyle: str = Field(default="", max_length=160)
    aiPersona: AiPersonaValue = "calm_guide"

    @field_validator("preferences", "chronicDiseases", "allergies")
    @classmethod
    def _normalize_string_lists(
        cls,
        value: list[str],
    ) -> list[str]:
        deduped = list(dict.fromkeys(value))
        if len(deduped) > 16:
            raise ValueError("Too many items in profile list field.")
        return deduped

    @model_validator(mode="after")
    def _validate_final_onboarding(self) -> "UserOnboardingCompleteRequest":
        age = self._parse_number(self.age)
        height = self._parse_number(self.height)
        weight = self._parse_number(self.weight)
        if age is None or age < 16 or age > 120:
            raise ValueError("Invalid onboarding age.")
        if height is None or height < 90 or height > 250:
            raise ValueError("Invalid onboarding height.")
        if weight is None or weight < 30 or weight > 300:
            raise ValueError("Invalid onboarding weight.")
        if self.activityLevel == "":
            raise ValueError("Activity level is required.")
        if self.goal == "":
            raise ValueError("Goal is required.")
        if self.goal in {"lose", "increase"} and self.calorieAdjustment is None:
            raise ValueError("Calorie adjustment is required for this goal.")
        if self.goal == "maintain" and self.calorieAdjustment is not None:
            raise ValueError("Calorie adjustment is only supported for adjustment goals.")
        return self

    @staticmethod
    def _parse_number(value: str) -> int | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None

    def to_completion_payload(self) -> dict[str, Any]:
        return self.model_dump()


class UserOnboardingCompleteResponse(UserProfileUpdateResponse):
    pass


class AiConsentState(BaseModel):
    status: AiConsentStatusValue
    grantedAt: str | None = None
    revokedAt: str | None = None


class AiConsentActionResponse(BaseModel):
    aiConsent: AiConsentState
