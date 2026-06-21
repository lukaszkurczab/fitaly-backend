from __future__ import annotations

import re
from typing import Any
import unicodedata

import pytest

from app.services.food_library_seed_validator import (
    FoodLibrarySeedValidationIssueCode,
    FoodLibrarySeedValidationReport,
    validate_ingredient_product_seed_records,
    validate_production_ingredient_product_corpus,
)


def _normalize_search_value(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().lower())
    without_marks = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", without_marks).strip()


def _search_prefixes(*values: str | None) -> list[str]:
    prefixes: set[str] = set()
    for value in values:
        if not value:
            continue
        normalized = _normalize_search_value(value)
        if len(normalized) >= 2:
            for end_index in range(2, len(normalized) + 1):
                prefix = normalized[:end_index]
                if not prefix.endswith(" "):
                    prefixes.add(prefix)
        for token in normalized.split():
            if len(token) < 2:
                continue
            for end_index in range(2, len(token) + 1):
                prefixes.add(token[:end_index])
    return sorted(prefixes)


def _valid_record(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "ingredientProductId": "seed-oats",
        "recordScope": "global_seed",
        "lifecycleState": "verified",
        "displayName": "Owies gorski",
        "kind": "generic_ingredient",
        "ingredientName": "Owies",
        "brandName": None,
        "packageName": None,
        "category": "grain",
        "defaultServing": {"quantity": 50, "unit": "g"},
        "nutritionPer100": {
            "basis": "per_100g",
            "unit": "g",
            "kcal": 389,
            "protein": 16.9,
            "fat": 6.9,
            "carbs": 66.3,
        },
        "sourceAttribution": {
            "sourceType": "internal_seed",
            "sourceId": "reviewed-oats-seed",
            "sourceName": "Fitaly reviewed seed",
            "reviewedAt": "2026-06-15T10:30:00.000Z",
            "reviewedBy": "nutrition-review",
        },
        "confidence": {
            "identity": "verified",
            "nutrition": "high",
            "profile": "high",
        },
        "profileFlags": {
            "compatibilityStatus": "compatible",
            "dietaryFlags": [],
            "allergenFlags": [],
        },
        "warningReasonCodes": [],
        "servingSizes": [],
        "dietaryFlags": [],
        "allergenFlags": [],
        "createdAt": "2026-06-15T10:30:00.000Z",
        "updatedAt": "2026-06-15T10:30:00.000Z",
    }
    record["searchPrefixes"] = _search_prefixes(
        str(record["displayName"]),
        str(record["ingredientName"]),
        None,
        None,
        str(record["category"]),
    )
    if overrides:
        record.update(overrides)
    return record


def _issue_codes(
    report: FoodLibrarySeedValidationReport,
) -> set[FoodLibrarySeedValidationIssueCode]:
    return {issue.code for issue in report.issues}


def test_accepts_reviewed_global_seed_and_returns_deterministic_summary() -> None:
    report = validate_production_ingredient_product_corpus(
        [_valid_record()],
        dataset_name="approved-food-library-corpus",
        approved_production_corpus=True,
        document_ids=["seed-oats"],
    )

    assert report.hasErrors is False
    assert report.issues == []
    assert report.summary.recordCount == 1
    assert report.summary.sourceTypes == {"internal_seed": 1}
    assert report.summary.confidenceLevels == {"high": 2, "verified": 1}
    assert report.summary.scopeCounts == {"global_seed": 1}
    assert report.summary.lifecycleCounts == {"verified": 1}
    assert report.summary.warningCount == 0
    assert report.summary.coverageNotes == [
        "Validated global Ingredient/Product record structure, source, confidence, nutrition, serving, lifecycle, scope, and search-prefix rules.",
        "Basic PL/EN query coverage and nutrition quality ownership still require human corpus review.",
    ]


@pytest.mark.parametrize(
    "field_name",
    ["sourceAttribution", "confidence", "nutritionPer100", "defaultServing"],
)
def test_rejects_missing_required_seed_contract_fields(field_name: str) -> None:
    record = _valid_record()
    del record[field_name]

    report = validate_production_ingredient_product_corpus(
        [record],
        dataset_name="broken-corpus",
        approved_production_corpus=True,
    )

    assert "schema_error" in _issue_codes(report)
    assert report.hasErrors is True


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("global_owner_leakage", "global_owner_leakage"),
        ("user_scoped_record", "user_scoped_record"),
        ("candidate_lifecycle", "invalid_lifecycle_state"),
        ("rejected_lifecycle", "invalid_lifecycle_state"),
        ("barcode_identity_source", "candidate_only_source_type"),
        ("runtime_ai_source", "candidate_only_source_type"),
        ("placeholder_content", "placeholder_content"),
        ("low_confidence", "low_required_confidence"),
        ("unknown_confidence", "low_required_confidence"),
        ("malformed_search_prefix", "malformed_search_prefix"),
        ("missing_search_prefix", "missing_required_search_prefix"),
        ("ai_derived_nutrition_truth", "ai_derived_durable_nutrition"),
        ("missing_kind_specific_field", "kind_specific_field_missing"),
        ("unsafe_document_id", "unsafe_document_id"),
        ("global_user_created_source", "invalid_global_source_type"),
        ("external_source_placeholder", "placeholder_content"),
        ("barcode_placeholder", "placeholder_content"),
        ("barcode_observed_at_placeholder", "placeholder_content"),
        ("source_observed_at_placeholder", "placeholder_content"),
        ("source_reviewed_at_placeholder", "placeholder_content"),
        ("source_reviewed_by_placeholder", "placeholder_content"),
        ("nutrition_basis_unit_mismatch", "nutrition_basis_unit_mismatch"),
        ("implausible_kcal", "implausible_nutrition"),
        ("implausible_macro", "implausible_nutrition"),
        ("implausible_macro_sum", "implausible_nutrition"),
    ],
)
def test_rejects_unsafe_production_seed_records(
    case: str,
    expected_code: FoodLibrarySeedValidationIssueCode,
) -> None:
    record = _valid_record()
    if case == "global_owner_leakage":
        record["ownerUserId"] = "user-1"
    elif case == "user_scoped_record":
        record["recordScope"] = "user_scoped"
        record["ownerUserId"] = "user-1"
    elif case == "candidate_lifecycle":
        record["lifecycleState"] = "candidate"
    elif case == "rejected_lifecycle":
        record["lifecycleState"] = "rejected"
    elif case == "barcode_identity_source":
        record["sourceAttribution"] = {
            **record["sourceAttribution"],
            "sourceType": "barcode_identity",
        }
    elif case == "runtime_ai_source":
        record["sourceAttribution"] = {
            **record["sourceAttribution"],
            "sourceType": "runtime_ai_candidate",
        }
    elif case == "placeholder_content":
        record["displayName"] = "TODO oats"
        record["searchPrefixes"] = ["to", "tod", "todo", "todo oats", "oa", "oats"]
    elif case == "low_confidence":
        record["confidence"] = {**record["confidence"], "nutrition": "low"}
    elif case == "unknown_confidence":
        record["confidence"] = {**record["confidence"], "profile": "unknown"}
    elif case == "malformed_search_prefix":
        record["searchPrefixes"] = ["OW", "not-from-record", "owies gorski"]
    elif case == "missing_search_prefix":
        record["searchPrefixes"] = ["owies gorski"]
    elif case == "ai_derived_nutrition_truth":
        record["sourceAttribution"] = {
            **record["sourceAttribution"],
            "sourceName": "OpenAI generated nutrition review",
        }
    elif case == "missing_kind_specific_field":
        record["ingredientName"] = None
    elif case == "unsafe_document_id":
        record["ingredientProductId"] = "seed/oats"
    elif case == "global_user_created_source":
        record["sourceAttribution"] = {
            **record["sourceAttribution"],
            "sourceType": "user_created",
        }
    elif case == "external_source_placeholder":
        record["externalSourceIds"] = {"provider": "TODO"}
    elif case == "barcode_placeholder":
        record["barcodeIdentities"] = [
            {
                "barcode": "1234567890123",
                "format": "EAN_13",
                "sourceType": "external_provider",
                "sourceId": "placeholder-barcode",
            }
        ]
    elif case == "barcode_observed_at_placeholder":
        record["barcodeIdentities"] = [
            {
                "barcode": "1234567890123",
                "format": "EAN_13",
                "sourceType": "external_provider",
                "sourceId": "reviewed-barcode",
                "observedAt": "TODO",
            }
        ]
    elif case == "source_observed_at_placeholder":
        record["sourceAttribution"] = {
            **record["sourceAttribution"],
            "observedAt": "TODO",
        }
    elif case == "source_reviewed_at_placeholder":
        record["sourceAttribution"] = {
            **record["sourceAttribution"],
            "reviewedAt": "TODO",
        }
    elif case == "source_reviewed_by_placeholder":
        record["sourceAttribution"] = {
            **record["sourceAttribution"],
            "reviewedBy": "TODO",
        }
    elif case == "nutrition_basis_unit_mismatch":
        record["nutritionPer100"] = {**record["nutritionPer100"], "unit": "piece"}
    elif case == "implausible_kcal":
        record["nutritionPer100"] = {**record["nutritionPer100"], "kcal": 9999}
    elif case == "implausible_macro":
        record["nutritionPer100"] = {**record["nutritionPer100"], "protein": 999}
    elif case == "implausible_macro_sum":
        record["nutritionPer100"] = {
            **record["nutritionPer100"],
            "protein": 80,
            "fat": 80,
            "carbs": 80,
        }

    report = validate_production_ingredient_product_corpus(
        [record],
        dataset_name="unsafe-corpus",
        approved_production_corpus=True,
    )

    assert expected_code in _issue_codes(report)
    assert report.hasErrors is True


def test_production_corpus_without_approval_remains_data_gate_blocker() -> None:
    report = validate_production_ingredient_product_corpus(
        [_valid_record()],
        dataset_name="unapproved-food-library-corpus",
        approved_production_corpus=False,
    )

    assert report.hasErrors is True
    assert "missing_approved_production_corpus" in _issue_codes(report)
    assert report.summary.recordCount == 1
    assert (
        "Approved production corpus metadata is missing; F1 data gate remains blocked."
        in report.summary.coverageNotes
    )


def test_local_e2e_seed_uses_same_record_checks_without_approval_claim() -> None:
    report = validate_ingredient_product_seed_records(
        [_valid_record()],
        dataset_name="ingredient-autocomplete-local-e2e",
        dataset_kind="local_e2e_seed",
    )

    assert report.hasErrors is False
    assert report.summary.recordCount == 1
    assert (
        "Local E2E seed validation is emulator import evidence only, not approved production corpus evidence."
        in report.summary.coverageNotes
    )
