from fastapi import APIRouter, Depends, Query

from app.api.deps import (
    AuthenticatedUser,
    get_required_authenticated_user,
)
from app.schemas.ai_credits import AiCreditsResponse, AiCreditTransactionsResponse
from app.services import ai_credits_service

router = APIRouter()


@router.get("/ai/credits", response_model=AiCreditsResponse)
async def get_ai_credits(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AiCreditsResponse:
    credits_status = await ai_credits_service.get_credits_status(current_user.uid)
    return AiCreditsResponse(**credits_status.model_dump())


@router.get("/ai/credits/transactions", response_model=AiCreditTransactionsResponse)
async def get_ai_credit_transactions(
    limit: int = Query(default=50, ge=1, le=200),
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AiCreditTransactionsResponse:
    items = await ai_credits_service.list_credit_transactions(
        current_user.uid,
        limit_count=limit,
    )
    return AiCreditTransactionsResponse(items=items)
