from fastapi import APIRouter, Depends

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.api.http_errors import raise_bad_request
from app.schemas.streak import (
    StreakRecalculateRequest,
    StreakResponse,
    StreakWriteRequest,
)
from app.services import streak_service
from app.services.streak_service import (
    StreakState,
    StreakValidationError,
    _streak_current,
    _streak_last_date,
)

router = APIRouter()


def _build_response(
    *,
    streak: StreakState,
    awarded_badge_ids: list[str] | None = None,
) -> StreakResponse:
    return StreakResponse(
        current=_streak_current(streak),
        lastDate=_streak_last_date(streak),
        awardedBadgeIds=awarded_badge_ids or [],
    )


@router.get("/users/me/streak", response_model=StreakResponse)
async def get_streak_me(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> StreakResponse:
    streak = await streak_service.get_streak(current_user.uid)
    return _build_response(streak=streak)


@router.post("/users/me/streak/ensure", response_model=StreakResponse)
async def ensure_streak_me(
    request: StreakWriteRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> StreakResponse:
    try:
        streak, awarded_badge_ids = await streak_service.ensure_streak(
            current_user.uid,
            request.dayKey,
        )
    except StreakValidationError as exc:
        raise_bad_request(exc)

    return _build_response(streak=streak, awarded_badge_ids=awarded_badge_ids)


@router.post("/users/me/streak/reset-if-missed", response_model=StreakResponse)
async def reset_streak_if_missed_me(
    request: StreakWriteRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> StreakResponse:
    try:
        streak, awarded_badge_ids = await streak_service.reset_streak_if_missed(
            current_user.uid,
            request.dayKey,
        )
    except StreakValidationError as exc:
        raise_bad_request(exc)

    return _build_response(streak=streak, awarded_badge_ids=awarded_badge_ids)


@router.post("/users/me/streak/recalculate", response_model=StreakResponse)
async def recalculate_streak_me(
    request: StreakRecalculateRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> StreakResponse:
    try:
        streak, awarded_badge_ids = await streak_service.recalculate_streak(
            user_id=current_user.uid,
            day_key=request.dayKey,
            todays_kcal=request.todaysKcal,
            target_kcal=request.targetKcal,
            threshold_pct=request.thresholdPct,
        )
    except StreakValidationError as exc:
        raise_bad_request(exc)

    return _build_response(streak=streak, awarded_badge_ids=awarded_badge_ids)
