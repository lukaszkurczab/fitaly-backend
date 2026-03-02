"""Schema representing error logs sent from the client application."""

from typing import Dict, Optional

from pydantic import BaseModel


class ErrorLogRequest(BaseModel):
    source: str
    message: str
    stack: Optional[str] = None
    context: Optional[Dict] = None
    userId: Optional[str] = None
