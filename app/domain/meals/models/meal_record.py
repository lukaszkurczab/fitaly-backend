from dataclasses import dataclass


@dataclass(slots=True)
class MealRecord:
    id: str
    day_key: str
    timestamp: str
    meal_count: int = 1
    kcal: float = 0.0
    protein_g: float = 0.0
    fat_g: float = 0.0
    carbs_g: float = 0.0
