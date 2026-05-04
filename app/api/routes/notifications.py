"""Canonical notification preference routes."""

from fastapi import APIRouter, Depends

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.api.http_errors import raise_bad_request
from app.schemas.notification import (
    NotificationPrefsPayload,
    NotificationPrefsResponse,
    NotificationPrefsUpdateRequest,
    NotificationPrefsUpdateResponse,
)
from app.services import notification_service
from app.services.notification_service import NotificationPrefsValidationError

router = APIRouter()


@router.get(
    "/users/me/notifications/preferences",
    response_model=NotificationPrefsResponse,
    summary="Notification preferences",
    description=(
        "Active settings surface for notification-related preferences "
        "(including smart reminders and system notifications)."
    ),
)
async def get_notification_prefs_me(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> NotificationPrefsResponse:
    notifications = await notification_service.get_notification_prefs(current_user.uid)
    return NotificationPrefsResponse(
        notifications=NotificationPrefsPayload.model_validate(notifications)
    )


@router.post(
    "/users/me/notifications/preferences",
    response_model=NotificationPrefsUpdateResponse,
    summary="Update notification preferences",
    description=(
        "Active settings surface for notification-related preferences "
        "(including smart reminders and system notifications)."
    ),
)
async def update_notification_prefs_me(
    request: NotificationPrefsUpdateRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> NotificationPrefsUpdateResponse:
    try:
        notifications = await notification_service.update_notification_prefs(
            current_user.uid,
            request.notifications.model_dump(exclude_unset=True),
        )
    except NotificationPrefsValidationError as exc:
        raise_bad_request(exc)

    return NotificationPrefsUpdateResponse(
        notifications=NotificationPrefsPayload.model_validate(notifications),
        updated=True,
    )
