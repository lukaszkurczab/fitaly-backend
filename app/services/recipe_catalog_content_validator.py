"""Validation and loading for versioned Recipe Catalog content packs."""

from __future__ import annotations

from collections import Counter
import json
from json import JSONDecodeError
from pathlib import Path
import re
from typing import Literal, cast
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.schemas.recipes import RecipeCatalogRecord


RecipeCatalogContentValidationSeverity = Literal["error"]
RecipeCatalogContentValidationIssueCode = Literal[
    "content_path_unavailable",
    "disallowed_content_path",
    "relative_content_path",
    "invalid_json",
    "pack_schema_error",
    "record_schema_error",
    "missing_approved_content_metadata",
    "unapproved_content_pack",
    "empty_content_pack",
    "missing_source_attribution",
    "missing_review_metadata",
    "duplicate_recipe_version",
    "inactive_content_record",
    "unready_review_state",
    "locale_mismatch",
    "fixture_language_mismatch",
    "placeholder_content",
    "artificial_ingredient_snapshot",
    "unsafe_nutrition",
    "inconsistent_profile_flags",
]

RecipeCatalogContentSchemaVersion = Literal[1]

_PLACEHOLDER_PATTERN = re.compile(
    r"(^|[\s_\-:/])(?:todo|tbd|placeholder|sample|dummy|mock|lorem)([\s_\-:/]|$)"
)
_FOUNDATION_PHRASES = {
    "curated recipe catalog foundation record",
    "fitaly curated foundation",
    "recipe catalog foundation",
    "curated ingredient snapshot",
    "prepare curated recipe",
}
_PL_PL_REJECTED_ENGLISH_FOUNDATION_TITLES = {
    "vegan lentil bowl",
    "salmon rice plate",
    "tofu vegetable stir fry",
    "berry yogurt bowl",
    "peanut oat bars",
    "chickpea vegetable pan",
    "chicken quinoa salad",
}
_PL_PL_REJECTED_ENGLISH_FOUNDATION_STEPS = {
    "prepare curated recipe",
}
_DISALLOWED_CONTENT_PATH_PARTS = {
    "__fixtures__",
    "fixture",
    "fixtures",
    "test",
    "tests",
}


class RecipeCatalogContentApprovalMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    approved: bool
    approvedAt: str = Field(min_length=1, max_length=64)
    approvedBy: str = Field(min_length=1, max_length=128)


class RecipeCatalogContentReviewMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reviewedAt: str = Field(min_length=1, max_length=64)
    reviewedBy: str = Field(min_length=1, max_length=128)
    reviewSource: str | None = Field(default=None, max_length=128)


class RecipeCatalogContentPack(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schemaVersion: RecipeCatalogContentSchemaVersion
    contentVersion: str = Field(min_length=1, max_length=64)
    locale: str = Field(min_length=2, max_length=16)
    approval: RecipeCatalogContentApprovalMetadata
    review: RecipeCatalogContentReviewMetadata
    records: list[RecipeCatalogRecord]


class RecipeCatalogContentValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: RecipeCatalogContentValidationIssueCode
    severity: RecipeCatalogContentValidationSeverity
    path: str
    message: str
    recipeId: str | None = None
    version: int | None = None


class RecipeCatalogContentValidationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: int | None
    contentVersion: str | None
    locale: str | None
    recordCount: int
    lifecycleCounts: dict[str, int]
    nutritionConfidenceCounts: dict[str, int]
    issueCodes: list[RecipeCatalogContentValidationIssueCode]


class RecipeCatalogContentValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    packName: str
    summary: RecipeCatalogContentValidationSummary
    issues: list[RecipeCatalogContentValidationIssue]
    hasErrors: bool


class RecipeCatalogContentValidationError(ValueError):
    """Raised when configured Recipe Catalog content cannot be used."""

    def __init__(self, report: RecipeCatalogContentValidationReport) -> None:
        self.report = report
        super().__init__(
            f"{report.packName} failed Recipe Catalog content validation "
            f"with {len(report.issues)} error(s)."
        )


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().lower())
    without_marks = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", without_marks).strip()


def _phrase_text(value: str) -> str:
    return re.sub(r"[\s_\-:/]+", " ", _normalize_text(value)).strip()


def _contains_placeholder(value: str) -> bool:
    normalized = _normalize_text(value)
    phrase_text = _phrase_text(value)
    return bool(_PLACEHOLDER_PATTERN.search(normalized)) or any(
        phrase in phrase_text for phrase in _FOUNDATION_PHRASES
    )


def _record_id(raw_record: object) -> str | None:
    if not isinstance(raw_record, dict):
        return None
    raw_value = cast(dict[str, object], raw_record).get("recipeId")
    if isinstance(raw_value, str) and raw_value.strip():
        return raw_value.strip()
    return None


def _record_version(raw_record: object) -> int | None:
    if not isinstance(raw_record, dict):
        return None
    raw_value = cast(dict[str, object], raw_record).get("version")
    if isinstance(raw_value, int):
        return raw_value
    return None


def _raw_records(payload: object) -> list[object]:
    if not isinstance(payload, dict):
        return []
    raw_records = cast(dict[str, object], payload).get("records")
    if not isinstance(raw_records, list):
        return []
    return list(cast(list[object], raw_records))


def _raw_string(payload: object, key: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    raw_value = cast(dict[str, object], payload).get(key)
    if isinstance(raw_value, str) and raw_value.strip():
        return raw_value.strip()
    return None


def _raw_int(payload: object, key: str) -> int | None:
    if not isinstance(payload, dict):
        return None
    raw_value = cast(dict[str, object], payload).get(key)
    if isinstance(raw_value, int):
        return raw_value
    return None


def _issue(
    *,
    code: RecipeCatalogContentValidationIssueCode,
    path: str,
    message: str,
    recipe_id: str | None = None,
    version: int | None = None,
) -> RecipeCatalogContentValidationIssue:
    return RecipeCatalogContentValidationIssue(
        code=code,
        severity="error",
        path=path,
        message=message,
        recipeId=recipe_id,
        version=version,
    )


def _issue_path(loc: object) -> str:
    if not isinstance(loc, tuple) or not loc:
        return "$"

    parts: list[str] = []
    for part in cast(tuple[object, ...], loc):
        if isinstance(part, int) and parts:
            parts[-1] = f"{parts[-1]}[{part}]"
            continue
        parts.append(str(part))
    return ".".join(parts)


def _schema_issue_code(
    loc: tuple[object, ...],
) -> RecipeCatalogContentValidationIssueCode:
    if "approval" in loc:
        return "missing_approved_content_metadata"
    if "review" in loc and "reviewedAt" in loc:
        return "missing_review_metadata"
    if "records" not in loc:
        return "pack_schema_error"
    if "sourceAttribution" in loc:
        if "reviewedAt" in loc:
            return "missing_review_metadata"
        return "missing_source_attribution"
    return "record_schema_error"


def _schema_issues(
    *,
    payload: object,
    error: ValidationError,
) -> list[RecipeCatalogContentValidationIssue]:
    records = _raw_records(payload)
    issues: list[RecipeCatalogContentValidationIssue] = []
    for validation_error in error.errors():
        loc = validation_error.get("loc", ())
        loc_tuple = loc if isinstance(loc, tuple) else ()
        record_id: str | None = None
        version: int | None = None
        if (
            len(loc_tuple) >= 2
            and loc_tuple[0] == "records"
            and isinstance(loc_tuple[1], int)
            and 0 <= loc_tuple[1] < len(records)
        ):
            raw_record = records[loc_tuple[1]]
            record_id = _record_id(raw_record)
            version = _record_version(raw_record)
        issues.append(
            _issue(
                code=_schema_issue_code(loc_tuple),
                path=_issue_path(loc),
                message=str(validation_error.get("msg", "schema validation failed")),
                recipe_id=record_id,
                version=version,
            )
        )
    return issues


def _metadata_issues(payload: object) -> list[RecipeCatalogContentValidationIssue]:
    if not isinstance(payload, dict):
        return [
            _issue(
                code="pack_schema_error",
                path="$",
                message="Recipe Catalog content pack must be a JSON object.",
            )
        ]

    raw_payload = cast(dict[str, object], payload)
    issues: list[RecipeCatalogContentValidationIssue] = []
    raw_approval = raw_payload.get("approval")
    if not isinstance(raw_approval, dict):
        issues.append(
            _issue(
                code="missing_approved_content_metadata",
                path="approval",
                message="Content pack requires approval metadata.",
            )
        )
    else:
        approval = cast(dict[str, object], raw_approval)
        has_approval_metadata = (
            "approved" in approval
            and isinstance(approval.get("approvedAt"), str)
            and bool(cast(str, approval.get("approvedAt")).strip())
            and isinstance(approval.get("approvedBy"), str)
            and bool(cast(str, approval.get("approvedBy")).strip())
        )
        if not has_approval_metadata:
            issues.append(
                _issue(
                    code="missing_approved_content_metadata",
                    path="approval",
                    message="Content pack requires approved, approvedAt, and approvedBy metadata.",
                )
            )
        elif approval.get("approved") is not True:
            issues.append(
                _issue(
                    code="unapproved_content_pack",
                    path="approval.approved",
                    message="Content pack must be explicitly approved before runtime use.",
                )
            )

    raw_review = raw_payload.get("review")
    if not isinstance(raw_review, dict):
        issues.append(
            _issue(
                code="missing_review_metadata",
                path="review",
                message="Content pack requires review metadata.",
            )
        )
    else:
        review = cast(dict[str, object], raw_review)
        if not (
            isinstance(review.get("reviewedAt"), str)
            and bool(cast(str, review.get("reviewedAt")).strip())
            and isinstance(review.get("reviewedBy"), str)
            and bool(cast(str, review.get("reviewedBy")).strip())
        ):
            issues.append(
                _issue(
                    code="missing_review_metadata",
                    path="review",
                    message="Content pack requires reviewedAt and reviewedBy metadata.",
                )
            )

    return issues


def _record_string_values(record: RecipeCatalogRecord) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = [
        ("recipeId", record.recipeId),
        ("title", record.title),
        ("yield", record.yieldText),
        ("sourceAttribution.sourceId", record.sourceAttribution.sourceId),
        ("sourceAttribution.sourceName", record.sourceAttribution.sourceName),
        ("sourceAttribution.reviewedAt", record.sourceAttribution.reviewedAt),
    ]
    if record.description:
        values.append(("description", record.description))
    if record.imageRef:
        values.append(("imageRef", record.imageRef))
    for index, ingredient in enumerate(record.ingredients):
        values.append((f"ingredients.{index}.snapshotName", ingredient.snapshotName))
        if ingredient.ingredientProductId:
            values.append(
                (
                    f"ingredients.{index}.ingredientProductId",
                    ingredient.ingredientProductId,
                )
            )
    for index, step in enumerate(record.steps):
        values.append((f"steps.{index}", step))
    return values


def _record_content_issues(
    *,
    record_index: int,
    record: RecipeCatalogRecord,
    pack_locale: str,
    seen_versions: set[tuple[str, int]],
) -> list[RecipeCatalogContentValidationIssue]:
    issues: list[RecipeCatalogContentValidationIssue] = []
    base_path = f"records[{record_index}]"
    record_id = record.recipeId
    record_version = record.version

    if record.lifecycleState != "active":
        issues.append(
            _issue(
                code="inactive_content_record",
                path=f"{base_path}.lifecycleState",
                message="Runtime Recipe Catalog content records must be active.",
                recipe_id=record_id,
                version=record_version,
            )
        )

    if record.reviewState != "curated":
        issues.append(
            _issue(
                code="unready_review_state",
                path=f"{base_path}.reviewState",
                message="Runtime Recipe Catalog content records must be curated.",
                recipe_id=record_id,
                version=record_version,
            )
        )

    if (record.recipeId, record.version) in seen_versions:
        issues.append(
            _issue(
                code="duplicate_recipe_version",
                path=f"{base_path}.version",
                message="Recipe content packs must not contain duplicate recipeId/version pairs.",
                recipe_id=record_id,
                version=record_version,
            )
        )
    seen_versions.add((record.recipeId, record.version))

    if record.locale != pack_locale:
        issues.append(
            _issue(
                code="locale_mismatch",
                path=f"{base_path}.locale",
                message="Record locale must match content pack locale.",
                recipe_id=record_id,
                version=record_version,
            )
        )

    if not record.sourceAttribution.reviewedAt.strip():
        issues.append(
            _issue(
                code="missing_review_metadata",
                path=f"{base_path}.sourceAttribution.reviewedAt",
                message="Recipe source attribution requires reviewedAt metadata.",
                recipe_id=record_id,
                version=record_version,
            )
        )

    for path, value in _record_string_values(record):
        if _contains_placeholder(value):
            issues.append(
                _issue(
                    code="placeholder_content",
                    path=f"{base_path}.{path}",
                    message="Recipe Catalog content must not contain placeholder or foundation sample content.",
                    recipe_id=record_id,
                    version=record_version,
                )
            )

    if len(record.ingredients) == 1:
        ingredient_name = _phrase_text(record.ingredients[0].snapshotName)
        if (
            "curated" in ingredient_name
            or "foundation" in ingredient_name
            or "ingredient snapshot" in ingredient_name
            or ingredient_name == "ingredient"
        ):
            issues.append(
                _issue(
                    code="artificial_ingredient_snapshot",
                    path=f"{base_path}.ingredients[0].snapshotName",
                    message="Recipe records must not be single artificial ingredient snapshots.",
                    recipe_id=record_id,
                    version=record_version,
                )
            )

    if pack_locale == "pl-PL":
        normalized_title = _phrase_text(record.title)
        if normalized_title in _PL_PL_REJECTED_ENGLISH_FOUNDATION_TITLES:
            issues.append(
                _issue(
                    code="fixture_language_mismatch",
                    path=f"{base_path}.title",
                    message="pl-PL content packs must not use known English foundation fixture titles.",
                    recipe_id=record_id,
                    version=record_version,
                )
            )
        for step_index, step in enumerate(record.steps):
            if _phrase_text(step) in _PL_PL_REJECTED_ENGLISH_FOUNDATION_STEPS:
                issues.append(
                    _issue(
                        code="fixture_language_mismatch",
                        path=f"{base_path}.steps.{step_index}",
                        message="pl-PL content packs must not use known English foundation fixture steps.",
                        recipe_id=record_id,
                        version=record_version,
                    )
                )

    nutrition = record.nutritionSnapshot
    if nutrition.confidence in {"unknown", "low"}:
        issues.append(
            _issue(
                code="unsafe_nutrition",
                path=f"{base_path}.nutritionSnapshot.confidence",
                message="Recipe nutrition confidence must not be unknown or low.",
                recipe_id=record_id,
                version=record_version,
            )
        )
    if nutrition.isPartial:
        issues.append(
            _issue(
                code="unsafe_nutrition",
                path=f"{base_path}.nutritionSnapshot.isPartial",
                message="Recipe nutrition must not be partial.",
                recipe_id=record_id,
                version=record_version,
            )
        )
    if (
        nutrition.kcal == 0
        and nutrition.proteinGrams == 0
        and nutrition.fatGrams == 0
        and nutrition.carbsGrams == 0
    ):
        issues.append(
            _issue(
                code="unsafe_nutrition",
                path=f"{base_path}.nutritionSnapshot",
                message="Recipe nutrition must not have all kcal and macro values set to zero.",
                recipe_id=record_id,
                version=record_version,
            )
        )

    has_unknown_flags = bool(
        record.unknownDietaryFlags or record.unknownAllergenFlags
    )
    if record.profileFlagState == "complete" and has_unknown_flags:
        issues.append(
            _issue(
                code="inconsistent_profile_flags",
                path=f"{base_path}.profileFlagState",
                message="Complete profile flag state cannot include unknown flags.",
                recipe_id=record_id,
                version=record_version,
            )
        )
    if record.profileFlagState in {"partial", "unknown"} and not has_unknown_flags:
        issues.append(
            _issue(
                code="inconsistent_profile_flags",
                path=f"{base_path}.profileFlagState",
                message="Partial or unknown profile flag state must declare at least one unknown flag.",
                recipe_id=record_id,
                version=record_version,
            )
        )

    return issues


def _sorted_counts(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _dedupe_issues(
    issues: list[RecipeCatalogContentValidationIssue],
) -> list[RecipeCatalogContentValidationIssue]:
    unique_issues: dict[
        tuple[RecipeCatalogContentValidationIssueCode, str, str | None, int | None, str],
        RecipeCatalogContentValidationIssue,
    ] = {}
    for issue in issues:
        unique_issues[
            (
                issue.code,
                issue.path,
                issue.recipeId,
                issue.version,
                issue.message,
            )
        ] = issue
    return list(unique_issues.values())


def _build_report(
    *,
    pack_name: str,
    payload: object,
    records: tuple[RecipeCatalogRecord, ...],
    issues: list[RecipeCatalogContentValidationIssue],
) -> RecipeCatalogContentValidationReport:
    lifecycle_counts: Counter[str] = Counter()
    nutrition_confidence_counts: Counter[str] = Counter()
    for record in records:
        lifecycle_counts.update([record.lifecycleState])
        nutrition_confidence_counts.update([record.nutritionSnapshot.confidence])

    sorted_issues = sorted(
        _dedupe_issues(issues),
        key=lambda issue: (
            issue.path,
            issue.code,
            issue.recipeId or "",
            issue.version or 0,
            issue.message,
        ),
    )
    return RecipeCatalogContentValidationReport(
        packName=pack_name,
        summary=RecipeCatalogContentValidationSummary(
            schemaVersion=_raw_int(payload, "schemaVersion"),
            contentVersion=_raw_string(payload, "contentVersion"),
            locale=_raw_string(payload, "locale"),
            recordCount=len(_raw_records(payload)) if not records else len(records),
            lifecycleCounts=_sorted_counts(lifecycle_counts),
            nutritionConfidenceCounts=_sorted_counts(nutrition_confidence_counts),
            issueCodes=sorted({issue.code for issue in sorted_issues}),
        ),
        issues=sorted_issues,
        hasErrors=bool(sorted_issues),
    )


def _validate_payload(
    payload: object,
    *,
    pack_name: str,
) -> tuple[RecipeCatalogContentValidationReport, tuple[RecipeCatalogRecord, ...]]:
    issues = _metadata_issues(payload)
    try:
        pack = RecipeCatalogContentPack.model_validate(payload)
    except ValidationError as error:
        issues.extend(_schema_issues(payload=payload, error=error))
        return (
            _build_report(
                pack_name=pack_name,
                payload=payload,
                records=(),
                issues=issues,
            ),
            (),
        )

    records = tuple(pack.records)
    if not pack.approval.approved:
        issues.append(
            _issue(
                code="unapproved_content_pack",
                path="approval.approved",
                message="Content pack must be explicitly approved before runtime use.",
            )
        )
    if not pack.review.reviewedAt.strip() or not pack.review.reviewedBy.strip():
        issues.append(
            _issue(
                code="missing_review_metadata",
                path="review",
                message="Content pack requires reviewedAt and reviewedBy metadata.",
            )
        )
    if not records:
        issues.append(
            _issue(
                code="empty_content_pack",
                path="records",
                message="Configured Recipe Catalog content packs must contain at least one record.",
            )
        )

    seen_versions: set[tuple[str, int]] = set()
    for record_index, record in enumerate(records):
        issues.extend(
            _record_content_issues(
                record_index=record_index,
                record=record,
                pack_locale=pack.locale,
                seen_versions=seen_versions,
            )
        )

    return (
        _build_report(
            pack_name=pack_name,
            payload=payload,
            records=records,
            issues=issues,
        ),
        records,
    )


def validate_recipe_catalog_content_pack(
    payload: object,
    *,
    pack_name: str = "recipe-catalog-content-pack",
) -> RecipeCatalogContentValidationReport:
    report, _ = _validate_payload(payload, pack_name=pack_name)
    return report


def _path_issues(path: Path) -> list[RecipeCatalogContentValidationIssue]:
    expanded_path = path.expanduser()
    if not expanded_path.is_absolute():
        return [
            _issue(
                code="relative_content_path",
                path="contentPath",
                message="Recipe Catalog content path must be absolute.",
            )
        ]

    resolved_path = expanded_path.resolve(strict=False)
    normalized_parts = {part.lower() for part in resolved_path.parts}
    if normalized_parts.intersection(_DISALLOWED_CONTENT_PATH_PARTS):
        return [
            _issue(
                code="disallowed_content_path",
                path="contentPath",
                message="Recipe Catalog content path must not point at tests or fixtures.",
            )
        ]
    return []


def _configured_path(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    if isinstance(path, Path):
        return path
    stripped = path.strip()
    if not stripped:
        return None
    return Path(stripped)


def _validate_file(
    path: Path,
) -> tuple[RecipeCatalogContentValidationReport, tuple[RecipeCatalogRecord, ...]]:
    path_issues = _path_issues(path)
    if path_issues:
        return (
            _build_report(
                pack_name=path.name or "recipe-catalog-content-pack",
                payload={},
                records=(),
                issues=path_issues,
            ),
            (),
        )

    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return (
            _build_report(
                pack_name=path.name or "recipe-catalog-content-pack",
                payload={},
                records=(),
                issues=[
                    _issue(
                        code="content_path_unavailable",
                        path="contentPath",
                        message="Configured Recipe Catalog content path is unavailable.",
                    )
                ],
            ),
            (),
        )
    except JSONDecodeError:
        return (
            _build_report(
                pack_name=path.name or "recipe-catalog-content-pack",
                payload={},
                records=(),
                issues=[
                    _issue(
                        code="invalid_json",
                        path="contentPath",
                        message="Configured Recipe Catalog content pack is not valid JSON.",
                    )
                ],
            ),
            (),
        )
    except OSError:
        return (
            _build_report(
                pack_name=path.name or "recipe-catalog-content-pack",
                payload={},
                records=(),
                issues=[
                    _issue(
                        code="content_path_unavailable",
                        path="contentPath",
                        message="Configured Recipe Catalog content path is unavailable.",
                    )
                ],
            ),
            (),
        )

    return _validate_payload(
        payload,
        pack_name=path.name or "recipe-catalog-content-pack",
    )


def validate_recipe_catalog_content_file(
    path: str | Path,
) -> RecipeCatalogContentValidationReport:
    report, _ = _validate_file(Path(path))
    return report


def raise_for_recipe_catalog_content_errors(
    report: RecipeCatalogContentValidationReport,
) -> None:
    if report.hasErrors:
        raise RecipeCatalogContentValidationError(report)


def load_recipe_catalog_content(
    path: str | Path | None,
) -> tuple[RecipeCatalogRecord, ...]:
    configured_path = _configured_path(path)
    if configured_path is None:
        return ()

    report, records = _validate_file(configured_path)
    raise_for_recipe_catalog_content_errors(report)
    return records
