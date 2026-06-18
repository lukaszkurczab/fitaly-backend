from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.api.http_errors import raise_bad_request, raise_service_unavailable
from app.core.exceptions import FirestoreServiceError
from app.schemas.food_library import (
    IngredientProductCreateRequest,
    IngredientProductCreateResponse,
    IngredientProductDeleteRequest,
    IngredientProductDeleteResponse,
    IngredientProductPullResponse,
    IngredientProductSearchResponse,
    IngredientProductUpdateRequest,
    IngredientProductUpdateResponse,
)
from app.services import food_library_service
from app.services.food_library_service import (
    IngredientProductMutationConflictError,
    IngredientProductNotFoundError,
)

router = APIRouter()


@router.get(
    "/users/me/ingredient-products/pull",
    response_model=IngredientProductPullResponse,
)
async def pull_ingredient_products_me(
    updatedAfter: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> IngredientProductPullResponse:
    try:
        return await food_library_service.pull_user_ingredient_products(
            current_user.uid,
            updated_after=updatedAfter,
            limit_count=limit,
        )
    except ValueError as exc:
        raise_bad_request(exc)
    except FirestoreServiceError as exc:
        raise_service_unavailable(
            exc,
            detail="Ingredient/Product pull is temporarily unavailable",
        )


@router.get(
    "/users/me/ingredient-products/search",
    response_model=IngredientProductSearchResponse,
)
async def search_ingredient_products_me(
    query: str = Query(min_length=1),
    locale: str | None = Query(default=None),
    limit: int = Query(default=8, ge=1),
    includeUserScoped: bool = Query(default=True),
    includeGlobal: bool = Query(default=True),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> IngredientProductSearchResponse:
    try:
        return await food_library_service.search_ingredient_products(
            current_user.uid,
            query=query,
            locale=locale,
            limit_count=limit,
            include_user_scoped=includeUserScoped,
            include_global=includeGlobal,
        )
    except ValueError as exc:
        raise_bad_request(exc)
    except FirestoreServiceError:
        return food_library_service.build_degraded_search_response(
            query=query,
            locale=locale,
            limit_count=limit,
            include_user_scoped=includeUserScoped,
            include_global=includeGlobal,
        )


@router.post(
    "/users/me/ingredient-products",
    response_model=IngredientProductCreateResponse,
)
async def create_ingredient_product_me(
    request: IngredientProductCreateRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> IngredientProductCreateResponse:
    try:
        item, updated = await food_library_service.create_user_ingredient_product(
            current_user.uid,
            request,
        )
    except IngredientProductMutationConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise_bad_request(exc)

    return IngredientProductCreateResponse(item=item, updated=updated)


@router.post(
    "/users/me/ingredient-products/{ingredientProductId}/update",
    response_model=IngredientProductUpdateResponse,
)
async def update_ingredient_product_me(
    ingredientProductId: str,
    request: IngredientProductUpdateRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> IngredientProductUpdateResponse:
    try:
        item, updated = await food_library_service.update_user_ingredient_product(
            current_user.uid,
            ingredient_product_id=ingredientProductId,
            request=request,
        )
    except IngredientProductMutationConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IngredientProductNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise_bad_request(exc)
    except FirestoreServiceError as exc:
        raise_service_unavailable(
            exc,
            detail="Ingredient/Product update is temporarily unavailable",
        )

    return IngredientProductUpdateResponse(item=item, updated=updated)


@router.post(
    "/users/me/ingredient-products/{ingredientProductId}/delete",
    response_model=IngredientProductDeleteResponse,
)
async def delete_ingredient_product_me(
    ingredientProductId: str,
    request: IngredientProductDeleteRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> IngredientProductDeleteResponse:
    try:
        product_id, updated_at, updated = await food_library_service.delete_user_ingredient_product(
            current_user.uid,
            ingredient_product_id=ingredientProductId,
            client_mutation_id=request.clientMutationId,
        )
    except IngredientProductNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise_bad_request(exc)
    except FirestoreServiceError as exc:
        raise_service_unavailable(
            exc,
            detail="Ingredient/Product delete is temporarily unavailable",
        )

    return IngredientProductDeleteResponse(
        ingredientProductId=product_id,
        updatedAt=updated_at,
        updated=updated,
    )
