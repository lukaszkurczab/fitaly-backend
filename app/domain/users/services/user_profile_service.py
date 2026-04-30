from __future__ import annotations

from typing import Any, cast

from app.domain.users.models.user_profile import UserProfile
from app.services import user_account_service


def _normalize_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in cast(list[object], value):
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


_AI_PERSONA_LABELS = {
    "calm_guide": "Calm Guide",
    "cheerful_companion": "Cheerful Companion",
    "focused_coach": "Focused Coach",
    "mediterranean_friend": "Mediterranean Friend",
}

_AI_PERSONA_ALIASES = {
    "": "calm_guide",
    "none": "calm_guide",
    "calm": "calm_guide",
    "calm_guide": "calm_guide",
    "calm guide": "calm_guide",
    "friendly": "cheerful_companion",
    "cheerful": "cheerful_companion",
    "cheerful_companion": "cheerful_companion",
    "cheerful companion": "cheerful_companion",
    "concise": "focused_coach",
    "focused": "focused_coach",
    "focused_coach": "focused_coach",
    "focused coach": "focused_coach",
    "mediterranean": "mediterranean_friend",
    "mediterranean_friend": "mediterranean_friend",
    "mediterranean friend": "mediterranean_friend",
}


def _normalize_ai_persona(*, ai_persona: object, ai_style: object) -> str:
    persona_text = str(ai_persona or "").strip().lower().replace("-", "_")
    persona_key = _AI_PERSONA_ALIASES.get(persona_text) if persona_text else None
    if persona_key:
        return persona_key

    style_text = str(ai_style or "").strip().lower().replace("-", "_")
    return _AI_PERSONA_ALIASES.get(style_text, "calm_guide")


def _style_profile(persona: str) -> dict[str, str]:
    label = _AI_PERSONA_LABELS.get(persona, _AI_PERSONA_LABELS["calm_guide"])
    return {
        "id": persona if persona in _AI_PERSONA_LABELS else "calm_guide",
        "label": label,
    }


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
        ai_style = str(raw.get("aiStyle")).strip() if raw.get("aiStyle") else None
        ai_persona = _normalize_ai_persona(
            ai_persona=raw.get("aiPersona"),
            ai_style=ai_style,
        )
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
            ai_style=ai_style,
            ai_persona=ai_persona,
            style_profile=_style_profile(ai_persona),
            ai_health_data_consent_at=consent_at_text,
            survey_completed=bool(raw.get("surveyComplited")),
        )

    async def get_profile_summary(self, *, user_id: str) -> dict[str, Any]:
        profile = await self.get_profile(user_id=user_id)
        if profile is None:
            return {
                "goal": None,
                "activityLevel": None,
                "preferences": [],
                "allergies": [],
                "language": "pl",
                "aiStyle": None,
                "aiPersona": "calm_guide",
                "styleProfile": _style_profile("calm_guide"),
            }

        return {
            "goal": profile.goal,
            "activityLevel": profile.activity_level,
            "preferences": profile.preferences,
            "allergies": profile.allergies,
            "language": profile.language,
            "aiStyle": profile.ai_style,
            "aiPersona": profile.ai_persona,
            "styleProfile": profile.style_profile,
        }

    async def get_goal_context(self, *, user_id: str) -> dict[str, Any]:
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
