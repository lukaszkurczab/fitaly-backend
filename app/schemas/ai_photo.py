from pydantic import BaseModel, Field


class AiPhotoAnalyzeRequest(BaseModel):
    userId: str = Field(min_length=1)
    imageBase64: str = Field(min_length=1)
    lang: str = Field(default="en", min_length=2, max_length=10)


class AiPhotoIngredient(BaseModel):
    name: str
    amount: float
    protein: float
    fat: float
    carbs: float
    kcal: float
    unit: str | None = None


class AiPhotoAnalyzeResponse(BaseModel):
    userId: str
    ingredients: list[AiPhotoIngredient]
    usageCount: int
    remaining: int
    dateKey: str
    version: str
