from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


FoodLibraryDomain = Literal[
    "MealTemplate",
    "Recipe",
    "Ingredient/Product",
    "ShoppingList",
]
FoodLibraryDomainOwner = Literal[
    "meal_template_library",
    "recipe_library",
    "ingredient_product_library",
    "shopping_list_library",
]
FoodLibraryDomainField = Literal[
    "templateId",
    "ownerUserId",
    "templateVersion",
    "displayName",
    "description",
    "mealTypeHint",
    "draftItems",
    "draftTotals",
    "nutritionSnapshot",
    "imageRef",
    "recipeId",
    "recipeVersion",
    "title",
    "ingredients",
    "steps",
    "instructions",
    "servings",
    "yield",
    "prepTimeMin",
    "cookTimeMin",
    "ingredientProductId",
    "kind",
    "brandName",
    "ingredientName",
    "barcodeIdentities",
    "servingSizes",
    "nutritionPerServing",
    "sourceAttribution",
    "listId",
    "items",
    "itemRefs",
    "checkedState",
    "sortOrder",
    "notes",
]
FoodLibraryForbiddenLoggedMealField = Literal[
    "mealTemplateId",
    "templateId",
    "templateVersion",
    "recipeId",
    "recipeInstructions",
    "recipeSteps",
    "recipeYield",
    "recipeServings",
    "productId",
    "productBarcode",
    "productCatalogId",
    "productLifecycleState",
    "shoppingListId",
    "shoppingListItems",
    "shoppingListCheckedAt",
    "shoppingListLifecycleState",
]
CurrentSavedMealLegacyMarker = Literal[
    "myMeals",
    "saved_meals",
    "source:saved",
    "inputMethod:saved",
    "savedMealRefId",
]
BarcodeResultOwner = Literal["backend_provider_adapter", "add_meal_draft_source"]

FOOD_LIBRARY_DOMAINS: tuple[FoodLibraryDomain, ...] = (
    "MealTemplate",
    "Recipe",
    "Ingredient/Product",
    "ShoppingList",
)
FOOD_LIBRARY_LOGGED_MEAL_OWNER = "meal_logging"
FOOD_LIBRARY_LOGGED_MEAL_SCHEMA = "Meal"
FOOD_LIBRARY_FORBIDDEN_LOGGED_MEAL_FIELDS: tuple[
    FoodLibraryForbiddenLoggedMealField,
    ...,
] = (
    "mealTemplateId",
    "templateId",
    "templateVersion",
    "recipeId",
    "recipeInstructions",
    "recipeSteps",
    "recipeYield",
    "recipeServings",
    "productId",
    "productBarcode",
    "productCatalogId",
    "productLifecycleState",
    "shoppingListId",
    "shoppingListItems",
    "shoppingListCheckedAt",
    "shoppingListLifecycleState",
)
FOOD_LIBRARY_CURRENT_SAVED_MEAL_NAMES: tuple[Literal["myMeals", "saved_meals"], ...] = (
    "myMeals",
    "saved_meals",
)
FOOD_LIBRARY_LEGACY_MARKERS_NOT_CANONICAL: tuple[
    CurrentSavedMealLegacyMarker,
    ...,
] = (
    "myMeals",
    "saved_meals",
    "source:saved",
    "inputMethod:saved",
    "savedMealRefId",
)
FOOD_LIBRARY_BARCODE_RESULT_OWNERS: tuple[BarcodeResultOwner, ...] = (
    "backend_provider_adapter",
    "add_meal_draft_source",
)
FOOD_LIBRARY_MEAL_TEMPLATE_FORBIDDEN_LOGGED_MEAL_FIELDS = {
    "loggedAt",
    "dayKey",
    "loggedAtLocalMin",
    "tzOffsetMin",
    "syncState",
    "source",
    "inputMethod",
    "savedMealRefId",
}
FOOD_LIBRARY_DOMAIN_CONTRACTS: dict[
    FoodLibraryDomain,
    tuple[
        FoodLibraryDomainOwner,
        tuple[FoodLibraryDomainField, ...],
        tuple[FoodLibraryDomainField, ...],
    ],
] = {
    "MealTemplate": (
        "meal_template_library",
        ("templateId", "ownerUserId", "templateVersion"),
        (
            "displayName",
            "description",
            "mealTypeHint",
            "draftItems",
            "draftTotals",
            "nutritionSnapshot",
            "imageRef",
        ),
    ),
    "Recipe": (
        "recipe_library",
        ("recipeId", "ownerUserId", "recipeVersion"),
        (
            "title",
            "description",
            "ingredients",
            "steps",
            "instructions",
            "servings",
            "yield",
            "prepTimeMin",
            "cookTimeMin",
            "nutritionSnapshot",
            "imageRef",
        ),
    ),
    "Ingredient/Product": (
        "ingredient_product_library",
        ("ingredientProductId", "ownerUserId"),
        (
            "displayName",
            "kind",
            "brandName",
            "ingredientName",
            "barcodeIdentities",
            "servingSizes",
            "nutritionPerServing",
            "sourceAttribution",
        ),
    ),
    "ShoppingList": (
        "shopping_list_library",
        ("listId", "ownerUserId"),
        ("title", "items", "itemRefs", "checkedState", "sortOrder", "notes"),
    ),
}


class LoggedMealBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: Literal["meal_logging"]
    schemaName: Literal["Meal"]
    mustRemainNarrow: Literal[True]
    mustNotServeAsLibraryCatchAll: Literal[True]
    mustNotGainFields: list[FoodLibraryForbiddenLoggedMealField] = Field(min_length=1)
    rationale: str = Field(min_length=1)


class CurrentSavedMealsBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currentNames: list[Literal["myMeals", "saved_meals"]]
    isFinalLibraryFoundation: Literal[False]
    laterTargetDomain: Literal["MealTemplate"]
    compatibilityFallbackToOldShapeAccepted: Literal[False]
    legacyMarkersNotCanonicalLibraryFoundation: list[CurrentSavedMealLegacyMarker]
    mustNotExpandWith: list[FoodLibraryForbiddenLoggedMealField] = Field(min_length=1)
    rationale: str = Field(min_length=1)


class BarcodeBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resultOwnership: list[BarcodeResultOwner]
    addMealDraftSourceOnly: Literal[True]
    createsFirstPartyProductCatalogInThisSlice: Literal[False]
    mustNotWriteLibraryDomains: list[Literal["Ingredient/Product"]]
    rationale: str = Field(min_length=1)


class FoodLibraryDomainContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: FoodLibraryDomainOwner
    identityFields: list[FoodLibraryDomainField] = Field(min_length=1)
    ownedFields: list[FoodLibraryDomainField] = Field(min_length=1)


class FoodLibraryDomainsContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: Literal["food_library_domains_v1"]
    libraryDomains: list[FoodLibraryDomain]
    domainContracts: dict[FoodLibraryDomain, FoodLibraryDomainContract]
    loggedMealBoundary: LoggedMealBoundary
    currentSavedMealsBoundary: CurrentSavedMealsBoundary
    barcodeBoundary: BarcodeBoundary

    @model_validator(mode="after")
    def _validate_exact_contract(self) -> "FoodLibraryDomainsContract":
        if tuple(self.libraryDomains) != FOOD_LIBRARY_DOMAINS:
            raise ValueError("libraryDomains must match canonical CH-06 domains")
        if tuple(self.domainContracts.keys()) != FOOD_LIBRARY_DOMAINS:
            raise ValueError("domainContracts must declare each CH-06 domain exactly once")
        for domain, expected in FOOD_LIBRARY_DOMAIN_CONTRACTS.items():
            expected_owner, expected_identity_fields, expected_owned_fields = expected
            domain_contract = self.domainContracts[domain]
            if domain_contract.owner != expected_owner:
                raise ValueError(f"{domain} owner drifted from CH-06 contract")
            if tuple(domain_contract.identityFields) != expected_identity_fields:
                raise ValueError(f"{domain} identityFields drifted from CH-06 contract")
            if tuple(domain_contract.ownedFields) != expected_owned_fields:
                raise ValueError(f"{domain} ownedFields drifted from CH-06 contract")
        meal_template_fields = {
            *self.domainContracts["MealTemplate"].identityFields,
            *self.domainContracts["MealTemplate"].ownedFields,
        }
        if forbidden := (
            meal_template_fields & FOOD_LIBRARY_MEAL_TEMPLATE_FORBIDDEN_LOGGED_MEAL_FIELDS
        ):
            raise ValueError(
                f"MealTemplate contract reuses logged-meal-only fields: {sorted(forbidden)}"
            )
        if self.loggedMealBoundary.owner != FOOD_LIBRARY_LOGGED_MEAL_OWNER:
            raise ValueError("logged Meal must remain owned by meal logging")
        if self.loggedMealBoundary.schemaName != FOOD_LIBRARY_LOGGED_MEAL_SCHEMA:
            raise ValueError("logged Meal schema name must remain Meal")
        if (
            tuple(self.loggedMealBoundary.mustNotGainFields)
            != FOOD_LIBRARY_FORBIDDEN_LOGGED_MEAL_FIELDS
        ):
            raise ValueError("logged Meal forbidden fields must match CH-06 boundary")
        if (
            tuple(self.currentSavedMealsBoundary.currentNames)
            != FOOD_LIBRARY_CURRENT_SAVED_MEAL_NAMES
        ):
            raise ValueError("current saved-meal names must be explicit")
        if (
            tuple(
                self.currentSavedMealsBoundary.legacyMarkersNotCanonicalLibraryFoundation
            )
            != FOOD_LIBRARY_LEGACY_MARKERS_NOT_CANONICAL
        ):
            raise ValueError("legacy saved-meal markers must stay explicit")
        if (
            tuple(self.currentSavedMealsBoundary.mustNotExpandWith)
            != FOOD_LIBRARY_FORBIDDEN_LOGGED_MEAL_FIELDS
        ):
            raise ValueError("current saved meals must not expand with library fields")
        if tuple(self.barcodeBoundary.resultOwnership) != FOOD_LIBRARY_BARCODE_RESULT_OWNERS:
            raise ValueError(
                "barcode ownership must stay backend-adapter/add-meal-draft only"
            )
        if self.barcodeBoundary.mustNotWriteLibraryDomains != ["Ingredient/Product"]:
            raise ValueError("barcode lookup must not create product catalog records")
        return self
