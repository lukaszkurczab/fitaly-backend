from collections.abc import Mapping


def parse_target_kcal(raw_user: Mapping[str, object] | None) -> float:
    if raw_user is None:
        return 0.0

    for key in ("calorieTarget", "targetKcal"):
        value = raw_user.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0
