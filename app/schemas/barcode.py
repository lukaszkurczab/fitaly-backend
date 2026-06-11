from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BarcodeIngredient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    amount: float
    unit: Literal["g", "ml"] | None = None
    kcal: float = 0
    protein: float = 0
    fat: float = 0
    carbs: float = 0


class BarcodeLookupFoundResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["found"]
    name: str = Field(min_length=1)
    ingredient: BarcodeIngredient
