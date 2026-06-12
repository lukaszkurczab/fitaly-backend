from dataclasses import dataclass, field
from typing import Literal

ReadinessStatus = Literal["needs_profile", "needs_ai_consent", "ready"]
AiConsentStatus = Literal["not_granted", "granted", "revoked"]


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
    ai_consent_status: AiConsentStatus = "not_granted"
    ai_consent_granted_at: str | None = None
    ai_consent_revoked_at: str | None = None
    style_profile: dict[str, str] = field(default_factory=_empty_style_profile)
    readiness_status: ReadinessStatus = "needs_profile"
    readiness_onboarding_completed_at: str | None = None
    readiness_ready_at: str | None = None

    @property
    def is_ready(self) -> bool:
        return self.readiness_status == "ready"

    @property
    def has_active_ai_consent(self) -> bool:
        return (
            self.ai_consent_status == "granted"
            and bool(self.ai_consent_granted_at)
            and self.ai_consent_revoked_at is None
        )
