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


class AvatarMetadataRequest(BaseModel):
    avatarUrl: str = Field(min_length=1)


class AvatarMetadataResponse(BaseModel):
    avatarUrl: str
    avatarlastSyncedAt: str
    updated: bool


def _dict_items_default() -> list[dict[str, Any]]:
    return []


class UserExportResponse(BaseModel):
    profile: dict[str, Any] | None
    meals: list[dict[str, Any]]
    myMeals: list[dict[str, Any]] = Field(default_factory=_dict_items_default)
    chatMessages: list[dict[str, Any]]
    notifications: list[dict[str, Any]] = Field(default_factory=_dict_items_default)
    notificationPrefs: dict[str, Any] = Field(default_factory=dict)
    feedback: list[dict[str, Any]] = Field(default_factory=_dict_items_default)


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


class UserReadinessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ReadinessStatusValue
    onboardingCompletedAt: str | None = Field(default=None, max_length=64)
    readyAt: str | None = Field(default=None, max_length=64)


class UserProfilePatchRequest(BaseModel):
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
    aiPersona: AiPersonaValue | None = Field(default=None)
    calorieTarget: int | None = Field(default=None, ge=0, le=10000)
    language: LanguageValue | None = Field(default=None)

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

    @model_validator(mode="after")
    def _ensure_non_empty_patch(self) -> "UserProfilePatchRequest":
        if not self.model_fields_set:
            raise ValueError("Profile patch payload must not be empty.")
        return self

    def to_patch(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


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
    preferences: list[PreferenceValue] = Field(default_factory=list)
    activityLevel: ActivityLevelValue
    goal: GoalValue
    calorieAdjustment: float | None = Field(default=None, ge=0.1, le=0.5)
    chronicDiseases: list[ChronicDiseaseValue] = Field(default_factory=list)
    chronicDiseasesOther: str = Field(default="", max_length=120)
    allergies: list[AllergyValue] = Field(default_factory=list)
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


class AiHealthDataConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: Literal[True] = True


class AiHealthDataConsentState(BaseModel):
    status: ReadinessStatusValue
    onboardingCompletedAt: str | None = None
    readyAt: str | None = None


class AiHealthDataConsentResponse(UserProfileResponse):
    updated: bool
    consent: AiHealthDataConsentState
