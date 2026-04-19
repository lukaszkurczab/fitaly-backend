from __future__ import annotations

from typing import Any

from app.domain.users.models.user_profile import UserProfile
from app.services import user_account_service


def _normalize_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
    return result


def _normalize_language(value: object) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("en"):
        return "en"
    return "pl"


def _normalize_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None
    return None


class UserProfileService:
    async def get_profile(self, *, user_id: str) -> UserProfile | None:
        raw = await user_account_service.get_user_profile_data(user_id)
        if raw is None:
            return None
        return self._to_profile(user_id=user_id, raw=raw)

    @staticmethod
    def _to_profile(*, user_id: str, raw: dict[str, Any]) -> UserProfile:
        consent_at = raw.get("aiHealthDataConsentAt")
        consent_at_text = str(consent_at).strip() if consent_at is not None else None
        if consent_at_text == "":
            consent_at_text = None
        return UserProfile(
            user_id=user_id,
            goal=str(raw.get("goal")).strip() if raw.get("goal") else None,
            activity_level=str(raw.get("activityLevel")).strip()
            if raw.get("activityLevel")
            else None,
            calorie_target=_normalize_int(raw.get("calorieTarget")),
            preferences=_normalize_list(raw.get("preferences")),
            allergies=_normalize_list(raw.get("allergies")),
            language=_normalize_language(raw.get("language")),
            ai_health_data_consent_at=consent_at_text,
            survey_completed=bool(raw.get("surveyComplited")),
        )

    async def get_profile_summary(self, *, user_id: str) -> dict:
        profile = await self.get_profile(user_id=user_id)
        if profile is None:
            return {
                "goal": None,
                "activityLevel": None,
                "preferences": [],
                "allergies": [],
                "language": "pl",
            }

        return {
            "goal": profile.goal,
            "activityLevel": profile.activity_level,
            "preferences": profile.preferences,
            "allergies": profile.allergies,
            "language": profile.language,
        }

    async def get_goal_context(self, *, user_id: str) -> dict:
        profile = await self.get_profile(user_id=user_id)
        if profile is None:
            return {
                "goal": None,
                "calorieTarget": None,
                "proteinStrategy": None,
            }

        protein_strategy = None
        goal = (profile.goal or "").strip().lower()
        if goal in {"lose", "fat_loss", "reduction"}:
            protein_strategy = "higher_protein_for_satiety_and_muscle_retention"
        elif goal in {"gain", "muscle_gain", "bulk"}:
            protein_strategy = "higher_protein_with_calorie_surplus"
        elif goal:
            protein_strategy = "balanced_protein_intake"

        return {
            "goal": profile.goal,
            "calorieTarget": profile.calorie_target,
            "proteinStrategy": protein_strategy,
        }
