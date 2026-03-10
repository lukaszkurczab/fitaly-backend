from typing import Any, Dict, Optional

from pydantic import BaseModel
from app.schemas.ai_common import AiPersistence
from app.schemas.ai_usage import AiUsageStatus


class AiAskRequest(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = None


class AiAskResponse(AiUsageStatus):
    reply: str
    version: str
    persistence: AiPersistence
