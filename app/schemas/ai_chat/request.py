from typing import Optional, Literal
from pydantic import BaseModel, Field, constr

class UiContextDto(BaseModel):
    screen: Optional[str] = None
    entry_point: Optional[str] = Field(default=None, alias="entryPoint")

class ChatRunRequestDto(BaseModel):
    thread_id: constr(strip_whitespace=True, min_length=1) = Field(alias="threadId")
    client_message_id: constr(strip_whitespace=True, min_length=1) = Field(alias="clientMessageId")
    message: constr(strip_whitespace=True, min_length=1, max_length=4000)
    language: Optional[Literal["pl", "en"]] = "pl"
    ui_context: Optional[UiContextDto] = Field(default=None, alias="uiContext")
