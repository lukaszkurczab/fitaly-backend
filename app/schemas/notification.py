from pydantic import BaseModel, Field


class NotificationQuietHours(BaseModel):
    startHour: int = Field(ge=0, le=23)
    endHour: int = Field(ge=0, le=23)


class NotificationPrefsPayload(BaseModel):
    smartRemindersEnabled: bool | None = None
    motivationEnabled: bool | None = None
    statsEnabled: bool | None = None
    weekdays0to6: list[int] | None = None
    daysAhead: int | None = Field(default=None, ge=1, le=14)
    quietHours: NotificationQuietHours | None = None


class NotificationPrefsResponse(BaseModel):
    notifications: NotificationPrefsPayload


class NotificationPrefsUpdateRequest(BaseModel):
    notifications: NotificationPrefsPayload


class NotificationPrefsUpdateResponse(NotificationPrefsResponse):
    updated: bool
