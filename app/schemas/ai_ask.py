from typing import Any, Dict, Optional

from pydantic import BaseModel


class AiAskRequest(BaseModel):
    userId: str
    message: str
    context: Optional[Dict[str, Any]] = None


class AiAskResponse(BaseModel):
    userId: str
    reply: str
    usageCount: float
    remaining: float
    dateKey: str
    version: str
