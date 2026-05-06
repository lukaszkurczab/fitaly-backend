from dataclasses import dataclass, field
from typing import Literal

ReadinessStatus = Literal["needs_profile", "needs_ai_consent", "ready"]


def _empty_preferences() -> list[str]:
    return []


def _empty_allergies() -> list[str]:
    return []


def _empty_style_profile() -> dict[str, str]:
    return {}


@dataclass(slots=True)
class UserProfile:
    user_id: str
    goal: str | None = None
    activity_level: str | None = None
    calorie_target: int | None = None
    preferences: list[str] = field(default_factory=_empty_preferences)
    allergies: list[str] = field(default_factory=_empty_allergies)
    language: str = "en"
    ai_persona: str = "calm_guide"
    ai_health_data_consent_at: str | None = None
    style_profile: dict[str, str] = field(default_factory=_empty_style_profile)
    readiness_status: ReadinessStatus = "needs_profile"
    readiness_onboarding_completed_at: str | None = None
    readiness_ready_at: str | None = None

    @property
    def is_ready(self) -> bool:
        return self.readiness_status == "ready"

    @property
    def has_ai_health_data_consent(self) -> bool:
        return bool(self.ai_health_data_consent_at)
