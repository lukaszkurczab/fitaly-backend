from fastapi import APIRouter, Depends

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.schemas.badge import (
    BadgeItemResponse,
    BadgeListResponse,
    PremiumBadgeReconcileRequest,
    PremiumBadgeReconcileResponse,
)
from app.services import badge_service

router = APIRouter()


@router.post(
    "/users/me/badges/premium/reconcile",
    response_model=PremiumBadgeReconcileResponse,
)
async def reconcile_premium_badges_me(
    request: PremiumBadgeReconcileRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> PremiumBadgeReconcileResponse:
    awarded_badge_ids, has_premium_badge = await badge_service.reconcile_premium_badges(
        current_user.uid,
        is_premium=request.isPremium,
        now_ms=request.nowMs,
    )

    return PremiumBadgeReconcileResponse(
        awardedBadgeIds=awarded_badge_ids,
        hasPremiumBadge=has_premium_badge,
        updated=True,
    )


@router.get("/users/me/badges", response_model=BadgeListResponse)
async def list_badges_me(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> BadgeListResponse:
    items = await badge_service.list_badges(current_user.uid)
    return BadgeListResponse(items=[BadgeItemResponse.model_validate(item) for item in items])
