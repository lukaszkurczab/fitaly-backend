from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

class UiContextDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    screen: str | None = None
    entry_point: str | None = Field(default=None, alias="entryPoint")

class ChatRunRequestDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    thread_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = (
        Field(alias="threadId")
    )
    client_message_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1)
    ] = Field(alias="clientMessageId")
    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
    ]
    language: Literal["pl", "en"]
    ui_context: UiContextDto | None = Field(default=None, alias="uiContext")
