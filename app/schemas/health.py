from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: datetime


class FirestoreHealthResponse(BaseModel):
    status: str
    service: str
    database: str
    project_id: str
    timestamp: datetime
