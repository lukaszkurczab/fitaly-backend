from pydantic import BaseModel


class AiUsageResponse(BaseModel):
    userId: str
    dateKey: str
    usageCount: float
    dailyLimit: int
    remaining: float
