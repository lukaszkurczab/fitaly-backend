from fastapi import APIRouter, Depends

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.schemas.access_state import AccessStateResponse
from app.services import access_state_service

router = APIRouter()


async def _get_current_access_state(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AccessStateResponse:
    return await access_state_service.get_access_state(current_user.uid)


@router.get("/billing/access-state", response_model=AccessStateResponse)
async def get_billing_access_state(
    access_state: AccessStateResponse = Depends(_get_current_access_state),
) -> AccessStateResponse:
    return access_state


@router.get("/me/access", response_model=AccessStateResponse)
async def get_me_access_state(
    access_state: AccessStateResponse = Depends(_get_current_access_state),
) -> AccessStateResponse:
    return access_state
