from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SmartMemoryType = Literal[
    "typical_portion",
    "review_correction",
    "ingredient_product_selection",
]
SmartMemoryState = Literal[
    "candidate",
    "active",
    "muted",
    "deleted_suppressed",
    "disabled",
    "source_deleted",
    "sync_failed",
    "conflicted",
]
SmartMemoryCandidateState = Literal["candidate", "deleted_suppressed", "source_deleted"]
SmartMemoryStateReasonCode = Literal[
    "threshold_met",
    "user_muted",
    "user_restored",
    "user_deleted",
    "account_disabled",
    "source_deleted",
    "sync_failed",
    "conflict_remote_won",
    "local_pending",
]
SmartMemoryConfidenceReasonCode = Literal[
    "single_observation",
    "distinct_days_met",
    "consistent_user_review",
    "ingredient_selection_repeated",
]
SmartMemoryUserValueReasonCode = Literal["user_corrected"]
SmartMemoryUserControlOperation = Literal[
    "candidate_upsert",
    "edit",
    "mute",
    "restore",
    "delete",
    "source_deleted",
    "settings_disable",
    "settings_enable",
]
SmartMemoryProjectionState = Literal[
    "no_signal",
    "backend_candidate",
    "pending_offline_candidate",
    "active",
    "muted",
    "deleted_suppressed",
    "disabled",
    "source_deleted",
    "sync_failed",
    "conflicted",
    "queued_edit",
    "queued_mute",
    "queued_delete",
    "queued_disable",
]
SmartMemoryCenterState = Literal[
    "empty_enabled",
    "empty_disabled",
    "has_active",
    "has_pending_controls",
    "has_sync_failed",
]
SmartMemoryReviewState = Literal["used", "new", "disabled"]

SMART_MEMORY_CONTRACT_NAME = "smart_memory_core_v1"
SMART_MEMORY_SCHEMA_VERSION = 1
SMART_MEMORY_TYPES: tuple[SmartMemoryType, ...] = (
    "typical_portion",
    "review_correction",
    "ingredient_product_selection",
)
SMART_MEMORY_STATES: tuple[SmartMemoryState, ...] = (
    "candidate",
    "active",
    "muted",
    "deleted_suppressed",
    "disabled",
    "source_deleted",
    "sync_failed",
    "conflicted",
)
SMART_MEMORY_CANDIDATE_STATES: tuple[SmartMemoryCandidateState, ...] = (
    "candidate",
    "deleted_suppressed",
    "source_deleted",
)
SMART_MEMORY_STATE_REASON_CODES: tuple[SmartMemoryStateReasonCode, ...] = (
    "threshold_met",
    "user_muted",
    "user_restored",
    "user_deleted",
    "account_disabled",
    "source_deleted",
    "sync_failed",
    "conflict_remote_won",
    "local_pending",
)
SMART_MEMORY_CONFIDENCE_REASON_CODES: tuple[SmartMemoryConfidenceReasonCode, ...] = (
    "single_observation",
    "distinct_days_met",
    "consistent_user_review",
    "ingredient_selection_repeated",
)
SMART_MEMORY_USER_VALUE_REASON_CODES: tuple[SmartMemoryUserValueReasonCode, ...] = (
    "user_corrected",
)
SMART_MEMORY_USER_CONTROL_OPERATIONS: tuple[SmartMemoryUserControlOperation, ...] = (
    "candidate_upsert",
    "edit",
    "mute",
    "restore",
    "delete",
    "source_deleted",
    "settings_disable",
    "settings_enable",
)
SMART_MEMORY_PROJECTION_STATES: tuple[SmartMemoryProjectionState, ...] = (
    "no_signal",
    "backend_candidate",
    "pending_offline_candidate",
    "active",
    "muted",
    "deleted_suppressed",
    "disabled",
    "source_deleted",
    "sync_failed",
    "conflicted",
    "queued_edit",
    "queued_mute",
    "queued_delete",
    "queued_disable",
)
SMART_MEMORY_CENTER_STATES: tuple[SmartMemoryCenterState, ...] = (
    "empty_enabled",
    "empty_disabled",
    "has_active",
    "has_pending_controls",
    "has_sync_failed",
)
SMART_MEMORY_REVIEW_STATES: tuple[SmartMemoryReviewState, ...] = (
    "used",
    "new",
    "disabled",
)

FORBIDDEN_MEMORY_PAYLOAD_KEYS = {
    "rawPrompt",
    "rawResponse",
    "providerMessages",
    "fullPayload",
    "openaiPayload",
    "providerPayload",
    "telemetryPayload",
    "rawReviewDiff",
    "rawDiff",
    "mealSnapshot",
}
RAW_SUBJECT_KEYS = {
    "key",
    "name",
    "label",
    "displayLabel",
    "alias",
    "ingredientName",
    "ingredientId",
}
RAW_SOURCE_REF_KEYS = {
    "mealId",
    "ingredientId",
    "dayKey",
    "loggedAt",
    "updatedAt",
    "name",
    "label",
    "displayLabel",
}
HASHED_SOURCE_REF_KEYS = {"kind", "sourceHash"}


def _dict_default() -> dict[str, Any]:
    return {}


def _list_default() -> list[dict[str, Any]]:
    return []


def _str_list_default() -> list[str]:
    return []


def _confidence_reason_list_default() -> list[SmartMemoryConfidenceReasonCode]:
    return []


def _reject_forbidden_payload_keys(value: object) -> object:
    if isinstance(value, dict):
        raw = cast(dict[object, object], value)
        for key, item in raw.items():
            if isinstance(key, str) and key in FORBIDDEN_MEMORY_PAYLOAD_KEYS:
                raise ValueError(f"Smart Memory payload cannot include {key}")
            _reject_forbidden_payload_keys(item)
    elif isinstance(value, list):
        for item in cast(list[object], value):
            _reject_forbidden_payload_keys(item)
    return cast(object, value)


def _require_hash_only_source_ref(value: dict[str, Any], *, message: str) -> dict[str, Any]:
    checked = cast(dict[str, Any], _reject_forbidden_payload_keys(value))
    if set(checked) != HASHED_SOURCE_REF_KEYS:
        raise ValueError(message)
    if not isinstance(checked.get("kind"), str) or not isinstance(
        checked.get("sourceHash"),
        str,
    ):
        raise ValueError(message)
    return checked


def _reject_invalid_user_value_reason_code(value: dict[str, Any]) -> dict[str, Any]:
    reason_code = value.get("reasonCode")
    if (
        reason_code is not None
        and reason_code not in SMART_MEMORY_USER_VALUE_REASON_CODES
    ):
        raise ValueError("Smart Memory userValue reasonCode is unsupported")
    return value


class SmartMemoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memoryItemId: str = Field(min_length=1, max_length=128)
    ownerUserId: str = Field(min_length=1)
    schemaVersion: int = Field(default=1, ge=1)
    memoryType: SmartMemoryType
    state: SmartMemoryState
    stateReason: SmartMemoryStateReasonCode | None = None
    subject: dict[str, Any] = Field(default_factory=_dict_default)
    userValue: dict[str, Any] = Field(default_factory=_dict_default)
    evidenceSummary: dict[str, Any] = Field(default_factory=_dict_default)
    sourceRefs: list[dict[str, Any]] = Field(default_factory=_list_default)
    threshold: dict[str, Any] = Field(default_factory=_dict_default)
    confidence: dict[str, Any] = Field(default_factory=_dict_default)
    confidenceReasonCodes: list[SmartMemoryConfidenceReasonCode] = Field(
        default_factory=_confidence_reason_list_default
    )
    control: dict[str, Any] = Field(default_factory=_dict_default)
    createdAt: str
    updatedAt: str
    lastEvaluatedAt: str | None = None
    mutedAt: str | None = None
    deletedAt: str | None = None
    editedAt: str | None = None
    restoredAt: str | None = None
    sourceDeletedAt: str | None = None
    serverRevision: int = Field(default=1, ge=1)

    @field_validator(
        "subject",
        "userValue",
        "evidenceSummary",
        "threshold",
        "confidence",
        "control",
    )
    @classmethod
    def _reject_forbidden_dicts(cls, value: dict[str, Any]) -> dict[str, Any]:
        return cast(dict[str, Any], _reject_forbidden_payload_keys(value))

    @field_validator("userValue")
    @classmethod
    def _reject_invalid_user_value_reason_code(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        return _reject_invalid_user_value_reason_code(value)

    @field_validator("sourceRefs")
    @classmethod
    def _reject_forbidden_source_refs(
        cls,
        value: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], _reject_forbidden_payload_keys(value))


class SmartMemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidateId: str = Field(min_length=1, max_length=128)
    ownerUserId: str = Field(min_length=1)
    schemaVersion: int = Field(default=1, ge=1)
    memoryType: SmartMemoryType
    state: Literal["candidate", "deleted_suppressed", "source_deleted"] = "candidate"
    subject: dict[str, Any] = Field(default_factory=_dict_default)
    evidenceSummary: dict[str, Any] = Field(default_factory=_dict_default)
    sourceRefs: list[dict[str, Any]] = Field(default_factory=_list_default)
    confidenceReasonCodes: list[SmartMemoryConfidenceReasonCode] = Field(
        default_factory=_confidence_reason_list_default
    )
    suppressionChecks: dict[str, Any] = Field(default_factory=_dict_default)
    createdAt: str
    updatedAt: str
    firstSeenAt: str | None = None
    lastSeenAt: str | None = None
    serverRevision: int = Field(default=1, ge=1)

    @field_validator("subject", "evidenceSummary", "suppressionChecks")
    @classmethod
    def _reject_forbidden_dicts(cls, value: dict[str, Any]) -> dict[str, Any]:
        return cast(dict[str, Any], _reject_forbidden_payload_keys(value))

    @field_validator("sourceRefs")
    @classmethod
    def _reject_forbidden_source_refs(
        cls,
        value: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], _reject_forbidden_payload_keys(value))


class SmartMemorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ownerUserId: str = Field(min_length=1)
    enabled: bool = True
    disabledAt: str | None = None
    updatedAt: str
    serverRevision: int = Field(default=1, ge=1)
    clientMutationId: str | None = None


class SmartMemoryTombstone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tombstoneId: str = Field(min_length=1, max_length=128)
    ownerUserId: str = Field(min_length=1)
    memoryType: SmartMemoryType
    subjectKey: str = Field(min_length=1, max_length=256)
    deletedAt: str
    deleteRevision: int = Field(ge=1)
    reasonCode: str | None = Field(default=None, max_length=80)


class SmartMemoryMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clientMutationId: str = Field(min_length=1, max_length=160)

    @field_validator("clientMutationId")
    @classmethod
    def _strip_client_mutation_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Missing clientMutationId")
        return normalized


class SmartMemoryItemPatchRequest(SmartMemoryMutationRequest):
    userValue: dict[str, Any] | None = None
    stateReason: SmartMemoryStateReasonCode | None = None
    editedFields: list[str] = Field(default_factory=_str_list_default)

    @field_validator("userValue")
    @classmethod
    def _reject_forbidden_user_value(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        checked = cast(dict[str, Any], _reject_forbidden_payload_keys(value))
        return _reject_invalid_user_value_reason_code(checked)


class SmartMemorySettingsUpdateRequest(SmartMemoryMutationRequest):
    enabled: bool


class SmartMemorySourceDeletedRequest(SmartMemoryMutationRequest):
    sourceRef: dict[str, Any]

    @field_validator("sourceRef")
    @classmethod
    def _reject_forbidden_source_ref(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        return _require_hash_only_source_ref(
            value,
            message="Smart Memory sourceRef must use hashed identifiers",
        )


class SmartMemoryCandidateUpsertRequest(SmartMemoryMutationRequest):
    candidateId: str = Field(min_length=1, max_length=128)
    memoryType: SmartMemoryType
    subject: dict[str, Any] = Field(default_factory=_dict_default)
    evidenceSummary: dict[str, Any] = Field(default_factory=_dict_default)
    sourceRefs: list[dict[str, Any]] = Field(default_factory=_list_default)
    confidenceReasonCodes: list[SmartMemoryConfidenceReasonCode] = Field(
        default_factory=_confidence_reason_list_default
    )
    suppressionChecks: dict[str, Any] = Field(default_factory=_dict_default)
    firstSeenAt: str | None = None
    lastSeenAt: str | None = None

    @field_validator("subject", "evidenceSummary", "suppressionChecks")
    @classmethod
    def _reject_forbidden_dicts(cls, value: dict[str, Any]) -> dict[str, Any]:
        return cast(dict[str, Any], _reject_forbidden_payload_keys(value))

    @field_validator("sourceRefs")
    @classmethod
    def _reject_forbidden_source_refs(
        cls,
        value: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], _reject_forbidden_payload_keys(value))

    @model_validator(mode="after")
    def _require_hashed_subject_and_source_refs(self) -> "SmartMemoryCandidateUpsertRequest":
        if not self.subject:
            return self
        raw_subject_keys = set(self.subject) & RAW_SUBJECT_KEYS
        if raw_subject_keys:
            raise ValueError("Smart Memory candidate subject must use hashed identifiers")
        kind = self.subject.get("kind")
        has_subject_hash = isinstance(self.subject.get("subjectHash"), str)
        has_alias_hash = isinstance(self.subject.get("aliasHash"), str)
        if not isinstance(kind, str) or not (has_subject_hash or has_alias_hash):
            raise ValueError("Smart Memory candidate subject must include a hash")
        for source_ref in self.sourceRefs:
            _require_hash_only_source_ref(
                source_ref,
                message="Smart Memory sourceRefs must use hashed identifiers",
            )
        return self


class SmartMemoryItemResponse(BaseModel):
    item: SmartMemoryItem


class SmartMemoryItemMutationResponse(SmartMemoryItemResponse):
    updated: bool


class SmartMemoryItemsPageResponse(BaseModel):
    items: list[SmartMemoryItem]


class SmartMemoryCandidateResponse(BaseModel):
    candidate: SmartMemoryCandidate
    updated: bool = False


class SmartMemoryCandidatesPageResponse(BaseModel):
    items: list[SmartMemoryCandidate]


class SmartMemorySettingsResponse(BaseModel):
    settings: SmartMemorySettings
    updated: bool = False


class SmartMemoryReasonCodeContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stateReasonCodes: list[SmartMemoryStateReasonCode]
    confidenceReasonCodes: list[SmartMemoryConfidenceReasonCode]
    userValueReasonCodes: list[SmartMemoryUserValueReasonCode]


class SmartMemoryEndpointContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["GET", "POST", "PATCH"]
    path: str = Field(min_length=1)
    response: str = Field(min_length=1)


class SmartMemoryApiResponseExamples(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emptyItemsPage: SmartMemoryItemsPageResponse
    itemsPage: SmartMemoryItemsPageResponse
    candidateResponse: SmartMemoryCandidateResponse
    itemDeleteResponse: SmartMemoryItemMutationResponse
    settingsEnabledResponse: SmartMemorySettingsResponse
    settingsDisabledResponse: SmartMemorySettingsResponse


class SmartMemoryQueuedOperationExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: SmartMemoryUserControlOperation
    status: Literal["queued", "sync_failed", "conflicted"]
    clientMutationId: str = Field(min_length=1)


class SmartMemoryStateTransitionExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: SmartMemoryProjectionState
    memoryType: SmartMemoryType | None = None
    backendState: SmartMemoryState | None = None
    projectionState: SmartMemoryProjectionState
    reviewState: SmartMemoryReviewState | None = None
    memoryItemId: str | None = None
    candidateId: str | None = None
    queuedOperation: SmartMemoryQueuedOperationExample | None = None
    suggestionUse: Literal["allowed", "blocked", "pending_only"]


class SmartMemoryCenterStateContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    states: list[SmartMemoryCenterState]
    emptyEnabledAllowsCandidates: bool
    disabledBlocksCandidateWrites: bool
    syncFailedRequiresVisibleRetryOrDiscard: bool


class SmartMemoryReviewStateContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    states: list[SmartMemoryReviewState]
    usedCanReferenceBackendActiveMemory: bool
    newCannotDependOnPendingCandidate: bool
    disabledCannotWriteCandidate: bool


class SmartMemoryPrivacyBoundaryContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    excludesMealNarrativeText: bool
    excludesReviewDiffs: bool
    excludesProviderPayloads: bool
    excludesTelemetryPrivateIdentifiers: bool
    usesHashedSubjectAndSourceRefs: bool


class SmartMemoryCoreContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: Literal["smart_memory_core_v1"]
    schemaVersion: Literal[1]
    memoryTypes: list[SmartMemoryType]
    memoryStates: list[SmartMemoryState]
    candidateStates: list[SmartMemoryCandidateState]
    reasonCodes: SmartMemoryReasonCodeContract
    userControlOperations: list[SmartMemoryUserControlOperation]
    offlineProjectionStates: list[SmartMemoryProjectionState]
    apiEndpoints: list[SmartMemoryEndpointContract]
    apiResponseExamples: SmartMemoryApiResponseExamples
    stateTransitionExamples: list[SmartMemoryStateTransitionExample]
    memoryCenter: SmartMemoryCenterStateContract
    review: SmartMemoryReviewStateContract
    privacyBoundary: SmartMemoryPrivacyBoundaryContract


class SmartMemoryExport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, Any]] = Field(default_factory=_list_default)
    candidates: list[dict[str, Any]] = Field(default_factory=_list_default)
    settings: list[dict[str, Any]] = Field(default_factory=_list_default)
    tombstones: list[dict[str, Any]] = Field(default_factory=_list_default)
    mutationDedupe: list[dict[str, Any]] = Field(default_factory=_list_default)

    @model_validator(mode="after")
    def _reject_forbidden_export_payload(self) -> "SmartMemoryExport":
        _reject_forbidden_payload_keys(self.model_dump())
        return self
