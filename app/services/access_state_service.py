"""Canonical access-state contract derived from backend-owned billing state."""

from datetime import datetime

from app.core.datetime_utils import utc_now
from app.core.exceptions import FirestoreServiceError
from app.schemas.access_state import AccessFeatureState, AccessFeatures, AccessStateResponse
from app.schemas.ai_credits import AiCreditsStatus
from app.services import ai_credits_service


def _credit_feature(
    *,
    balance: int,
    required_credits: int,
) -> AccessFeatureState:
    enabled = balance >= required_credits
    return AccessFeatureState(
        enabled=enabled,
        status="enabled" if enabled else "disabled",
        reason=None if enabled else "insufficient_credits",
        requiredCredits=required_credits,
        remainingCredits=max(balance - required_credits, 0),
    )


def _premium_feature(*, is_premium: bool) -> AccessFeatureState:
    return AccessFeatureState(
        enabled=is_premium,
        status="enabled" if is_premium else "disabled",
        reason=None if is_premium else "requires_premium",
        requiredCredits=None,
        remainingCredits=None,
    )


def _degraded_feature(required_credits: int | None = None) -> AccessFeatureState:
    return AccessFeatureState(
        enabled=False,
        status="unknown",
        reason="degraded",
        requiredCredits=required_credits,
        remainingCredits=None,
    )


def _features_from_credits(status: AiCreditsStatus) -> AccessFeatures:
    is_premium = status.tier == "premium"
    return AccessFeatures(
        aiChat=_credit_feature(
            balance=status.balance,
            required_credits=status.costs.chat,
        ),
        photoAnalysis=_credit_feature(
            balance=status.balance,
            required_credits=status.costs.photo,
        ),
        textMealAnalysis=_credit_feature(
            balance=status.balance,
            required_credits=status.costs.textMeal,
        ),
        weeklyReport=_premium_feature(is_premium=is_premium),
        fullHistory=_premium_feature(is_premium=is_premium),
        cloudBackup=_premium_feature(is_premium=is_premium),
    )


def _degraded_access_state(refreshed_at: datetime) -> AccessStateResponse:
    return AccessStateResponse(
        tier="unknown",
        entitlementStatus="degraded",
        credits=None,
        features=AccessFeatures(
            aiChat=_degraded_feature(),
            photoAnalysis=_degraded_feature(),
            textMealAnalysis=_degraded_feature(),
            weeklyReport=_degraded_feature(),
            fullHistory=_degraded_feature(),
            cloudBackup=_degraded_feature(),
        ),
        refreshedAt=refreshed_at,
    )


async def get_access_state(user_id: str) -> AccessStateResponse:
    refreshed_at = utc_now()
    try:
        credits = await ai_credits_service.get_credits_status(user_id)
    except FirestoreServiceError:
        return _degraded_access_state(refreshed_at)

    return AccessStateResponse(
        tier=credits.tier,
        entitlementStatus="active" if credits.tier == "premium" else "inactive",
        credits=credits,
        features=_features_from_credits(credits),
        refreshedAt=refreshed_at,
    )
