from __future__ import annotations

from typing import Any, cast

from app.domain.users.models.user_profile import ReadinessStatus, UserProfile
from app.services import user_account_service

_ACTIVITY_MULTIPLIERS = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
    "very_active": 1.9,
}


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
    if text == "pl" or text.startswith("pl-"):
        return "pl"
    return "en"


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


def _normalize_ai_persona(ai_persona: object) -> str:
    persona_text = str(ai_persona or "").strip().lower().replace("-", "_")
    persona_key = _AI_PERSONA_ALIASES.get(persona_text) if persona_text else None
    if persona_key:
        return persona_key
    return "calm_guide"


def _style_profile(persona: str) -> dict[str, str]:
    label = _AI_PERSONA_LABELS.get(persona, _AI_PERSONA_LABELS["calm_guide"])
    return {
        "id": persona if persona in _AI_PERSONA_LABELS else "calm_guide",
        "label": label,
    }


def _normalize_readiness(raw: object) -> tuple[ReadinessStatus, str | None, str | None]:
    if not isinstance(raw, dict):
        return "needs_profile", None, None
    payload = cast(dict[str, object], raw)
    status_raw = payload.get("status")
    status: ReadinessStatus
    if status_raw == "needs_ai_consent" or status_raw == "ready":
        status = status_raw
    else:
        status = "needs_profile"
    onboarding_completed_at = payload.get("onboardingCompletedAt")
    ready_at = payload.get("readyAt")
    onboarding_completed_at_text = (
        str(onboarding_completed_at).strip()
        if onboarding_completed_at is not None
        else None
    )
    ready_at_text = str(ready_at).strip() if ready_at is not None else None
    return (
        status,
        onboarding_completed_at_text or None,
        ready_at_text or None,
    )


def _calculate_calorie_target(payload: dict[str, Any]) -> int:
    weight_kg = _normalize_int(payload.get("weight"))
    height_cm = _normalize_int(payload.get("height"))
    age = _normalize_int(payload.get("age"))
    sex = str(payload.get("sex") or "").strip().lower()
    activity_level = str(payload.get("activityLevel") or "").strip()
    goal = str(payload.get("goal") or "").strip()

    if not weight_kg or not height_cm or not age or sex not in {"male", "female"}:
        raise ValueError("Onboarding profile is missing required calorie inputs.")
    if activity_level not in _ACTIVITY_MULTIPLIERS:
        raise ValueError("Onboarding profile has invalid activity level.")
    if goal not in {"lose", "maintain", "increase"}:
        raise ValueError("Onboarding profile has invalid goal.")

    bmr = (
        10 * weight_kg + 6.25 * height_cm - 5 * age + 5
        if sex == "male"
        else 10 * weight_kg + 6.25 * height_cm - 5 * age - 161
    )
    tdee = bmr * _ACTIVITY_MULTIPLIERS[activity_level]
    adjustment_raw = payload.get("calorieAdjustment")
    adjustment = float(adjustment_raw) if adjustment_raw is not None else None

    if goal == "lose":
        if adjustment is None:
            raise ValueError("Calorie adjustment is required for weight loss goal.")
        target = tdee * (1 - adjustment)
    elif goal == "increase":
        if adjustment is None:
            raise ValueError("Calorie adjustment is required for weight gain goal.")
        target = tdee * (1 + adjustment)
    else:
        target = tdee

    rounded = round(target)
    if rounded < 1000 or rounded > 6000:
        raise ValueError("Calculated calorie target is outside supported range.")
    return rounded


class UserProfileService:
    async def get_profile(self, *, user_id: str) -> UserProfile | None:
        raw = await user_account_service.get_user_profile_data(user_id)
        if raw is None:
            return None
        return self._to_profile(user_id=user_id, raw=raw)

    @staticmethod
    def _to_profile(*, user_id: str, raw: dict[str, Any]) -> UserProfile:
        profile_raw = raw.get("profile")
        profile = cast(dict[str, Any], profile_raw) if isinstance(profile_raw, dict) else {}
        nutrition_raw = profile.get("nutritionProfile")
        nutrition = (
            cast(dict[str, Any], nutrition_raw) if isinstance(nutrition_raw, dict) else {}
        )
        ai_preferences_raw = profile.get("aiPreferences")
        ai_preferences = (
            cast(dict[str, Any], ai_preferences_raw)
            if isinstance(ai_preferences_raw, dict)
            else {}
        )
        consents_raw = profile.get("consents")
        consents = (
            cast(dict[str, Any], consents_raw) if isinstance(consents_raw, dict) else {}
        )
        ai_persona = _normalize_ai_persona(ai_preferences.get("stylePersona"))
        readiness_status, onboarding_completed_at, ready_at = _normalize_readiness(
            profile.get("readiness")
        )
        ai_health_data_consent_at_raw = consents.get("aiHealthDataConsentAt")
        ai_health_data_consent_at = (
            str(ai_health_data_consent_at_raw).strip()
            if ai_health_data_consent_at_raw is not None
            else None
        )
        return UserProfile(
            user_id=user_id,
            goal=str(nutrition.get("goal")).strip() if nutrition.get("goal") else None,
            activity_level=str(nutrition.get("activityLevel")).strip()
            if nutrition.get("activityLevel")
            else None,
            calorie_target=_normalize_int(nutrition.get("calorieTarget")),
            preferences=_normalize_list(nutrition.get("preferences")),
            allergies=_normalize_list(nutrition.get("allergies")),
            language=_normalize_language(profile.get("language")),
            ai_persona=ai_persona,
            ai_health_data_consent_at=ai_health_data_consent_at or None,
            style_profile=_style_profile(ai_persona),
            readiness_status=readiness_status,
            readiness_onboarding_completed_at=onboarding_completed_at,
            readiness_ready_at=ready_at,
        )

    @staticmethod
    def build_onboarding_completion_patch(
        *,
        payload: dict[str, Any],
        completed_at: str,
    ) -> dict[str, Any]:
        calorie_target = _calculate_calorie_target(payload)
        return {
            "profile": {
                "nutritionProfile": {
                    "unitsSystem": payload.get("unitsSystem"),
                    "age": payload.get("age"),
                    "sex": payload.get("sex"),
                    "height": payload.get("height"),
                    "heightInch": payload.get("heightInch") or "",
                    "weight": payload.get("weight"),
                    "preferences": payload.get("preferences") or [],
                    "activityLevel": payload.get("activityLevel"),
                    "goal": payload.get("goal"),
                    "chronicDiseases": payload.get("chronicDiseases") or [],
                    "chronicDiseasesOther": payload.get("chronicDiseasesOther") or "",
                    "allergies": payload.get("allergies") or [],
                    "allergiesOther": payload.get("allergiesOther") or "",
                    "lifestyle": payload.get("lifestyle") or "",
                    "calorieTarget": calorie_target,
                },
                "aiPreferences": {
                    "stylePersona": _normalize_ai_persona(payload.get("aiPersona")),
                },
                "readiness": {
                    "status": "needs_ai_consent",
                    "onboardingCompletedAt": completed_at,
                    "readyAt": None,
                },
            },
        }

    async def get_profile_summary(self, *, user_id: str) -> dict[str, Any]:
        profile = await self.get_profile(user_id=user_id)
        if profile is None:
            return {
                "goal": None,
                "activityLevel": None,
                "preferences": [],
                "allergies": [],
                "language": "en",
                "aiPersona": "calm_guide",
                "styleProfile": _style_profile("calm_guide"),
            }

        return {
            "goal": profile.goal,
            "activityLevel": profile.activity_level,
            "preferences": profile.preferences,
            "allergies": profile.allergies,
            "language": profile.language,
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
