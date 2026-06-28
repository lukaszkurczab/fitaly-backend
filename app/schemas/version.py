from pydantic import BaseModel, ConfigDict, Field


class VersionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version: str
    commit_sha: str | None = Field(default=None, alias="commitSha")
