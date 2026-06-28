from __future__ import annotations

from collections.abc import Sequence

from app.schemas.recipes import (
    RecipeCatalogAllergenFlag,
    RecipeCatalogDietaryFlag,
    RecipeCatalogFilterRequest,
    RecipeCatalogNutritionSnapshot,
    RecipeCatalogRecord,
    RecipeCatalogSourceAttribution,
)
from app.services.recipe_catalog_service import RecipeCatalogCoverageCase


def _source() -> RecipeCatalogSourceAttribution:
    return RecipeCatalogSourceAttribution(
        sourceType="internal_curated",
        sourceId="recipe-catalog-foundation",
        sourceName="Fitaly curated foundation",
        reviewedAt="2026-06-18T00:00:00.000Z",
    )


def _record(
    *,
    recipe_id: str,
    title: str,
    nutrition: RecipeCatalogNutritionSnapshot,
    dietary_flags: list[RecipeCatalogDietaryFlag],
    allergen_flags: list[RecipeCatalogAllergenFlag] | None = None,
    style_tags: list[str] | None = None,
) -> RecipeCatalogRecord:
    return RecipeCatalogRecord.model_validate(
        {
            "recipeId": recipe_id,
            "version": 1,
            "lifecycleState": "active",
            "locale": "pl-PL",
            "title": title,
            "description": "Curated recipe catalog foundation record.",
            "servings": 2,
            "yield": "2 servings",
            "sourceAttribution": _source().model_dump(mode="json"),
            "updatedAt": "2026-06-18T00:00:00.000Z",
            "reviewState": "curated",
            "ingredients": [
                {
                    "ingredientProductId": None,
                    "snapshotName": "Curated ingredient snapshot",
                    "quantity": 100,
                    "unit": "g",
                }
            ],
            "steps": ["Prepare curated recipe."],
            "prepTimeMin": 10,
            "cookTimeMin": 20,
            "nutritionSnapshot": nutrition.model_dump(mode="json"),
            "imageRef": None,
            "profileFlagState": "complete",
            "dietaryFlags": dietary_flags,
            "allergenFlags": allergen_flags or [],
            "unknownDietaryFlags": [],
            "unknownAllergenFlags": [],
            "styleTags": style_tags or [],
        }
    )


TEST_RECIPE_CATALOG: tuple[RecipeCatalogRecord, ...] = (
    _record(
        recipe_id="vegan-lentil-bowl",
        title="Vegan lentil bowl",
        nutrition=RecipeCatalogNutritionSnapshot(
            kcal=520,
            proteinGrams=28,
            fatGrams=14,
            carbsGrams=62,
            confidence="medium",
            isPartial=False,
        ),
        dietary_flags=["vegan", "vegetarian", "gluten_free", "dairy_free"],
        style_tags=["balanced", "mediterranean"],
    ),
    _record(
        recipe_id="salmon-rice-plate",
        title="Salmon rice plate",
        nutrition=RecipeCatalogNutritionSnapshot(
            kcal=610,
            proteinGrams=34,
            fatGrams=22,
            carbsGrams=58,
            confidence="medium",
            isPartial=False,
        ),
        dietary_flags=["pescatarian", "gluten_free", "dairy_free"],
        style_tags=["balanced", "mediterranean"],
    ),
    _record(
        recipe_id="tofu-vegetable-stir-fry",
        title="Tofu vegetable stir fry",
        nutrition=RecipeCatalogNutritionSnapshot(
            kcal=430,
            proteinGrams=26,
            fatGrams=17,
            carbsGrams=42,
            confidence="medium",
            isPartial=False,
        ),
        dietary_flags=["vegan", "vegetarian", "dairy_free"],
        style_tags=["balanced"],
    ),
    _record(
        recipe_id="berry-yogurt-bowl",
        title="Berry yogurt bowl",
        nutrition=RecipeCatalogNutritionSnapshot(
            kcal=360,
            proteinGrams=24,
            fatGrams=9,
            carbsGrams=45,
            confidence="medium",
            isPartial=False,
        ),
        dietary_flags=["vegetarian", "gluten_free"],
        allergen_flags=["lactose"],
        style_tags=["balanced"],
    ),
    _record(
        recipe_id="peanut-oat-bars",
        title="Peanut oat bars",
        nutrition=RecipeCatalogNutritionSnapshot(
            kcal=390,
            proteinGrams=14,
            fatGrams=18,
            carbsGrams=48,
            confidence="medium",
            isPartial=False,
        ),
        dietary_flags=["vegan", "vegetarian", "dairy_free"],
        allergen_flags=["peanuts", "gluten"],
    ),
    RecipeCatalogRecord.model_validate(
        {
            "recipeId": "chickpea-vegetable-pan",
            "version": 1,
            "lifecycleState": "active",
            "locale": "pl-PL",
            "title": "Chickpea vegetable pan",
            "description": "Curated recipe with partial profile flag review.",
            "servings": 2,
            "yield": "2 servings",
            "sourceAttribution": _source().model_dump(mode="json"),
            "updatedAt": "2026-06-18T00:00:00.000Z",
            "reviewState": "curated",
            "ingredients": [
                {
                    "ingredientProductId": None,
                    "snapshotName": "Curated ingredient snapshot",
                    "quantity": 100,
                    "unit": "g",
                }
            ],
            "steps": ["Prepare curated recipe."],
            "prepTimeMin": 10,
            "cookTimeMin": 20,
            "nutritionSnapshot": RecipeCatalogNutritionSnapshot(
                kcal=470,
                proteinGrams=22,
                fatGrams=15,
                carbsGrams=54,
                confidence="medium",
                isPartial=False,
            ).model_dump(mode="json"),
            "imageRef": None,
            "profileFlagState": "partial",
            "dietaryFlags": ["vegan", "vegetarian", "gluten_free", "dairy_free"],
            "allergenFlags": [],
            "unknownDietaryFlags": [],
            "unknownAllergenFlags": ["peanuts"],
            "styleTags": ["balanced", "mediterranean"],
        }
    ),
    _record(
        recipe_id="chicken-quinoa-salad",
        title="Chicken quinoa salad",
        nutrition=RecipeCatalogNutritionSnapshot(
            kcal=560,
            proteinGrams=42,
            fatGrams=16,
            carbsGrams=46,
            confidence="medium",
            isPartial=False,
        ),
        dietary_flags=["gluten_free", "dairy_free"],
        style_tags=["balanced", "mediterranean"],
    ),
)


def recipe_catalog_coverage_cases(
    catalog: Sequence[RecipeCatalogRecord] = TEST_RECIPE_CATALOG,
) -> tuple[RecipeCatalogCoverageCase, ...]:
    return (
        RecipeCatalogCoverageCase(
            case_id="no-filters",
            name="No profile filters",
            request=_coverage_request(),
            catalog=catalog,
        ),
        RecipeCatalogCoverageCase(
            case_id="one-allergy",
            name="One allergy hard exclusion",
            request=_coverage_request(allergies=["lactose"]),
            catalog=catalog,
        ),
        RecipeCatalogCoverageCase(
            case_id="one-restriction",
            name="One restriction-like preference",
            request=_coverage_request(preferences=["vegan"]),
            catalog=catalog,
        ),
        RecipeCatalogCoverageCase(
            case_id="one-macro-style",
            name="One macro/style preference",
            request=_coverage_request(preferences=["highProtein"]),
            catalog=catalog,
        ),
        RecipeCatalogCoverageCase(
            case_id="allergy-plus-restriction",
            name="Allergy plus restriction-like preference",
            request=_coverage_request(allergies=["peanuts"], preferences=["vegan"]),
            catalog=catalog,
        ),
        RecipeCatalogCoverageCase(
            case_id="low-results",
            name="Low-results state required",
            request=_coverage_request(preferences=["pescatarian"]),
            catalog=catalog,
        ),
        RecipeCatalogCoverageCase(
            case_id="empty-catalog",
            name="Empty catalog state",
            request=_coverage_request(allergies=["peanuts"]),
            catalog=(),
            expected_empty_catalog=True,
        ),
        RecipeCatalogCoverageCase(
            case_id="unknown-reveal",
            name="Unknown flags revealed explicitly",
            request=_coverage_request(allergies=["peanuts"], revealUnknown=True),
            catalog=catalog,
            expected_unknown_reveal=True,
        ),
    )


def _coverage_request(**values: object) -> RecipeCatalogFilterRequest:
    return RecipeCatalogFilterRequest.model_validate(values)
