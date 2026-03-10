from pydantic import BaseModel, Field

from app.schemas.ai_common import AiPersistence
from app.schemas.ai_usage import AiUsageStatus


class AiTextMealPayload(BaseModel):
    name: str | None = None
    ingredients: str | None = None
    amount_g: int | None = Field(default=None, gt=0)
    notes: str | None = None


class AiTextMealAnalyzeRequest(BaseModel):
    payload: AiTextMealPayload
    lang: str = Field(default="en", min_length=2, max_length=10)


class AiTextMealIngredient(BaseModel):
    name: str
    amount: float
    protein: float
    fat: float
    carbs: float
    kcal: float
    unit: str | None = None


class AiTextMealAnalyzeResponse(AiUsageStatus):
    ingredients: list[AiTextMealIngredient]
    version: str
    persistence: AiPersistence
