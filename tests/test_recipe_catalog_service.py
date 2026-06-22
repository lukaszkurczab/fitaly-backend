from __future__ import annotations

from collections.abc import Sequence

from pydantic import ValidationError

from app.schemas.recipes import (
    RecipeCatalogAllergenFlag,
    RecipeCatalogDietaryFlag,
    RecipeCatalogFilterResult,
    RecipeCatalogFilterRequest,
    RecipeCatalogNutritionSnapshot,
    RecipeCatalogProfileFlagState,
    RecipeCatalogRecord,
)
from app.services import recipe_catalog_service
from app.services.recipe_catalog_service import (
    RecipeCatalogCoverageCase,
    evaluate_default_recipe_catalog_coverage,
    evaluate_recipe_catalog,
)
from tests.recipe_catalog_fixtures import recipe_catalog_coverage_cases


def _recipe(
    recipe_id: str,
    *,
    title: str | None = None,
    dietary_flags: list[RecipeCatalogDietaryFlag] | None = None,
    allergen_flags: list[RecipeCatalogAllergenFlag] | None = None,
    profile_flag_state: RecipeCatalogProfileFlagState = "complete",
    unknown_dietary_flags: list[RecipeCatalogDietaryFlag] | None = None,
    unknown_allergen_flags: list[RecipeCatalogAllergenFlag] | None = None,
    kcal: int = 500,
    protein: int = 20,
    fat: int = 15,
    carbs: int = 50,
    style_tags: list[str] | None = None,
) -> RecipeCatalogRecord:
    return RecipeCatalogRecord.model_validate(
        {
            "recipeId": recipe_id,
            "version": 1,
            "lifecycleState": "active",
            "locale": "pl-PL",
            "title": title or recipe_id.replace("-", " ").title(),
            "description": "Test fixture recipe.",
            "servings": 2,
            "yield": "2 servings",
            "sourceAttribution": {
                "sourceType": "internal_curated",
                "sourceId": "test",
                "sourceName": "test",
                "reviewedAt": "2026-06-18T00:00:00.000Z",
            },
            "updatedAt": "2026-06-18T00:00:00.000Z",
            "reviewState": "curated",
            "ingredients": [
                {
                    "ingredientProductId": None,
                    "snapshotName": "Ingredient",
                    "quantity": 100,
                    "unit": "g",
                }
            ],
            "steps": ["Cook."],
            "prepTimeMin": 5,
            "cookTimeMin": 10,
            "nutritionSnapshot": {
                "kcal": kcal,
                "proteinGrams": protein,
                "fatGrams": fat,
                "carbsGrams": carbs,
                "confidence": "medium",
                "isPartial": False,
            },
            "imageRef": None,
            "profileFlagState": profile_flag_state,
            "dietaryFlags": dietary_flags or [],
            "allergenFlags": allergen_flags or [],
            "unknownDietaryFlags": unknown_dietary_flags or [],
            "unknownAllergenFlags": unknown_allergen_flags or [],
            "styleTags": style_tags or [],
        }
    )


def _request(**values: object) -> RecipeCatalogFilterRequest:
    return RecipeCatalogFilterRequest.model_validate(values)


def _ids(response_items: Sequence[RecipeCatalogFilterResult]) -> list[str]:
    return [item.recipe.recipeId for item in response_items]


def test_no_filters_returns_visible_catalog_in_deterministic_order() -> None:
    catalog = [_recipe("bravo"), _recipe("alpha")]

    response = evaluate_recipe_catalog(_request(), catalog=catalog)

    assert _ids(response.items) == ["alpha", "bravo"]
    assert {item.status for item in response.items} == {"visible"}
    assert {item.softPreferenceStatus for item in response.items} == {"not_applicable"}
    assert response.lowResults is False
    assert response.emptyCatalog is False


def test_one_allergy_hides_only_explicit_matching_allergen_flag() -> None:
    catalog = [
        _recipe("peanut-bars", allergen_flags=["peanuts"]),
        _recipe("clean-bowl"),
    ]

    response = evaluate_recipe_catalog(
        _request(allergies=["peanuts"]),
        catalog=catalog,
    )

    assert _ids(response.items) == ["clean-bowl"]
    assert response.hiddenHardExclusionCount == 1
    assert response.items[0].hardExclusionReasons == []


def test_one_restriction_like_preference_hides_explicit_mismatches() -> None:
    catalog = [
        _recipe("vegan-bowl", dietary_flags=["vegan"]),
        _recipe("yogurt-bowl", dietary_flags=["vegetarian"]),
    ]

    response = evaluate_recipe_catalog(
        _request(preferences=["vegan"]),
        catalog=catalog,
    )

    assert _ids(response.items) == ["vegan-bowl"]
    assert response.hiddenHardExclusionCount == 1


def test_macro_style_preference_is_soft_ranking_not_eligibility() -> None:
    catalog = [
        _recipe("lower-protein", protein=12),
        _recipe("higher-protein", protein=35),
    ]

    response = evaluate_recipe_catalog(
        _request(preferences=["highProtein"]),
        catalog=catalog,
    )

    assert _ids(response.items) == ["higher-protein", "lower-protein"]
    assert response.visibleCount == 2
    assert response.hiddenHardExclusionCount == 0
    assert response.items[0].softPreferenceMatches == ["highProtein"]
    assert response.items[1].softPreferenceMisses == ["highProtein"]


def test_allergy_plus_restriction_combines_only_hard_filter_classes() -> None:
    catalog = [
        _recipe("safe-vegan", dietary_flags=["vegan"]),
        _recipe("peanut-vegan", dietary_flags=["vegan"], allergen_flags=["peanuts"]),
        _recipe("peanut-non-vegan", allergen_flags=["peanuts"]),
    ]

    response = evaluate_recipe_catalog(
        _request(allergies=["peanuts"], preferences=["vegan"]),
        catalog=catalog,
    )

    assert _ids(response.items) == ["safe-vegan"]
    assert response.hiddenHardExclusionCount == 2
    assert response.queryEcho.lowResultsThreshold == 3


def test_low_results_state_uses_deterministic_filter_threshold() -> None:
    catalog = [
        _recipe("safe-one"),
        _recipe("hidden-peanut", allergen_flags=["peanuts"]),
    ]

    response = evaluate_recipe_catalog(
        _request(allergies=["peanuts"]),
        catalog=catalog,
    )

    assert response.visibleCount == 1
    assert response.queryEcho.lowResultsThreshold == 6
    assert response.lowResults is True


def test_empty_catalog_is_explicit_not_low_results() -> None:
    response = evaluate_recipe_catalog(_request(allergies=["peanuts"]), catalog=[])

    assert response.items == []
    assert response.totalCatalogCount == 0
    assert response.emptyCatalog is True
    assert response.lowResults is False


def test_default_catalog_is_true_empty_state_without_injected_content() -> None:
    response = evaluate_recipe_catalog(_request(allergies=["peanuts"]))

    assert response.items == []
    assert response.totalCatalogCount == 0
    assert response.visibleCount == 0
    assert response.hiddenHardExclusionCount == 0
    assert response.unknownRevealRequiredCount == 0
    assert response.emptyCatalog is True
    assert response.lowResults is False
    assert response.queryEcho.lowResultsThreshold == 0


def test_unknown_flags_require_reveal_and_are_not_treated_as_safe() -> None:
    catalog = [
        _recipe(
            "unknown-peanut-status",
            profile_flag_state="partial",
            unknown_allergen_flags=["peanuts"],
        ),
        _recipe("safe-bowl"),
    ]

    hidden_response = evaluate_recipe_catalog(
        _request(allergies=["peanuts"]),
        catalog=catalog,
    )
    reveal_response = evaluate_recipe_catalog(
        _request(allergies=["peanuts"], revealUnknown=True),
        catalog=catalog,
    )

    assert _ids(hidden_response.items) == ["safe-bowl"]
    assert hidden_response.unknownRevealRequiredCount == 1
    assert _ids(reveal_response.items) == ["safe-bowl", "unknown-peanut-status"]
    unknown = reveal_response.items[1]
    assert unknown.status == "unknown_reveal_required"
    assert unknown.unknownReasons[0].code == "unknown_allergen_flag"


def test_show_hidden_and_reveal_unknown_are_explicit_controls() -> None:
    catalog = [
        _recipe("hidden-peanut", allergen_flags=["peanuts"]),
        _recipe("unknown-peanut", profile_flag_state="unknown"),
        _recipe("safe-bowl"),
    ]

    hidden_only = evaluate_recipe_catalog(
        _request(allergies=["peanuts"], showHidden=True),
        catalog=catalog,
    )
    revealed = evaluate_recipe_catalog(
        _request(allergies=["peanuts"], showHidden=True, revealUnknown=True),
        catalog=catalog,
    )

    assert _ids(hidden_only.items) == ["safe-bowl", "hidden-peanut"]
    assert _ids(revealed.items) == ["safe-bowl", "unknown-peanut", "hidden-peanut"]


def test_chronic_diseases_allergies_other_and_lifestyle_are_ignored() -> None:
    catalog = [_recipe("safe-bowl")]

    baseline = evaluate_recipe_catalog(_request(), catalog=catalog)
    with_ignored_profile_context = evaluate_recipe_catalog(
        _request(
            chronicDiseases=["diabetes"],
            allergiesOther="nightshade",
            lifestyle="night shifts",
        ),
        catalog=catalog,
    )

    assert _ids(with_ignored_profile_context.items) == _ids(baseline.items)
    assert with_ignored_profile_context.queryEcho.ignoredChronicDiseases == ["diabetes"]
    assert with_ignored_profile_context.queryEcho.ignoredAllergiesOtherPresent is True
    assert with_ignored_profile_context.queryEcho.ignoredLifestylePresent is True


def test_schema_validation_is_strict_for_unknown_fields() -> None:
    try:
        RecipeCatalogFilterRequest.model_validate({"unexpected": True})
    except ValidationError as exc:
        assert "Extra inputs are not permitted" in str(exc)
    else:
        raise AssertionError("RecipeCatalogFilterRequest accepted an unknown field")


def test_recipe_service_does_not_import_or_call_meal_write_surfaces() -> None:
    service_source = recipe_catalog_service.__loader__.get_source(
        recipe_catalog_service.__name__
    )

    assert service_source is not None
    assert "meal_service" not in service_source
    assert "meal_storage" not in service_source
    assert "MealUpsert" not in service_source


def test_nutrition_snapshot_thresholds_are_deterministic() -> None:
    record = _recipe("threshold", protein=25, carbs=35, fat=12)

    assert isinstance(record.nutritionSnapshot, RecipeCatalogNutritionSnapshot)
    response = evaluate_recipe_catalog(
        _request(preferences=["highProtein", "lowCarb", "lowFat"]),
        catalog=[record],
    )

    assert response.items[0].softPreferenceMatches == [
        "highProtein",
        "lowCarb",
        "lowFat",
    ]


def test_default_coverage_gate_reports_empty_production_catalog_without_fixture() -> None:
    report = evaluate_default_recipe_catalog_coverage()

    assert len(report) == 1
    assert report[0].case_id == "empty-catalog"
    assert report[0].total_catalog_count == 0
    assert report[0].returned_item_count == 0
    assert report[0].empty_catalog is True
    assert report[0].low_results is False
    assert report[0].passes_coverage_gate is True


def test_fixture_catalog_coverage_gate_reports_accepted_cases() -> None:
    report = evaluate_default_recipe_catalog_coverage(
        cases=recipe_catalog_coverage_cases()
    )
    rows = {row.case_id: row for row in report}

    assert set(rows) == {
        "no-filters",
        "one-allergy",
        "one-restriction",
        "one-macro-style",
        "allergy-plus-restriction",
        "low-results",
        "empty-catalog",
        "unknown-reveal",
    }
    assert all(row.passes_coverage_gate for row in rows.values())

    no_filters = rows["no-filters"]
    assert no_filters.name == "No profile filters"
    assert no_filters.visible_count > 0
    assert no_filters.hidden_hard_exclusion_count == 0
    assert no_filters.unknown_reveal_required_count == 0
    assert no_filters.threshold == 0
    assert no_filters.low_results is False
    assert no_filters.empty_catalog is False
    assert no_filters.low_results_state_required is False
    assert no_filters.query_echo.activeAllergies == []
    assert no_filters.query_echo.activeRestrictions == []
    assert no_filters.query_echo.activeSoftPreferences == []

    one_allergy = rows["one-allergy"]
    assert one_allergy.request.allergies == ["lactose"]
    assert one_allergy.query_echo.activeAllergies == ["lactose"]
    assert one_allergy.hidden_hard_exclusion_count == 1
    assert one_allergy.threshold == 6

    one_restriction = rows["one-restriction"]
    assert one_restriction.query_echo.activeRestrictions == ["vegan"]
    assert one_restriction.hidden_hard_exclusion_count > 0
    assert one_restriction.threshold == 6

    macro_style = rows["one-macro-style"]
    assert macro_style.query_echo.activeSoftPreferences == ["highProtein"]
    assert macro_style.visible_count == macro_style.total_catalog_count
    assert macro_style.hidden_hard_exclusion_count == 0
    assert macro_style.threshold == 6

    combined = rows["allergy-plus-restriction"]
    assert combined.query_echo.activeAllergies == ["peanuts"]
    assert combined.query_echo.activeRestrictions == ["vegan"]
    assert combined.threshold == 3
    assert combined.hidden_hard_exclusion_count > 0


def test_fixture_catalog_coverage_gate_reports_low_empty_and_unknown_states() -> None:
    rows = {
        row.case_id: row
        for row in evaluate_default_recipe_catalog_coverage(
            cases=recipe_catalog_coverage_cases()
        )
    }

    low_results = rows["low-results"]
    assert low_results.query_echo.activeRestrictions == ["pescatarian"]
    assert low_results.visible_count < low_results.threshold
    assert low_results.low_results is True
    assert low_results.empty_catalog is False
    assert low_results.low_results_state_required is True
    assert low_results.passes_coverage_gate is True

    empty_catalog = rows["empty-catalog"]
    assert empty_catalog.total_catalog_count == 0
    assert empty_catalog.returned_item_count == 0
    assert empty_catalog.visible_count == 0
    assert empty_catalog.hidden_hard_exclusion_count == 0
    assert empty_catalog.unknown_reveal_required_count == 0
    assert empty_catalog.threshold == 0
    assert empty_catalog.low_results is False
    assert empty_catalog.empty_catalog is True
    assert empty_catalog.low_results_state_required is False
    assert empty_catalog.passes_coverage_gate is True

    unknown_reveal = rows["unknown-reveal"]
    assert unknown_reveal.request.revealUnknown is True
    assert unknown_reveal.query_echo.revealUnknown is True
    assert unknown_reveal.query_echo.activeAllergies == ["peanuts"]
    assert unknown_reveal.total_catalog_count == rows["no-filters"].total_catalog_count
    assert unknown_reveal.unknown_reveal_required_count == 1
    assert unknown_reveal.revealed_unknown_count == 1
    assert (
        unknown_reveal.returned_item_count
        == unknown_reveal.visible_count + unknown_reveal.revealed_unknown_count
    )
    assert unknown_reveal.passes_coverage_gate is True


def test_coverage_gate_keeps_empty_catalog_separate_from_low_results() -> None:
    report = evaluate_default_recipe_catalog_coverage(
        cases=[
            RecipeCatalogCoverageCase(
                case_id="empty-without-empty-expectation",
                name="Empty catalog without explicit empty state expectation",
                request=_request(allergies=["peanuts"]),
                catalog=[],
            )
        ]
    )

    assert report[0].empty_catalog is True
    assert report[0].low_results is False
    assert report[0].passes_coverage_gate is False


def test_coverage_gate_rejects_non_empty_filtered_case_with_zero_visible_results() -> None:
    report = evaluate_default_recipe_catalog_coverage(
        cases=[
            RecipeCatalogCoverageCase(
                case_id="zero-visible-low-results",
                name="Filtered non-empty catalog with no usable visible rows",
                request=_request(allergies=["peanuts"]),
                catalog=[_recipe("hidden-peanut", allergen_flags=["peanuts"])],
            )
        ]
    )

    assert report[0].total_catalog_count == 1
    assert report[0].visible_count == 0
    assert report[0].hidden_hard_exclusion_count == 1
    assert report[0].low_results is True
    assert report[0].empty_catalog is False
    assert report[0].passes_coverage_gate is False


def test_coverage_gate_requires_unknown_reveal_to_be_visible_when_expected() -> None:
    report = evaluate_default_recipe_catalog_coverage(
        cases=[
            RecipeCatalogCoverageCase(
                case_id="unknown-not-revealed",
                name="Unknown case without reveal control",
                request=_request(allergies=["peanuts"]),
                catalog=[
                    _recipe(
                        "unknown-peanut-status",
                        profile_flag_state="partial",
                        unknown_allergen_flags=["peanuts"],
                    ),
                    _recipe("safe-bowl"),
                ],
                expected_unknown_reveal=True,
            )
        ]
    )

    assert report[0].unknown_reveal_required_count == 1
    assert report[0].revealed_unknown_count == 0
    assert report[0].passes_coverage_gate is False
