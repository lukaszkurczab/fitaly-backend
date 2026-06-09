from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


MediaAssetState = Literal[
    "local_pending",
    "uploading",
    "uploaded",
    "attached",
    "failed",
    "dead_letter",
    "retryable",
    "discarded",
    "deleted",
]
MediaAssetSurface = Literal[
    "meal_photo",
    "saved_meal_photo",
    "avatar",
    "feedback_attachment",
]
MediaAssetDomainOwner = Literal["meal", "saved_meal", "profile", "feedback"]

MEDIA_ASSET_STATES: tuple[MediaAssetState, ...] = (
    "local_pending",
    "uploading",
    "uploaded",
    "attached",
    "failed",
    "dead_letter",
    "retryable",
    "discarded",
    "deleted",
)
MEDIA_ASSET_SURFACES: tuple[MediaAssetSurface, ...] = (
    "meal_photo",
    "saved_meal_photo",
    "avatar",
    "feedback_attachment",
)
MEDIA_ASSET_LIFECYCLE_OWNER = "media_asset_lifecycle"
MEDIA_ASSET_LIFECYCLE_OWNED_FIELDS: tuple[str, ...] = (
    "localFilePath",
    "opId",
    "clientMutationId",
    "remoteStoragePath",
    "uploadAttempt",
    "uploadState",
    "retryState",
    "discardState",
    "deleteState",
    "deadLetterReason",
    "resolvedDownloadUrl",
)
MEDIA_ASSET_DOMAIN_OWNED_URL_FIELDS_FORBIDDEN: frozenset[str] = frozenset(
    {
        "avatarUrl",
        "attachmentUrl",
        "downloadUrl",
        "publicUrl",
        "resolvedDownloadUrl",
    }
)


class MediaAssetSurfaceContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usesAssetStates: Literal["assetStates"]
    domainOwner: MediaAssetDomainOwner
    domainDocumentOwns: list[str] = Field(min_length=1)
    domainDocumentMustNotOwn: list[str] = Field(min_length=1)


class MediaAssetLifecycleContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: Literal["media_asset_lifecycle_v1"]
    assetStates: list[MediaAssetState]
    lifecycleOwner: Literal["media_asset_lifecycle"]
    assetLifecycleOwns: list[str] = Field(min_length=1)
    surfaces: dict[MediaAssetSurface, MediaAssetSurfaceContract]

    @model_validator(mode="after")
    def _validate_exact_contract_sets(self) -> "MediaAssetLifecycleContract":
        if tuple(self.assetStates) != MEDIA_ASSET_STATES:
            raise ValueError("assetStates must match the canonical media asset vocabulary")
        if set(self.surfaces.keys()) != set(MEDIA_ASSET_SURFACES):
            raise ValueError("surfaces must match the canonical media asset surfaces")
        if tuple(self.assetLifecycleOwns) != MEDIA_ASSET_LIFECYCLE_OWNED_FIELDS:
            raise ValueError("assetLifecycleOwns must match canonical lifecycle ownership")
        for surface in self.surfaces.values():
            if (
                tuple(surface.domainDocumentMustNotOwn)
                != MEDIA_ASSET_LIFECYCLE_OWNED_FIELDS
            ):
                raise ValueError(
                    "domainDocumentMustNotOwn must forbid every lifecycle-owned field"
                )
            if MEDIA_ASSET_DOMAIN_OWNED_URL_FIELDS_FORBIDDEN.intersection(
                surface.domainDocumentOwns
            ) or any(
                field.endswith(("Url", "URL"))
                for field in surface.domainDocumentOwns
            ):
                raise ValueError("domainDocumentOwns must not declare URL fields")
        return self
