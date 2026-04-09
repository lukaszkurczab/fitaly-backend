from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.core.exceptions import FirestoreServiceError
from app.schemas.habits import HabitSignalsResponse
from app.services.habit_signal_service import get_habit_signals

router = APIRouter()


@router.get(
    "/users/me/habits",
    response_model=HabitSignalsResponse,
    summary="Get derived user habit signals",
    description=(
        "Returns the v2 Habit Signal Engine output for the authenticated user. "
        "The response is a structured behavior and data-quality summary built "
        "from meal history, designed as backend foundation for future coach, "
        "reminders, and nutrition-state features."
    ),
)
async def get_user_habits_me(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> HabitSignalsResponse:
    try:
        return await get_habit_signals(current_user.uid)
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute habit signals",
        ) from exc
