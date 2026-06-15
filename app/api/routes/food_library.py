from fastapi import APIRouter, Depends, Query

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.api.http_errors import raise_bad_request
from app.core.exceptions import FirestoreServiceError
from app.schemas.food_library import IngredientProductSearchResponse
from app.services import food_library_service

router = APIRouter()


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
