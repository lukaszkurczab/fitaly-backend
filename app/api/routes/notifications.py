from fastapi import APIRouter, Depends

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.api.http_errors import raise_bad_request
from app.schemas.notification import (
    NotificationDeleteResponse,
    NotificationListResponse,
    NotificationPrefsPayload,
    NotificationPrefsResponse,
    NotificationPrefsUpdateRequest,
    NotificationPrefsUpdateResponse,
    NotificationUpsertResponse,
    UserNotificationItem,
)
from app.schemas.notification_plan import (
    NotificationPlanItem,
    NotificationPlanRequest,
    NotificationPlanResponse,
    NotificationTime,
)
from app.services import notification_plan_service, notification_service
from app.services.notification_service import (
    NotificationPrefsValidationError,
    NotificationValidationError,
)

router = APIRouter()


@router.post(
    "/users/me/notifications/reconcile-plan",
    response_model=NotificationPlanResponse,
)
async def reconcile_notification_plan_me(
    request: NotificationPlanRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> NotificationPlanResponse:
    ai_style, plans = await notification_plan_service.get_notification_plan(
        current_user.uid,
        start_iso=request.startIso,
        end_iso=request.endIso,
    )

    return NotificationPlanResponse(
        aiStyle=ai_style,
        plans=[
            NotificationPlanItem(
                id=plan.id,
                type=plan.type,
                enabled=plan.enabled,
                text=plan.text,
                time=NotificationTime(hour=plan.time.hour, minute=plan.time.minute),
                days=plan.days,
                mealKind=plan.meal_kind,
                shouldSchedule=plan.should_schedule,
                missingKcal=plan.missing_kcal,
            )
            for plan in plans
        ],
    )


@router.get("/users/me/notifications", response_model=NotificationListResponse)
async def list_notifications_me(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> NotificationListResponse:
    items = await notification_service.list_notifications(current_user.uid)
    return NotificationListResponse(
        items=[UserNotificationItem.model_validate(item) for item in items]
    )


@router.post("/users/me/notifications", response_model=NotificationUpsertResponse)
async def upsert_notification_me(
    request: UserNotificationItem,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> NotificationUpsertResponse:
    try:
        item = await notification_service.upsert_notification(
            current_user.uid,
            request.model_dump(),
        )
    except NotificationValidationError as exc:
        raise_bad_request(exc)

    return NotificationUpsertResponse(
        item=UserNotificationItem.model_validate(item),
        updated=True,
    )


@router.post(
    "/users/me/notifications/{notificationId}/delete",
    response_model=NotificationDeleteResponse,
)
async def delete_notification_me(
    notificationId: str,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> NotificationDeleteResponse:
    try:
        await notification_service.delete_notification(current_user.uid, notificationId)
    except NotificationValidationError as exc:
        raise_bad_request(exc)

    return NotificationDeleteResponse(notificationId=notificationId, deleted=True)


@router.get(
    "/users/me/notifications/preferences",
    response_model=NotificationPrefsResponse,
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
