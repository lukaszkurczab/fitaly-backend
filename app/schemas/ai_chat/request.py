from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

class UiContextDto(BaseModel):
    screen: str | None = None
    entry_point: str | None = Field(default=None, alias="entryPoint")

class ChatRunRequestDto(BaseModel):
    thread_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = (
        Field(alias="threadId")
    )
    client_message_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1)
    ] = Field(alias="clientMessageId")
    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
    ]
    language: Literal["pl", "en"] | None = "pl"
    ui_context: UiContextDto | None = Field(default=None, alias="uiContext")
