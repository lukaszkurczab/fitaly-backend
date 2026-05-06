from collections.abc import Mapping
from typing import cast


def parse_target_kcal(raw_user: Mapping[str, object] | None) -> float:
    if raw_user is None:
        return 0.0

    profile = raw_user.get("profile")
    if not isinstance(profile, Mapping):
        return 0.0
    profile_document = cast(Mapping[str, object], profile)
    nutrition_profile = profile_document.get("nutritionProfile")
    if not isinstance(nutrition_profile, Mapping):
        return 0.0
    nutrition_profile_document = cast(Mapping[str, object], nutrition_profile)
    value = nutrition_profile_document.get("calorieTarget")
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0
