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
SavedMealPhotoLibraryBridgeDomain = Literal["MealTemplate", "Recipe"]
SavedMealPhotoLibraryNonMigrationTargetDomain = Literal[
    "Ingredient/Product",
    "ShoppingList",
]
SavedMealPhotoLibraryNonMigrationBoundaryMechanism = Literal[
    "excluded_from_saved_meal_photo_media_bridge"
]
SavedMealPhotoLibraryNonMigrationReason = Literal[
    "product_media_is_product_owned_not_derived_from_saved_meal_photo_asset",
    "shopping_list_references_items_without_transforming_saved_meal_photo_assets",
]
SavedMealPhotoStableMediaIdentity = Literal["imageRef", "imageRef.storagePath"]
SavedMealPhotoForbiddenLibraryField = Literal[
    "recipeLifecycleState",
    "productLifecycleState",
    "shoppingListLifecycleState",
    "recipeMediaLifecycle",
    "productMediaLifecycle",
    "shoppingListMediaLifecycle",
]

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
SAVED_MEAL_PHOTO_STABLE_MEDIA_IDENTITY: tuple[
    SavedMealPhotoStableMediaIdentity,
    ...,
] = ("imageRef", "imageRef.storagePath")
SAVED_MEAL_PHOTO_LIBRARY_BRIDGE_DOMAINS: tuple[
    SavedMealPhotoLibraryBridgeDomain,
    ...,
] = (
    "MealTemplate",
    "Recipe",
)
SAVED_MEAL_PHOTO_LIBRARY_NON_MIGRATION_TARGETS: tuple[
    tuple[
        SavedMealPhotoLibraryNonMigrationTargetDomain,
        SavedMealPhotoLibraryNonMigrationBoundaryMechanism,
        SavedMealPhotoLibraryNonMigrationReason,
    ],
    ...,
] = (
    (
        "Ingredient/Product",
        "excluded_from_saved_meal_photo_media_bridge",
        "product_media_is_product_owned_not_derived_from_saved_meal_photo_asset",
    ),
    (
        "ShoppingList",
        "excluded_from_saved_meal_photo_media_bridge",
        "shopping_list_references_items_without_transforming_saved_meal_photo_assets",
    ),
)
SAVED_MEAL_PHOTO_LIBRARY_SCHEMA_FIELDS_FORBIDDEN: tuple[
    SavedMealPhotoForbiddenLibraryField,
    ...,
] = (
    "recipeLifecycleState",
    "productLifecycleState",
    "shoppingListLifecycleState",
    "recipeMediaLifecycle",
    "productMediaLifecycle",
    "shoppingListMediaLifecycle",
)


class SavedMealPhotoLibraryNonMigrationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: SavedMealPhotoLibraryNonMigrationTargetDomain
    boundaryMechanism: SavedMealPhotoLibraryNonMigrationBoundaryMechanism
    reason: SavedMealPhotoLibraryNonMigrationReason


class SavedMealPhotoFutureLibraryBridge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currentDomain: Literal["saved_meal"]
    stableMediaIdentity: list[SavedMealPhotoStableMediaIdentity]
    bridgesToDomains: list[SavedMealPhotoLibraryBridgeDomain]
    bridgeMechanism: Literal["reuse_imageRef_storagePath_without_storage_rewrite"]
    requiresSeparateMediaMigration: Literal[False]
    nonMigrationTargets: list[SavedMealPhotoLibraryNonMigrationTarget]
    loggedMealMustRemainNarrow: Literal[True]
    currentSavedMealMustNotExpandWith: list[SavedMealPhotoForbiddenLibraryField]

    @model_validator(mode="after")
    def _validate_exact_bridge(self) -> "SavedMealPhotoFutureLibraryBridge":
        if tuple(self.stableMediaIdentity) != SAVED_MEAL_PHOTO_STABLE_MEDIA_IDENTITY:
            raise ValueError(
                "stableMediaIdentity must use canonical imageRef storage identity"
            )
        if tuple(self.bridgesToDomains) != SAVED_MEAL_PHOTO_LIBRARY_BRIDGE_DOMAINS:
            raise ValueError(
                "bridgesToDomains must match the saved-meal library media bridge"
            )
        non_migration_targets = tuple(
            (target.domain, target.boundaryMechanism, target.reason)
            for target in self.nonMigrationTargets
        )
        if (
            non_migration_targets
            != SAVED_MEAL_PHOTO_LIBRARY_NON_MIGRATION_TARGETS
        ):
            raise ValueError(
                "nonMigrationTargets must match the saved-meal library boundary"
            )
        if (
            tuple(self.currentSavedMealMustNotExpandWith)
            != SAVED_MEAL_PHOTO_LIBRARY_SCHEMA_FIELDS_FORBIDDEN
        ):
            raise ValueError(
                "currentSavedMealMustNotExpandWith must forbid future library fields"
            )
        return self


class MediaAssetSurfaceContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usesAssetStates: Literal["assetStates"]
    domainOwner: MediaAssetDomainOwner
    domainDocumentOwns: list[str] = Field(min_length=1)
    futureLibraryBridge: SavedMealPhotoFutureLibraryBridge | None = None
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
        saved_meal_photo = self.surfaces["saved_meal_photo"]
        if saved_meal_photo.futureLibraryBridge is None:
            raise ValueError(
                "saved_meal_photo must declare the future library media bridge"
            )
        if saved_meal_photo.domainDocumentOwns != [
            "imageRef",
            "displayMetadata",
            "savedMealDomainMetadata",
        ]:
            raise ValueError(
                "saved_meal_photo must keep only current saved-meal domain fields"
            )
        forbidden_library_fields = set(SAVED_MEAL_PHOTO_LIBRARY_SCHEMA_FIELDS_FORBIDDEN)
        if forbidden_library_fields.intersection(saved_meal_photo.domainDocumentOwns):
            raise ValueError(
                "saved_meal_photo domain ownership must not include library fields"
            )
        for surface_name, surface in self.surfaces.items():
            if (
                surface_name != "saved_meal_photo"
                and surface.futureLibraryBridge is not None
            ):
                raise ValueError(
                    "futureLibraryBridge is only valid for saved_meal_photo"
                )
        return self
