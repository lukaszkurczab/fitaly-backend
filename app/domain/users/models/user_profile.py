from dataclasses import dataclass, field


@dataclass(slots=True)
class UserProfile:
    user_id: str
    goal: str | None = None
    activity_level: str | None = None
    calorie_target: int | None = None
    preferences: list[str] = field(default_factory=list)
    allergies: list[str] = field(default_factory=list)
    language: str = "pl"
    ai_health_data_consent_at: str | None = None
    survey_completed: bool = False
