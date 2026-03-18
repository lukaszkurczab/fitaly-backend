from typing import Any, Dict, Optional

from pydantic import BaseModel

from app.schemas.ai_common import BaseAiResponse


class AiAskRequest(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = None


class AiAskResponse(BaseAiResponse):
    reply: str
