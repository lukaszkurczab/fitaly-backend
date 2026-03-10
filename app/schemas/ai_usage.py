from pydantic import BaseModel


class AiUsageStatus(BaseModel):
    dateKey: str
    usageCount: float
    dailyLimit: int
    remaining: float


class AiUsageResponse(AiUsageStatus):
    pass
