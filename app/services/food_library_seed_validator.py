"""Validation and import evidence for Ingredient/Product seed data."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.schemas.food_library import (
    IngredientProductAllergenFlag,
    IngredientProductConfidence,
    IngredientProductConfidenceLevel,
    IngredientProductDietaryFlag,
    IngredientProductKind,
    IngredientProductLifecycleState,
    IngredientProductNutritionPer100,
    IngredientProductProfileCompatibilityStatus,
    IngredientProductRecordScope,
    IngredientProductServing,
    IngredientProductServingSize,
    IngredientProductSourceAttribution,
    IngredientProductSourceType,
    IngredientProductWarningReasonCode,
)

FoodLibrarySeedDatasetKind = Literal["production_corpus", "local_e2e_seed"]
FoodLibrarySeedValidationSeverity = Literal["error", "warning"]
FoodLibrarySeedValidationIssueCode = Literal[
    "schema_error",
    "missing_approved_production_corpus",
    "empty_seed_corpus",
    "document_id_mismatch",
    "global_owner_leakage",
    "user_scoped_record",
    "invalid_lifecycle_state",
    "candidate_only_source_type",
    "placeholder_content",
    "low_required_confidence",
    "malformed_search_prefix",
    "missing_required_search_prefix",
    "ai_derived_durable_nutrition",
    "kind_specific_field_missing",
    "profile_status_unknown",
    "unsafe_document_id",
    "invalid_global_source_type",
    "nutrition_basis_unit_mismatch",
    "implausible_nutrition",
]

RecordPayload = Mapping[str, Any]

_MIN_SEARCH_PREFIX_LENGTH = 2
_CANDIDATE_ONLY_SOURCE_TYPES: set[IngredientProductSourceType] = {
    "barcode_identity",
    "runtime_ai_candidate",
}
_APPROVED_GLOBAL_SOURCE_TYPES: set[IngredientProductSourceType] = {
    "internal_seed",
    "internal_review",
    "external_provider",
}
_LOW_REQUIRED_CONFIDENCE: set[IngredientProductConfidenceLevel] = {"unknown", "low"}
_MAX_NUTRITION_KCAL_PER_100 = 900
_MAX_NUTRIENT_GRAMS_PER_100 = 100
_PLACEHOLDER_PATTERN = re.compile(
    r"(^|[\s_\-:/])(?:todo|tbd|placeholder|example|sample|dummy|mock|lorem)([\s_\-:/]|$)",
)
_AI_DERIVED_PATTERN = re.compile(
    r"(^|[\s_\-:/])(?:ai|openai|gpt|llm|ai[-_ ]?generated|ai[-_ ]?derived)([\s_\-:/]|$)",
)


class IngredientProductBarcodeIdentitySeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    barcode: str = Field(min_length=1)
    format: str = Field(min_length=1)
    sourceType: IngredientProductSourceType
    normalizedBarcode: str | None = None
    country: str | None = None
    sourceId: str | None = None
    observedAt: str | None = None


class IngredientProductSeedProfileFlags(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dietaryFlags: list[IngredientProductDietaryFlag] = Field(default_factory=list)
    allergenFlags: list[IngredientProductAllergenFlag] = Field(default_factory=list)
    compatibilityStatus: IngredientProductProfileCompatibilityStatus | None = None
    profileCompatibilityStatus: IngredientProductProfileCompatibilityStatus | None = None

    @model_validator(mode="after")
    def _reject_conflicting_statuses(self) -> "IngredientProductSeedProfileFlags":
        if (
            self.compatibilityStatus is not None
            and self.profileCompatibilityStatus is not None
            and self.compatibilityStatus != self.profileCompatibilityStatus
        ):
            raise ValueError("profileFlags compatibility statuses must match")
        return self

    def resolved_status(self) -> IngredientProductProfileCompatibilityStatus | None:
        if self.compatibilityStatus is not None:
            return self.compatibilityStatus
        return self.profileCompatibilityStatus


class IngredientProductSeedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingredientProductId: str = Field(min_length=1, max_length=128)
    recordScope: IngredientProductRecordScope
    lifecycleState: IngredientProductLifecycleState
    kind: IngredientProductKind
    displayName: str = Field(min_length=1, max_length=160)
    sourceAttribution: IngredientProductSourceAttribution
    confidence: IngredientProductConfidence
    nutritionPer100: IngredientProductNutritionPer100
    defaultServing: IngredientProductServing
    servingSizes: list[IngredientProductServingSize]
    profileFlags: IngredientProductSeedProfileFlags
    createdAt: str = Field(min_length=1)
    updatedAt: str = Field(min_length=1)
    searchPrefixes: list[str] = Field(min_length=1)
    profileCompatibility: IngredientProductProfileCompatibilityStatus | None = None
    ownerUserId: str | None = None
    brandName: str | None = None
    ingredientName: str | None = None
    packageName: str | None = None
    category: str | None = None
    barcodeIdentities: list[IngredientProductBarcodeIdentitySeed] = Field(
        default_factory=list
    )
    externalSourceIds: dict[str, str] = Field(default_factory=dict)
    dietaryFlags: list[IngredientProductDietaryFlag] = Field(default_factory=list)
    allergenFlags: list[IngredientProductAllergenFlag] = Field(default_factory=list)
    warningReasonCodes: list[IngredientProductWarningReasonCode] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def _validate_profile_status(self) -> "IngredientProductSeedRecord":
        profile_flags_status = self.profileFlags.resolved_status()
        if self.profileCompatibility is None and profile_flags_status is None:
            raise ValueError("Ingredient/Product seed must declare profile compatibility")
        if (
            self.profileCompatibility is not None
            and profile_flags_status is not None
            and self.profileCompatibility != profile_flags_status
        ):
            raise ValueError("profileCompatibility must match profileFlags status")
        return self

    def resolved_profile_status(self) -> IngredientProductProfileCompatibilityStatus:
        if self.profileCompatibility is not None:
            return self.profileCompatibility
        return cast(
            IngredientProductProfileCompatibilityStatus,
            self.profileFlags.resolved_status(),
        )


class FoodLibrarySeedValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: FoodLibrarySeedValidationIssueCode
    severity: FoodLibrarySeedValidationSeverity
    path: str
    message: str
    recordId: str | None = None


class FoodLibrarySeedValidationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recordCount: int
    sourceTypes: dict[str, int]
    confidenceLevels: dict[str, int]
    scopeCounts: dict[str, int]
    lifecycleCounts: dict[str, int]
    warningCount: int
    coverageNotes: list[str]


class FoodLibrarySeedValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasetName: str
    datasetKind: FoodLibrarySeedDatasetKind
    summary: FoodLibrarySeedValidationSummary
    issues: list[FoodLibrarySeedValidationIssue]
    hasErrors: bool


class FoodLibrarySeedValidationError(ValueError):
    """Raised when a seed import is blocked by validation errors."""

    def __init__(self, report: FoodLibrarySeedValidationReport) -> None:
        self.report = report
        super().__init__(
            f"{report.datasetName} failed Food Library seed validation "
            f"with {sum(1 for issue in report.issues if issue.severity == 'error')} error(s)."
        )


def _normalize_search_value(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().lower())
    without_marks = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", without_marks).strip()


def _search_prefixes(*values: str | None) -> set[str]:
    prefixes: set[str] = set()
    for value in values:
        if not value:
            continue
        normalized = _normalize_search_value(value)
        if len(normalized) >= _MIN_SEARCH_PREFIX_LENGTH:
            for end_index in range(_MIN_SEARCH_PREFIX_LENGTH, len(normalized) + 1):
                prefixes.add(normalized[:end_index])
        for token in normalized.split():
            if len(token) < _MIN_SEARCH_PREFIX_LENGTH:
                continue
            for end_index in range(_MIN_SEARCH_PREFIX_LENGTH, len(token) + 1):
                prefixes.add(token[:end_index])
    return prefixes


def _string_values(record: IngredientProductSeedRecord) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = [
        ("ingredientProductId", record.ingredientProductId),
        ("displayName", record.displayName),
        ("sourceAttribution.sourceId", record.sourceAttribution.sourceId),
        ("sourceAttribution.sourceName", record.sourceAttribution.sourceName),
    ]
    for path, value in (
        ("brandName", record.brandName),
        ("ingredientName", record.ingredientName),
        ("packageName", record.packageName),
        ("category", record.category),
        ("sourceAttribution.provider", record.sourceAttribution.provider),
        ("sourceAttribution.license", record.sourceAttribution.license),
        ("sourceAttribution.observedAt", record.sourceAttribution.observedAt),
        ("sourceAttribution.reviewedAt", record.sourceAttribution.reviewedAt),
        ("sourceAttribution.reviewedBy", record.sourceAttribution.reviewedBy),
    ):
        if value:
            values.append((path, value))
    for index, serving in enumerate(record.servingSizes):
        values.append((f"servingSizes.{index}.servingSizeId", serving.servingSizeId))
        values.append((f"servingSizes.{index}.label", serving.label))
    for key, value in record.externalSourceIds.items():
        values.append((f"externalSourceIds.{key}", key))
        values.append((f"externalSourceIds.{key}", value))
    for index, barcode_identity in enumerate(record.barcodeIdentities):
        values.append((f"barcodeIdentities.{index}.barcode", barcode_identity.barcode))
        values.append((f"barcodeIdentities.{index}.format", barcode_identity.format))
        values.append(
            (
                f"barcodeIdentities.{index}.sourceType",
                barcode_identity.sourceType,
            )
        )
        for path, value in (
            ("normalizedBarcode", barcode_identity.normalizedBarcode),
            ("country", barcode_identity.country),
            ("sourceId", barcode_identity.sourceId),
            ("observedAt", barcode_identity.observedAt),
        ):
            if value:
                values.append((f"barcodeIdentities.{index}.{path}", value))
    return values


def _source_attribution_values(
    record: IngredientProductSeedRecord,
) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = [
        ("sourceAttribution.sourceType", record.sourceAttribution.sourceType),
        ("sourceAttribution.sourceId", record.sourceAttribution.sourceId),
        ("sourceAttribution.sourceName", record.sourceAttribution.sourceName),
    ]
    for path, value in (
        ("sourceAttribution.provider", record.sourceAttribution.provider),
        ("sourceAttribution.license", record.sourceAttribution.license),
        ("sourceAttribution.observedAt", record.sourceAttribution.observedAt),
        ("sourceAttribution.reviewedAt", record.sourceAttribution.reviewedAt),
        ("sourceAttribution.reviewedBy", record.sourceAttribution.reviewedBy),
    ):
        if value:
            values.append((path, value))
    return values


def _record_id(raw_record: object) -> str | None:
    if not isinstance(raw_record, dict):
        return None
    raw_payload = cast(dict[str, object], raw_record)
    raw_value = raw_payload.get("ingredientProductId")
    if isinstance(raw_value, str) and raw_value.strip():
        return raw_value.strip()
    return None


def _issue_path(record_index: int, loc: object | None = None) -> str:
    if loc is None:
        return f"records[{record_index}]"
    if isinstance(loc, tuple):
        suffix = ".".join(str(part) for part in cast(tuple[object, ...], loc))
    else:
        suffix = str(loc)
    return f"records[{record_index}].{suffix}" if suffix else f"records[{record_index}]"


def _issue(
    *,
    code: FoodLibrarySeedValidationIssueCode,
    severity: FoodLibrarySeedValidationSeverity,
    path: str,
    message: str,
    record_id: str | None,
) -> FoodLibrarySeedValidationIssue:
    return FoodLibrarySeedValidationIssue(
        code=code,
        severity=severity,
        path=path,
        message=message,
        recordId=record_id,
    )


def _schema_issues(
    *,
    record_index: int,
    raw_record: object,
    error: ValidationError,
) -> list[FoodLibrarySeedValidationIssue]:
    record_id = _record_id(raw_record)
    issues: list[FoodLibrarySeedValidationIssue] = []
    for validation_error in error.errors():
        location = validation_error.get("loc", ())
        error_message = str(validation_error.get("msg", "schema validation failed"))
        issues.append(
            _issue(
                code="schema_error",
                severity="error",
                path=_issue_path(record_index, location),
                message=error_message,
                record_id=record_id,
            )
        )
    return issues


def _record_issues(
    *,
    record_index: int,
    record: IngredientProductSeedRecord,
    document_id: str | None,
) -> list[FoodLibrarySeedValidationIssue]:
    record_id = record.ingredientProductId
    issues: list[FoodLibrarySeedValidationIssue] = []
    base_path = f"records[{record_index}]"

    if "/" in record.ingredientProductId:
        issues.append(
            _issue(
                code="unsafe_document_id",
                severity="error",
                path=f"{base_path}.ingredientProductId",
                message="ingredientProductId must be a document id, not a path.",
                record_id=record_id,
            )
        )

    if document_id is not None and document_id != record.ingredientProductId:
        issues.append(
            _issue(
                code="document_id_mismatch",
                severity="error",
                path=f"{base_path}.ingredientProductId",
                message="Document id must match ingredientProductId before import.",
                record_id=record_id,
            )
        )

    if record.recordScope == "user_scoped":
        issues.append(
            _issue(
                code="user_scoped_record",
                severity="error",
                path=f"{base_path}.recordScope",
                message="Production seed/corpus records must be global records.",
                record_id=record_id,
            )
        )
    if record.recordScope in {"global_seed", "global_internal"} and record.ownerUserId:
        issues.append(
            _issue(
                code="global_owner_leakage",
                severity="error",
                path=f"{base_path}.ownerUserId",
                message="Global Ingredient/Product records must not carry ownerUserId.",
                record_id=record_id,
            )
        )
    if (
        record.recordScope in {"global_seed", "global_internal"}
        and record.sourceAttribution.sourceType not in _APPROVED_GLOBAL_SOURCE_TYPES
    ):
        issues.append(
            _issue(
                code="invalid_global_source_type",
                severity="error",
                path=f"{base_path}.sourceAttribution.sourceType",
                message="Global seed/corpus records must use reviewed global source attribution.",
                record_id=record_id,
            )
        )

    if record.lifecycleState != "verified":
        issues.append(
            _issue(
                code="invalid_lifecycle_state",
                severity="error",
                path=f"{base_path}.lifecycleState",
                message="Seed/corpus records must be verified before import.",
                record_id=record_id,
            )
        )

    if record.sourceAttribution.sourceType in _CANDIDATE_ONLY_SOURCE_TYPES:
        issues.append(
            _issue(
                code="candidate_only_source_type",
                severity="error",
                path=f"{base_path}.sourceAttribution.sourceType",
                message="Candidate-only source types must not become seed/corpus truth.",
                record_id=record_id,
            )
        )
    for barcode_index, barcode_identity in enumerate(record.barcodeIdentities):
        if barcode_identity.sourceType in _CANDIDATE_ONLY_SOURCE_TYPES:
            issues.append(
                _issue(
                    code="candidate_only_source_type",
                    severity="error",
                    path=f"{base_path}.barcodeIdentities.{barcode_index}.sourceType",
                    message="Barcode identity source type is candidate-only.",
                    record_id=record_id,
                )
            )

    for confidence_field in ("identity", "nutrition", "profile"):
        confidence_value = getattr(record.confidence, confidence_field)
        if confidence_value in _LOW_REQUIRED_CONFIDENCE:
            issues.append(
                _issue(
                    code="low_required_confidence",
                    severity="error",
                    path=f"{base_path}.confidence.{confidence_field}",
                    message="Required seed/corpus confidence must not be low or unknown.",
                    record_id=record_id,
                )
            )

    if (
        record.nutritionPer100.basis == "per_100g"
        and record.nutritionPer100.unit != "g"
    ) or (
        record.nutritionPer100.basis == "per_100ml"
        and record.nutritionPer100.unit != "ml"
    ):
        issues.append(
            _issue(
                code="nutrition_basis_unit_mismatch",
                severity="error",
                path=f"{base_path}.nutritionPer100.unit",
                message="Nutrition basis and unit must match for per-100 values.",
                record_id=record_id,
            )
        )

    if record.nutritionPer100.kcal > _MAX_NUTRITION_KCAL_PER_100:
        issues.append(
            _issue(
                code="implausible_nutrition",
                severity="error",
                path=f"{base_path}.nutritionPer100.kcal",
                message="Nutrition kcal per 100 must stay within plausible food bounds.",
                record_id=record_id,
            )
        )
    for field_name in (
        "protein",
        "fat",
        "carbs",
        "fiber",
        "sugar",
        "salt",
        "saturatedFat",
    ):
        nutrient_value = getattr(record.nutritionPer100, field_name)
        if (
            nutrient_value is not None
            and nutrient_value > _MAX_NUTRIENT_GRAMS_PER_100
        ):
            issues.append(
                _issue(
                    code="implausible_nutrition",
                    severity="error",
                    path=f"{base_path}.nutritionPer100.{field_name}",
                    message="Nutrition gram values per 100 must not exceed 100.",
                    record_id=record_id,
                )
            )
    macro_total = (
        record.nutritionPer100.protein
        + record.nutritionPer100.fat
        + record.nutritionPer100.carbs
    )
    if macro_total > _MAX_NUTRIENT_GRAMS_PER_100:
        issues.append(
            _issue(
                code="implausible_nutrition",
                severity="error",
                path=f"{base_path}.nutritionPer100",
                message="Protein, fat, and carbs per 100 must not exceed 100g combined.",
                record_id=record_id,
            )
        )

    for path, value in _string_values(record):
        if _PLACEHOLDER_PATTERN.search(_normalize_search_value(value)):
            issues.append(
                _issue(
                    code="placeholder_content",
                    severity="error",
                    path=f"{base_path}.{path}",
                    message="Seed/corpus records must not contain placeholder content.",
                    record_id=record_id,
                )
            )

    for path, value in _source_attribution_values(record):
        if _AI_DERIVED_PATTERN.search(_normalize_search_value(value)):
            issues.append(
                _issue(
                    code="ai_derived_durable_nutrition",
                    severity="error",
                    path=f"{base_path}.{path}",
                    message="AI-derived values must not become durable nutrition truth.",
                    record_id=record_id,
                )
            )

    if record.kind == "generic_ingredient" and not (record.ingredientName or "").strip():
        issues.append(
            _issue(
                code="kind_specific_field_missing",
                severity="error",
                path=f"{base_path}.ingredientName",
                message="Generic ingredient records require ingredientName.",
                record_id=record_id,
            )
        )
    if record.kind == "branded_product" and not (record.brandName or "").strip():
        issues.append(
            _issue(
                code="kind_specific_field_missing",
                severity="error",
                path=f"{base_path}.brandName",
                message="Branded product records require brandName.",
                record_id=record_id,
            )
        )

    expected_prefixes = _search_prefixes(
        record.displayName,
        record.ingredientName,
        record.brandName,
        record.packageName,
        record.category,
    )
    required_prefixes = {prefix for prefix in expected_prefixes if not prefix.endswith(" ")}
    seen_prefixes: set[str] = set()
    for prefix_index, prefix in enumerate(record.searchPrefixes):
        path = f"{base_path}.searchPrefixes.{prefix_index}"
        normalized_prefix = _normalize_search_value(prefix)
        if (
            not normalized_prefix
            or normalized_prefix != prefix
            or len(normalized_prefix) < _MIN_SEARCH_PREFIX_LENGTH
            or normalized_prefix in seen_prefixes
            or normalized_prefix not in required_prefixes
        ):
            issues.append(
                _issue(
                    code="malformed_search_prefix",
                    severity="error",
                    path=path,
                    message="Search prefixes must be normalized, unique, and derivable from searchable fields.",
                    record_id=record_id,
            )
        )
        seen_prefixes.add(normalized_prefix)
    if not required_prefixes.issubset(seen_prefixes):
        issues.append(
            _issue(
                code="missing_required_search_prefix",
                severity="error",
                path=f"{base_path}.searchPrefixes",
                message="Search prefixes must include every derivable two-character prefix.",
                record_id=record_id,
            )
        )

    if record.resolved_profile_status() == "unknown":
        issues.append(
            _issue(
                code="profile_status_unknown",
                severity="warning",
                path=f"{base_path}.profileFlags",
                message="Profile compatibility is unknown and requires review before rollout confidence.",
                record_id=record_id,
            )
        )

    return issues


def _sorted_counts(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _coverage_notes(
    *,
    dataset_kind: FoodLibrarySeedDatasetKind,
    approved_production_corpus: bool,
    valid_record_count: int,
) -> list[str]:
    notes = [
        "Validated global Ingredient/Product record structure, source, confidence, nutrition, serving, lifecycle, scope, and search-prefix rules.",
        "Basic PL/EN query coverage and nutrition quality ownership still require human corpus review.",
    ]
    if dataset_kind == "production_corpus":
        if not approved_production_corpus:
            notes.append(
                "Approved production corpus metadata is missing; F1 data gate remains blocked."
            )
        if valid_record_count == 0:
            notes.append("No valid production corpus records were supplied.")
    else:
        notes.append(
            "Local E2E seed validation is emulator import evidence only, not approved production corpus evidence."
        )
    return notes


def validate_ingredient_product_seed_records(
    records: Sequence[RecordPayload],
    *,
    dataset_name: str,
    dataset_kind: FoodLibrarySeedDatasetKind = "production_corpus",
    approved_production_corpus: bool = False,
    document_ids: Sequence[str | None] | None = None,
) -> FoodLibrarySeedValidationReport:
    """Validate seed records and return deterministic import evidence."""

    issues: list[FoodLibrarySeedValidationIssue] = []
    source_types: Counter[str] = Counter()
    confidence_levels: Counter[str] = Counter()
    scope_counts: Counter[str] = Counter()
    lifecycle_counts: Counter[str] = Counter()
    valid_record_count = 0

    if document_ids is not None and len(document_ids) != len(records):
        raise ValueError("document_ids length must match records length.")

    if dataset_kind == "production_corpus" and not approved_production_corpus:
        issues.append(
            _issue(
                code="missing_approved_production_corpus",
                severity="error",
                path="dataset.approvedProductionCorpus",
                message="Production import requires explicit approved corpus metadata.",
                record_id=None,
            )
        )
    if not records:
        issues.append(
            _issue(
                code="empty_seed_corpus",
                severity="error",
                path="records",
                message="Seed/corpus validation requires at least one Ingredient/Product record.",
                record_id=None,
            )
        )

    for record_index, raw_record in enumerate(records):
        try:
            record = IngredientProductSeedRecord.model_validate(raw_record)
        except ValidationError as error:
            issues.extend(
                _schema_issues(
                    record_index=record_index,
                    raw_record=raw_record,
                    error=error,
                )
            )
            continue

        valid_record_count += 1
        source_types.update([record.sourceAttribution.sourceType])
        confidence_levels.update(
            [
                record.confidence.identity,
                record.confidence.nutrition,
                record.confidence.profile,
            ]
        )
        scope_counts.update([record.recordScope])
        lifecycle_counts.update([record.lifecycleState])
        document_id = document_ids[record_index] if document_ids is not None else None
        issues.extend(
            _record_issues(
                record_index=record_index,
                record=record,
                document_id=document_id,
            )
        )

    warning_count = sum(1 for issue in issues if issue.severity == "warning")
    has_errors = any(issue.severity == "error" for issue in issues)
    return FoodLibrarySeedValidationReport(
        datasetName=dataset_name,
        datasetKind=dataset_kind,
        summary=FoodLibrarySeedValidationSummary(
            recordCount=len(records),
            sourceTypes=_sorted_counts(source_types),
            confidenceLevels=_sorted_counts(confidence_levels),
            scopeCounts=_sorted_counts(scope_counts),
            lifecycleCounts=_sorted_counts(lifecycle_counts),
            warningCount=warning_count,
            coverageNotes=_coverage_notes(
                dataset_kind=dataset_kind,
                approved_production_corpus=approved_production_corpus,
                valid_record_count=valid_record_count,
            ),
        ),
        issues=sorted(
            issues,
            key=lambda issue: (
                issue.path,
                issue.code,
                issue.recordId or "",
                issue.message,
            ),
        ),
        hasErrors=has_errors,
    )


def validate_production_ingredient_product_corpus(
    records: Sequence[RecordPayload],
    *,
    dataset_name: str,
    approved_production_corpus: bool,
    document_ids: Sequence[str | None] | None = None,
) -> FoodLibrarySeedValidationReport:
    return validate_ingredient_product_seed_records(
        records,
        dataset_name=dataset_name,
        dataset_kind="production_corpus",
        approved_production_corpus=approved_production_corpus,
        document_ids=document_ids,
    )


def validate_ingredient_product_seed_file(
    path: Path,
    *,
    dataset_kind: FoodLibrarySeedDatasetKind = "production_corpus",
    approved_production_corpus: bool = False,
) -> FoodLibrarySeedValidationReport:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        records = cast(list[RecordPayload], payload)
    elif isinstance(payload, dict):
        raw_payload = cast(dict[str, object], payload)
        raw_records = raw_payload.get("records")
        if not isinstance(raw_records, list):
            raise ValueError(
                "Seed/corpus file must be a JSON array or object with records."
            )
        records = cast(list[RecordPayload], raw_records)
    else:
        raise ValueError("Seed/corpus file must be a JSON array or object with records.")
    return validate_ingredient_product_seed_records(
        records,
        dataset_name=path.name,
        dataset_kind=dataset_kind,
        approved_production_corpus=approved_production_corpus,
    )


def raise_for_seed_validation_errors(
    report: FoodLibrarySeedValidationReport,
) -> None:
    if report.hasErrors:
        raise FoodLibrarySeedValidationError(report)
