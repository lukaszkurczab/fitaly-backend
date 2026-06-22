from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.services.recipe_catalog_content_validator import (
    RecipeCatalogContentValidationError,
    RecipeCatalogContentValidationIssueCode,
    RecipeCatalogContentValidationReport,
    load_recipe_catalog_content,
    validate_recipe_catalog_content_pack,
)


def _valid_record(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "recipeId": "kasza-warzywa-r1c",
        "version": 1,
        "lifecycleState": "active",
        "locale": "pl-PL",
        "title": "Kasza z warzywami",
        "description": "Przejrzany obiad z kaszy i warzyw.",
        "servings": 2,
        "yield": "2 porcje",
        "sourceAttribution": {
            "sourceType": "internal_curated",
            "sourceId": "r1c-review-kasza-warzywa",
            "sourceName": "Fitaly R1C review",
            "reviewedAt": "2026-06-20T10:00:00.000Z",
        },
        "updatedAt": "2026-06-20T10:00:00.000Z",
        "reviewState": "curated",
        "ingredients": [
            {
                "ingredientProductId": None,
                "snapshotName": "Kasza gryczana",
                "quantity": 120,
                "unit": "g",
            }
        ],
        "steps": ["Ugotuj kasze i wymieszaj z warzywami."],
        "prepTimeMin": 10,
        "cookTimeMin": 20,
        "nutritionSnapshot": {
            "kcal": 420,
            "proteinGrams": 18,
            "fatGrams": 12,
            "carbsGrams": 58,
            "confidence": "medium",
            "isPartial": False,
        },
        "imageRef": None,
        "profileFlagState": "complete",
        "dietaryFlags": ["vegetarian"],
        "allergenFlags": [],
        "unknownDietaryFlags": [],
        "unknownAllergenFlags": [],
        "styleTags": ["balanced"],
    }
    if overrides:
        record.update(overrides)
    return record


def _valid_pack(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    pack: dict[str, Any] = {
        "schemaVersion": 1,
        "contentVersion": "r1c-test-pack-v1",
        "locale": "pl-PL",
        "approval": {
            "approved": True,
            "approvedAt": "2026-06-20T10:30:00.000Z",
            "approvedBy": "nutrition-review",
        },
        "review": {
            "reviewedAt": "2026-06-20T10:30:00.000Z",
            "reviewedBy": "nutrition-review",
            "reviewSource": "r1c-review",
        },
        "records": [_valid_record()],
    }
    if overrides:
        pack.update(overrides)
    return pack


def _write_pack(path: Path, pack: dict[str, Any]) -> None:
    path.write_text(json.dumps(pack), encoding="utf-8")


def _issue_codes(
    report: RecipeCatalogContentValidationReport,
) -> set[RecipeCatalogContentValidationIssueCode]:
    return {issue.code for issue in report.issues}


def _assert_pack_rejected(
    pack: dict[str, Any],
    expected_code: RecipeCatalogContentValidationIssueCode,
) -> RecipeCatalogContentValidationReport:
    report = validate_recipe_catalog_content_pack(pack)

    assert report.hasErrors is True
    assert expected_code in _issue_codes(report)
    assert expected_code in report.summary.issueCodes
    return report


def test_valid_pack_loads_records_from_configured_path(tmp_path: Path) -> None:
    path = tmp_path / "recipe-catalog-pack.json"
    _write_pack(path, _valid_pack())

    records = load_recipe_catalog_content(path)

    assert len(records) == 1
    assert records[0].recipeId == "kasza-warzywa-r1c"
    assert records[0].locale == "pl-PL"


def test_empty_content_path_returns_empty_tuple_without_fixture_fallback() -> None:
    assert load_recipe_catalog_content("") == ()
    assert load_recipe_catalog_content(None) == ()


def test_missing_configured_content_path_is_explicit_validation_error(
    tmp_path: Path,
) -> None:
    with pytest.raises(RecipeCatalogContentValidationError) as exc_info:
        load_recipe_catalog_content(tmp_path / "missing-pack.json")

    assert exc_info.value.report.summary.issueCodes == ["content_path_unavailable"]


def test_content_path_must_not_point_at_tests_or_fixtures(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    path = fixture_dir / "recipe-catalog-pack.json"
    _write_pack(path, _valid_pack())

    with pytest.raises(RecipeCatalogContentValidationError) as exc_info:
        load_recipe_catalog_content(path)

    assert exc_info.value.report.summary.issueCodes == ["disallowed_content_path"]


def test_configured_content_path_must_be_absolute() -> None:
    with pytest.raises(RecipeCatalogContentValidationError) as exc_info:
        load_recipe_catalog_content("recipe-catalog-pack.json")

    assert exc_info.value.report.summary.issueCodes == ["relative_content_path"]


def test_invalid_json_is_reported_without_raw_file_contents(tmp_path: Path) -> None:
    path = tmp_path / "recipe-catalog-pack.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(RecipeCatalogContentValidationError) as exc_info:
        load_recipe_catalog_content(path)

    report = exc_info.value.report
    assert report.summary.issueCodes == ["invalid_json"]
    assert "{not-json" not in report.model_dump_json()


def test_rejects_pack_without_approved_content_metadata() -> None:
    pack = _valid_pack()
    del pack["approval"]

    _assert_pack_rejected(pack, "missing_approved_content_metadata")


def test_rejects_pack_with_approval_flag_false() -> None:
    pack = _valid_pack()
    pack["approval"] = {**pack["approval"], "approved": False}

    _assert_pack_rejected(pack, "unapproved_content_pack")


def test_rejects_empty_pack_when_path_is_configured() -> None:
    _assert_pack_rejected(_valid_pack({"records": []}), "empty_content_pack")


@pytest.mark.parametrize(
    "case",
    [
        "title_todo",
        "description_tbd",
        "source_sample",
        "step_lorem",
        "ingredient_dummy",
        "foundation_source",
    ],
)
def test_rejects_placeholder_or_foundation_content(case: str) -> None:
    pack = _valid_pack()
    record = copy.deepcopy(pack["records"][0])
    if case == "title_todo":
        record["title"] = "TODO obiad"
    elif case == "description_tbd":
        record["description"] = "TBD opis"
    elif case == "source_sample":
        record["sourceAttribution"]["sourceName"] = "sample source"
    elif case == "step_lorem":
        record["steps"] = ["lorem ipsum"]
    elif case == "ingredient_dummy":
        record["ingredients"][0]["snapshotName"] = "dummy ingredient"
    elif case == "foundation_source":
        record["sourceAttribution"]["sourceName"] = "Fitaly curated foundation"
    pack["records"] = [record]

    _assert_pack_rejected(pack, "placeholder_content")


def test_rejects_single_artificial_ingredient_snapshot_recipe() -> None:
    pack = _valid_pack()
    record = copy.deepcopy(pack["records"][0])
    record["ingredients"][0]["snapshotName"] = "Curated ingredient snapshot"
    pack["records"] = [record]

    _assert_pack_rejected(pack, "artificial_ingredient_snapshot")


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("missing_source_attribution", "missing_source_attribution"),
        ("missing_record_reviewed_at", "missing_review_metadata"),
        ("missing_pack_reviewed_at", "missing_review_metadata"),
    ],
)
def test_rejects_missing_source_or_review_metadata(
    case: str,
    expected_code: RecipeCatalogContentValidationIssueCode,
) -> None:
    pack = _valid_pack()
    record = copy.deepcopy(pack["records"][0])
    if case == "missing_source_attribution":
        del record["sourceAttribution"]
        pack["records"] = [record]
    elif case == "missing_record_reviewed_at":
        del record["sourceAttribution"]["reviewedAt"]
        pack["records"] = [record]
    elif case == "missing_pack_reviewed_at":
        pack["review"] = {**pack["review"]}
        del pack["review"]["reviewedAt"]

    _assert_pack_rejected(pack, expected_code)


def test_rejects_duplicate_recipe_id_version() -> None:
    duplicate = _valid_record({"title": "Kasza z warzywami druga"})

    _assert_pack_rejected(
        _valid_pack({"records": [_valid_record(), duplicate]}),
        "duplicate_recipe_version",
    )


def test_rejects_non_active_runtime_record() -> None:
    record = _valid_record({"lifecycleState": "retired"})

    _assert_pack_rejected(_valid_pack({"records": [record]}), "inactive_content_record")


def test_rejects_record_not_ready_for_runtime_review() -> None:
    record = _valid_record({"reviewState": "needs_review"})

    _assert_pack_rejected(_valid_pack({"records": [record]}), "unready_review_state")


def test_rejects_pack_record_locale_mismatch() -> None:
    record = _valid_record({"locale": "en-US"})

    _assert_pack_rejected(_valid_pack({"records": [record]}), "locale_mismatch")


def test_pl_pack_rejects_known_english_foundation_titles_and_steps() -> None:
    record = _valid_record(
        {
            "title": "Vegan lentil bowl",
            "steps": ["Prepare curated recipe."],
        }
    )

    _assert_pack_rejected(
        _valid_pack({"records": [record]}),
        "fixture_language_mismatch",
    )


@pytest.mark.parametrize(
    "case",
    ["unknown_confidence", "low_confidence", "partial", "all_zeros"],
)
def test_rejects_unsafe_nutrition(case: str) -> None:
    pack = _valid_pack()
    record = copy.deepcopy(pack["records"][0])
    nutrition = record["nutritionSnapshot"]
    if case == "unknown_confidence":
        nutrition["confidence"] = "unknown"
    elif case == "low_confidence":
        nutrition["confidence"] = "low"
    elif case == "partial":
        nutrition["isPartial"] = True
    elif case == "all_zeros":
        nutrition.update(
            {
                "kcal": 0,
                "proteinGrams": 0,
                "fatGrams": 0,
                "carbsGrams": 0,
            }
        )
    pack["records"] = [record]

    _assert_pack_rejected(pack, "unsafe_nutrition")


@pytest.mark.parametrize(
    "case",
    ["complete_with_unknown_flags", "partial_without_unknown_flags"],
)
def test_rejects_inconsistent_profile_flags(case: str) -> None:
    record = _valid_record()
    if case == "complete_with_unknown_flags":
        record["profileFlagState"] = "complete"
        record["unknownAllergenFlags"] = ["peanuts"]
    elif case == "partial_without_unknown_flags":
        record["profileFlagState"] = "partial"

    _assert_pack_rejected(
        _valid_pack({"records": [record]}),
        "inconsistent_profile_flags",
    )
