from dataclasses import dataclass


@dataclass(slots=True)
class NutritionSummary:
    start_date: str
    end_date: str
    timezone: str
    period_type: str
    is_partial: bool
    days_in_period: int
    days_with_entries: int
    meal_count: int
    coverage_level: str
    total_kcal: float
    total_protein_g: float
    total_fat_g: float
    total_carbs_g: float
