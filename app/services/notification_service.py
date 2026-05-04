"""Backend-owned notification preferences."""

from typing import Any, cast
import logging

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import (
    PREFS_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.db.firebase import get_firestore

logger = logging.getLogger(__name__)
GLOBAL_PREFS_DOCUMENT = "global"


class NotificationPrefsValidationError(Exception):
    """Raised when the notification preferences payload is invalid."""


def _as_object_map(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    raw_map = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for raw_key, raw_item in raw_map.items():
        if isinstance(raw_key, str):
            result[raw_key] = raw_item
    return result


def _prefs_document(user_id: str) -> firestore.DocumentReference:
    client: firestore.Client = get_firestore()
    return (
        client.collection(USERS_COLLECTION)
        .document(user_id)
        .collection(PREFS_SUBCOLLECTION)
        .document(GLOBAL_PREFS_DOCUMENT)
    )


def _normalize_quiet_hours(raw: object) -> dict[str, int] | None:
    if raw is None:
        return None
    raw_map = _as_object_map(raw)
    if raw_map is None:
        raise NotificationPrefsValidationError("Invalid quiet hours.")
    start_hour = raw_map.get("startHour")
    end_hour = raw_map.get("endHour")
    if not isinstance(start_hour, int) or not 0 <= start_hour <= 23:
        raise NotificationPrefsValidationError("Invalid quiet hours.")
    if not isinstance(end_hour, int) or not 0 <= end_hour <= 23:
        raise NotificationPrefsValidationError("Invalid quiet hours.")
    return {"startHour": start_hour, "endHour": end_hour}


def _normalize_weekdays(raw: object) -> list[int] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise NotificationPrefsValidationError("Invalid weekdays.")
    raw_days = cast(list[object], raw)
    days = sorted(
        {
            int(day)
            for day in raw_days
            if isinstance(day, int) and 0 <= day <= 6
        }
    )
    if len(days) != len(raw_days):
        raise NotificationPrefsValidationError("Invalid weekdays.")
    return days


def _normalize_notifications_prefs_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}

    if "smartRemindersEnabled" in payload:
        if not isinstance(payload["smartRemindersEnabled"], bool):
            raise NotificationPrefsValidationError("Invalid smartRemindersEnabled.")
        normalized["smartRemindersEnabled"] = payload["smartRemindersEnabled"]

    if "motivationEnabled" in payload:
        if not isinstance(payload["motivationEnabled"], bool):
            raise NotificationPrefsValidationError("Invalid motivationEnabled.")
        normalized["motivationEnabled"] = payload["motivationEnabled"]

    if "statsEnabled" in payload:
        if not isinstance(payload["statsEnabled"], bool):
            raise NotificationPrefsValidationError("Invalid statsEnabled.")
        normalized["statsEnabled"] = payload["statsEnabled"]

    if "weekdays0to6" in payload:
        normalized["weekdays0to6"] = _normalize_weekdays(payload.get("weekdays0to6"))

    if "daysAhead" in payload:
        days_ahead = payload["daysAhead"]
        if days_ahead is not None and (
            not isinstance(days_ahead, int) or days_ahead < 1 or days_ahead > 14
        ):
            raise NotificationPrefsValidationError("Invalid daysAhead.")
        normalized["daysAhead"] = days_ahead

    if "quietHours" in payload:
        normalized["quietHours"] = _normalize_quiet_hours(payload.get("quietHours"))

    return normalized


def _normalize_notifications_prefs_doc(raw: object) -> dict[str, Any]:
    raw_map = _as_object_map(raw)
    if raw_map is None:
        return {}

    notifications = _as_object_map(raw_map.get("notifications"))
    if notifications is None:
        return {}

    normalized: dict[str, Any] = {}

    smart_reminders_enabled = notifications.get("smartRemindersEnabled")
    if isinstance(smart_reminders_enabled, bool):
        normalized["smartRemindersEnabled"] = smart_reminders_enabled

    motivation_enabled = notifications.get("motivationEnabled")
    if isinstance(motivation_enabled, bool):
        normalized["motivationEnabled"] = motivation_enabled

    stats_enabled = notifications.get("statsEnabled")
    if isinstance(stats_enabled, bool):
        normalized["statsEnabled"] = stats_enabled

    weekdays = notifications.get("weekdays0to6")
    if isinstance(weekdays, list):
        weekdays_list = cast(list[object], weekdays)
        normalized_days = [
            day for day in weekdays_list if isinstance(day, int) and 0 <= day <= 6
        ]
        normalized["weekdays0to6"] = sorted(set(normalized_days))

    days_ahead = notifications.get("daysAhead")
    if isinstance(days_ahead, int) and 1 <= days_ahead <= 14:
        normalized["daysAhead"] = days_ahead

    quiet_hours = notifications.get("quietHours")
    quiet_hours_map = _as_object_map(quiet_hours)
    if quiet_hours_map is not None:
        start_hour = quiet_hours_map.get("startHour")
        end_hour = quiet_hours_map.get("endHour")
        if (
            isinstance(start_hour, int)
            and 0 <= start_hour <= 23
            and isinstance(end_hour, int)
            and 0 <= end_hour <= 23
        ):
            normalized["quietHours"] = {"startHour": start_hour, "endHour": end_hour}

    return normalized


async def get_notification_prefs(user_id: str) -> dict[str, Any]:
    prefs_ref = _prefs_document(user_id)

    try:
        snapshot = prefs_ref.get()
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to fetch notification prefs.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to fetch notification prefs.") from exc

    return _normalize_notifications_prefs_doc(snapshot.to_dict() or {})


async def update_notification_prefs(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_notifications_prefs_payload(payload)
    prefs_ref = _prefs_document(user_id)

    try:
        existing_snapshot = prefs_ref.get()
        existing = _normalize_notifications_prefs_doc(existing_snapshot.to_dict() or {})
        merged = dict(existing)
        merged.update(normalized)
        prefs_ref.set({"notifications": merged}, merge=True)
    except NotificationPrefsValidationError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to update notification prefs.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to update notification prefs.") from exc

    return merged
