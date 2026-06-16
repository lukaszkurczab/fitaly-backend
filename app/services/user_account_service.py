"""Business logic for account/profile mutations owned by the backend."""

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import re
from typing import Any, cast
from uuid import uuid4

from fastapi import UploadFile
from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, NotFound, RetryError
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.exceptions import FirestoreServiceError
from app.db.firebase import (
    build_storage_download_url,
    get_firestore,
    get_storage_bucket,
    get_storage_bucket_name,
)
from app.services.meal_storage import _validate_upload
from app.services import smart_memory_service, streak_service
from app.services.username_service import normalize_username

from app.core.firestore_constants import (
    AI_CREDITS_SUBCOLLECTION,
    AI_CREDIT_IDEMPOTENCY_SUBCOLLECTION,
    AI_CREDIT_TRANSACTIONS_SUBCOLLECTION,
    AI_RUNS_COLLECTION,
    BADGES_SUBCOLLECTION,
    BILLING_DOCUMENT_ID,
    BILLING_SUBCOLLECTION,
    CHAT_THREADS_SUBCOLLECTION,
    FEEDBACK_SUBCOLLECTION,
    INGREDIENT_PRODUCTS_SUBCOLLECTION,
    MEAL_TEMPLATES_SUBCOLLECTION,
    MESSAGES_SUBCOLLECTION,
    MEMORY_SUBCOLLECTION,
    SMART_MEMORY_CANDIDATES_SUBCOLLECTION,
    SMART_MEMORY_MUTATION_DEDUPE_SUBCOLLECTION,
    SMART_MEMORY_SETTINGS_SUBCOLLECTION,
    SMART_MEMORY_SUBCOLLECTION,
    SMART_MEMORY_TOMBSTONES_SUBCOLLECTION,
    STREAK_SUBCOLLECTION,
    USERNAMES_COLLECTION,
    USERS_COLLECTION,
)
from app.services.meal_service import MEAL_MUTATION_DEDUPE_SUBCOLLECTION
from app.services import telemetry_service
from app.services.reminder_decision_store import DAILY_STATS_SUBCOLLECTION

logger = logging.getLogger(__name__)
AiConsentDocument = dict[str, str | None]

DELETE_SUBCOLLECTIONS = (
    "meals",
    MEAL_TEMPLATES_SUBCOLLECTION,
    "chat_messages",
    "notifications",
    "prefs",
    "notif_meta",
    "feedback",
    MEAL_MUTATION_DEDUPE_SUBCOLLECTION,
    INGREDIENT_PRODUCTS_SUBCOLLECTION,
    SMART_MEMORY_SUBCOLLECTION,
    SMART_MEMORY_CANDIDATES_SUBCOLLECTION,
    SMART_MEMORY_SETTINGS_SUBCOLLECTION,
    SMART_MEMORY_TOMBSTONES_SUBCOLLECTION,
    SMART_MEMORY_MUTATION_DEDUPE_SUBCOLLECTION,
    BADGES_SUBCOLLECTION,
    STREAK_SUBCOLLECTION,
    DAILY_STATS_SUBCOLLECTION,
)
TELEMETRY_EVENTS_COLLECTION = telemetry_service.COLLECTION_NAME
BATCH_DELETE_LIMIT = 500
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$")
MIN_USERNAME_LENGTH = 3
EDITABLE_PROFILE_FIELDS = frozenset(
    {
        "profile",
    }
)
EDITABLE_PROFILE_DOCUMENT_FIELDS = frozenset(
    {
        "language",
        "nutritionProfile",
        "aiPreferences",
    }
)

LEGACY_PROFILE_FIELDS = (
    "avatarLocalPath",
    "unitsSystem",
    "age",
    "sex",
    "height",
    "heightInch",
    "weight",
    "preferences",
    "activityLevel",
    "goal",
    "chronicDiseases",
    "chronicDiseasesOther",
    "allergies",
    "allergiesOther",
    "lifestyle",
    "aiPersona",
    "readiness",
    "calorieTarget",
    "language",
)


class EmailValidationError(Exception):
    """Raised when the email pending payload is invalid."""


class UserProfileValidationError(Exception):
    """Raised when the user profile payload contains forbidden fields."""


class UserProfileMutationDedupeConflictError(ValueError):
    """Raised when a clientMutationId is reused for a different profile mutation."""


class OnboardingValidationError(Exception):
    """Raised when onboarding input payload is invalid."""


class OnboardingUsernameUnavailableError(Exception):
    """Raised when onboarding username is already owned by another user."""


def normalize_email(raw: object) -> str:
    return str(raw or "").strip()


def _normalize_language(raw: object) -> str:
    value = str(raw or "").strip().lower()
    if value == "pl" or value.startswith("pl-"):
        return "pl"
    return "en"


def _is_valid_username(username: str) -> bool:
    return len(username) >= MIN_USERNAME_LENGTH


def _validate_email(email: str) -> None:
    if not EMAIL_RE.match(email):
        raise EmailValidationError("Invalid email address.")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_timestamp_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _storage_emulator_configured() -> bool:
    return bool(os.getenv("FIREBASE_STORAGE_EMULATOR_HOST", "").strip())


def _default_nutrition_profile() -> dict[str, Any]:
    return {
        "unitsSystem": "metric",
        "age": "",
        "sex": "female",
        "height": "",
        "heightInch": "",
        "weight": "",
        "preferences": [],
        "activityLevel": "moderate",
        "goal": "maintain",
        "chronicDiseases": [],
        "chronicDiseasesOther": "",
        "allergies": [],
        "allergiesOther": "",
        "lifestyle": "",
        "calorieTarget": 0,
    }


def _default_ai_consent() -> AiConsentDocument:
    return {
        "status": "not_granted",
        "grantedAt": None,
        "revokedAt": None,
    }


def _normalize_ai_consent(raw: object) -> AiConsentDocument:
    if not isinstance(raw, dict):
        return _default_ai_consent()
    payload = cast(dict[str, Any], raw)
    status_raw = payload.get("status")
    status = (
        status_raw
        if status_raw in {"not_granted", "granted", "revoked"}
        else "not_granted"
    )
    granted_at = payload.get("grantedAt")
    revoked_at = payload.get("revokedAt")
    return {
        "status": cast(str, status),
        "grantedAt": granted_at if isinstance(granted_at, str) else None,
        "revokedAt": revoked_at if isinstance(revoked_at, str) else None,
    }


def _has_active_ai_consent(ai_consent: AiConsentDocument) -> bool:
    return (
        ai_consent.get("status") == "granted"
        and bool(ai_consent.get("grantedAt"))
        and ai_consent.get("revokedAt") is None
    )


def _canonicalize_profile_contract(profile: dict[str, Any]) -> dict[str, Any]:
    canonical = _deep_merge_dict(_default_profile(), profile)
    canonical.pop("consents", None)
    canonical["aiConsent"] = _normalize_ai_consent(canonical.get("aiConsent"))
    return canonical


def _profile_write_with_legacy_consents_delete(
    canonical_profile: dict[str, Any],
) -> dict[str, Any]:
    profile_write = dict(canonical_profile)
    profile_write["consents"] = firestore.DELETE_FIELD
    return profile_write


def _default_profile(normalized_language: str = "en") -> dict[str, Any]:
    return {
        "language": normalized_language,
        "nutritionProfile": _default_nutrition_profile(),
        "aiPreferences": {"stylePersona": "calm_guide"},
        "aiConsent": _default_ai_consent(),
        "readiness": {
            "status": "needs_profile",
            "onboardingCompletedAt": None,
            "readyAt": None,
        },
    }


def _deep_merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(
                cast(dict[str, Any], existing),
                cast(dict[str, Any], value),
            )
        else:
            merged[key] = value
    return merged


def _legacy_delete_document() -> dict[str, Any]:
    return {field: firestore.DELETE_FIELD for field in LEGACY_PROFILE_FIELDS}


def _remove_legacy_fields(document: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(document)
    for field in LEGACY_PROFILE_FIELDS:
        cleaned.pop(field, None)
    return cleaned


def _apply_confirmed_auth_email(
    document: dict[str, Any],
    *,
    existing: dict[str, Any],
    auth_email: str | None,
) -> None:
    normalized_email = normalize_email(auth_email)
    if not normalized_email:
        return

    document["email"] = normalized_email
    if normalize_email(existing.get("emailPending")) == normalized_email:
        document["emailPending"] = firestore.DELETE_FIELD


def _apply_local_only_profile_cleanup(
    document: dict[str, Any],
    *,
    existing: dict[str, Any],
) -> None:
    if "avatarLocalPath" in existing:
        document["avatarLocalPath"] = firestore.DELETE_FIELD


def _merge_document_for_response(
    existing: dict[str, Any],
    document: dict[str, Any],
) -> dict[str, Any]:
    merged = _remove_legacy_fields(dict(existing))
    for key, value in document.items():
        if value is firestore.DELETE_FIELD:
            merged.pop(key, None)
        else:
            merged[key] = value
    profile = merged.get("profile")
    if isinstance(profile, dict):
        merged["profile"] = _canonicalize_profile_contract(cast(dict[str, Any], profile))
    return merged


def _build_onboarding_profile_document(
    *,
    user_id: str,
    normalized_username: str,
    normalized_language: str,
    auth_email: str | None,
    now_iso: str,
    now_ms: int,
    existing: dict[str, Any],
) -> dict[str, Any]:
    profile = dict(existing)

    profile["uid"] = user_id
    profile["username"] = normalized_username
    if auth_email:
        profile["email"] = auth_email

    profile.setdefault("createdAt", now_ms)
    profile.setdefault("lastLogin", now_iso)
    profile.setdefault("plan", "free")
    existing_profile = profile.get("profile")
    profile["profile"] = _canonicalize_profile_contract(
        _deep_merge_dict(
            _default_profile(normalized_language),
            cast(dict[str, Any], existing_profile)
            if isinstance(existing_profile, dict)
            else {},
        )
    )
    profile.setdefault("syncState", "pending")
    profile.setdefault("lastSyncedAt", "")
    profile.setdefault("avatarUrl", "")
    profile.setdefault("avatarlastSyncedAt", "")

    return _remove_legacy_fields(profile)


@firestore.transactional
def _initialize_onboarding_profile_transaction(
    transaction: firestore.Transaction,
    *,
    user_ref: firestore.DocumentReference,
    usernames_collection: firestore.CollectionReference,
    username_ref: firestore.DocumentReference,
    user_id: str,
    normalized_username: str,
    normalized_language: str,
    auth_email: str | None,
    now_iso: str,
    now_ms: int,
) -> dict[str, Any]:
    username_snapshot = username_ref.get(transaction=transaction)
    if username_snapshot.exists:
        username_data = username_snapshot.to_dict() or {}
        owner_id = username_data.get("uid")
        if isinstance(owner_id, str) and owner_id and owner_id != user_id:
            raise OnboardingUsernameUnavailableError("Username unavailable.")

    user_snapshot = user_ref.get(transaction=transaction)
    existing = dict(user_snapshot.to_dict() or {}) if user_snapshot.exists else {}
    previous_username = normalize_username(existing.get("username"))

    profile_document = _build_onboarding_profile_document(
        user_id=user_id,
        normalized_username=normalized_username,
        normalized_language=normalized_language,
        auth_email=auth_email,
        now_iso=now_iso,
        now_ms=now_ms,
        existing=existing,
    )
    profile_write_document = dict(profile_document)
    profile_payload = profile_write_document.get("profile")
    if isinstance(profile_payload, dict):
        profile_write_document["profile"] = _profile_write_with_legacy_consents_delete(
            cast(dict[str, Any], profile_payload)
        )

    transaction.set(username_ref, {"uid": user_id}, merge=True)
    transaction.set(
        user_ref,
        {**_legacy_delete_document(), **profile_write_document},
        merge=True,
    )

    if previous_username and previous_username != normalized_username:
        transaction.delete(usernames_collection.document(previous_username))

    return profile_document


def _sanitize_profile_patch(payload: dict[str, Any]) -> dict[str, Any]:
    invalid_keys = sorted(key for key in payload if key not in EDITABLE_PROFILE_FIELDS)
    if invalid_keys:
        joined = ", ".join(invalid_keys)
        raise UserProfileValidationError(f"Forbidden profile fields: {joined}")

    patch = dict(payload)
    profile = patch.get("profile")
    if profile is not None and not isinstance(profile, dict):
        raise UserProfileValidationError("Profile payload must be an object.")
    if isinstance(profile, dict):
        profile_patch = cast(dict[str, Any], profile)
        invalid_profile_keys = sorted(
            key for key in profile_patch if key not in EDITABLE_PROFILE_DOCUMENT_FIELDS
        )
        if invalid_profile_keys:
            joined = ", ".join(f"profile.{key}" for key in invalid_profile_keys)
            raise UserProfileValidationError(f"Forbidden profile fields: {joined}")
    return patch


def _require_profile_client_mutation_id(value: object) -> str:
    client_mutation_id = str(value or "").strip()
    if not client_mutation_id:
        raise ValueError("Missing clientMutationId")
    return client_mutation_id


def _stable_profile_payload_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _profile_mutation_ref(
    client: firestore.Client,
    user_id: str,
    client_mutation_id: str,
) -> firestore.DocumentReference:
    mutation_hash = hashlib.sha256(client_mutation_id.encode("utf-8")).hexdigest()
    return client.collection(USERS_COLLECTION).document(user_id).collection(
        MEAL_MUTATION_DEDUPE_SUBCOLLECTION
    ).document(mutation_hash)


def _avatar_object_path(user_id: str, client_mutation_id: str) -> str:
    mutation_hash = hashlib.sha256(client_mutation_id.encode("utf-8")).hexdigest()
    return f"avatars/{user_id}/avatar.{mutation_hash}"


def _profile_mutation_record(
    *,
    user_id: str,
    client_mutation_id: str,
    payload_hash: str,
    result_profile: dict[str, Any],
    applied: bool,
) -> dict[str, Any]:
    return {
        "userId": user_id,
        "clientMutationId": client_mutation_id,
        "kind": "profile_update",
        "profileDocumentId": "user_profile",
        "payloadHash": payload_hash,
        "resultProfile": result_profile,
        "applied": applied,
        "createdAt": _utc_timestamp(),
    }


def _result_from_existing_profile_mutation(
    data: dict[str, Any],
    *,
    client_mutation_id: str,
    payload_hash: str,
) -> dict[str, Any]:
    if (
        data.get("clientMutationId") != client_mutation_id
        or data.get("kind") != "profile_update"
        or data.get("profileDocumentId") != "user_profile"
        or data.get("payloadHash") != payload_hash
    ):
        raise UserProfileMutationDedupeConflictError(
            "clientMutationId was already used for a different profile mutation"
        )

    result_profile = data.get("resultProfile")
    if not isinstance(result_profile, dict):
        raise UserProfileMutationDedupeConflictError(
            "clientMutationId record is incomplete"
        )
    return dict(cast(dict[str, Any], result_profile))


def _build_user_profile_update_document(
    *,
    user_id: str,
    sanitized_patch: dict[str, Any],
    existing: dict[str, Any],
    auth_email: str | None,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    document: dict[str, Any] = {**_legacy_delete_document(), "uid": user_id}
    _apply_confirmed_auth_email(
        document,
        existing=existing,
        auth_email=auth_email,
    )
    if "createdAt" not in existing:
        document["createdAt"] = _utc_timestamp_ms()
    if "plan" not in existing:
        document["plan"] = "free"
    if "syncState" not in existing:
        document["syncState"] = "pending"
    if "lastLogin" not in existing:
        document["lastLogin"] = _utc_timestamp()

    existing_profile = existing.get("profile")
    patch_profile = sanitized_patch.get("profile")
    if isinstance(patch_profile, dict):
        document["profile"] = _profile_write_with_legacy_consents_delete(
            _canonicalize_profile_contract(
                _deep_merge_dict(
                    cast(dict[str, Any], existing_profile)
                    if isinstance(existing_profile, dict)
                    else _default_profile(),
                    cast(dict[str, Any], patch_profile),
                )
            )
        )

    merged = _merge_document_for_response(existing, document)
    nutrition_patch = (
        sanitized_patch.get("profile", {}).get("nutritionProfile")
        if isinstance(sanitized_patch.get("profile"), dict)
        else None
    )
    should_sync_streak = (
        isinstance(nutrition_patch, dict) and "calorieTarget" in nutrition_patch
    )
    return document, merged, should_sync_streak


@firestore.transactional
def _upsert_user_profile_mutation_transaction(
    transaction: firestore.Transaction,
    *,
    mutation_ref: firestore.DocumentReference,
    user_ref: firestore.DocumentReference,
    user_id: str,
    client_mutation_id: str,
    payload_hash: str,
    sanitized_patch: dict[str, Any],
    auth_email: str | None,
) -> tuple[dict[str, Any], bool, bool]:
    mutation_snapshot = mutation_ref.get(transaction=transaction)
    if mutation_snapshot.exists:
        return (
            _result_from_existing_profile_mutation(
                dict(mutation_snapshot.to_dict() or {}),
                client_mutation_id=client_mutation_id,
                payload_hash=payload_hash,
            ),
            False,
            False,
        )

    user_snapshot = user_ref.get(transaction=transaction)
    existing = dict(user_snapshot.to_dict() or {}) if user_snapshot.exists else {}
    document, merged, should_sync_streak = _build_user_profile_update_document(
        user_id=user_id,
        sanitized_patch=sanitized_patch,
        existing=existing,
        auth_email=auth_email,
    )
    transaction.set(user_ref, document, merge=True)
    transaction.set(
        mutation_ref,
        _profile_mutation_record(
            user_id=user_id,
            client_mutation_id=client_mutation_id,
            payload_hash=payload_hash,
            result_profile=merged,
            applied=True,
        ),
        merge=False,
    )
    return merged, True, should_sync_streak


async def set_email_pending(user_id: str, email: str) -> str:
    normalized_email = normalize_email(email)
    _validate_email(normalized_email)

    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)

    try:
        user_ref.set({"emailPending": normalized_email}, merge=True)
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to persist email pending state.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to persist email pending state.") from exc

    return normalized_email


async def _set_avatar_upload_metadata(
    user_id: str,
    *,
    avatar_url: str,
    storage_path: str,
) -> tuple[str, str, dict[str, str]]:
    synced_at = _utc_timestamp()
    avatar_ref = {"storagePath": storage_path}

    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)

    try:
        user_ref.set(
            {
                "avatarRef": avatar_ref,
                "avatarUrl": avatar_url,
                "avatarlastSyncedAt": synced_at,
                "avatarLocalPath": firestore.DELETE_FIELD,
            },
            merge=True,
        )
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to persist avatar metadata.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to persist avatar metadata.") from exc

    return avatar_url, synced_at, avatar_ref


async def upload_avatar(
    user_id: str,
    upload: UploadFile,
    *,
    client_mutation_id: str,
) -> tuple[str, str, dict[str, str]]:
    normalized_client_mutation_id = _require_profile_client_mutation_id(
        client_mutation_id
    )
    bucket = get_storage_bucket()
    token = str(uuid4())
    object_path = _avatar_object_path(user_id, normalized_client_mutation_id)
    blob = bucket.blob(object_path)

    try:
        upload.file.seek(0)
        blob.metadata = {"firebaseStorageDownloadTokens": token}
        safe_content_type = _validate_upload(upload, require_detected_image=True)
        blob.upload_from_file(upload.file, content_type=safe_content_type)
        if not _storage_emulator_configured():
            blob.patch()
    except (FirebaseError, GoogleAPICallError, RetryError, OSError) as exc:
        logger.exception(
            "Failed to upload avatar.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to upload avatar.") from exc
    finally:
        upload.file.close()

    avatar_url = build_storage_download_url(
        get_storage_bucket_name(bucket),
        object_path,
        token,
    )
    return await _set_avatar_upload_metadata(
        user_id,
        avatar_url=avatar_url,
        storage_path=object_path,
    )


async def get_user_profile_data(
    user_id: str,
    *,
    touch_last_login: bool = False,
    auth_email: str | None = None,
) -> dict[str, Any] | None:
    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)

    try:
        snapshot = user_ref.get()
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to fetch user profile data.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to fetch user profile data.") from exc

    if not snapshot.exists:
        return None

    profile = dict(snapshot.to_dict() or {})

    document: dict[str, Any] = {}
    if touch_last_login:
        document["lastLogin"] = _utc_timestamp()
    _apply_confirmed_auth_email(
        document,
        existing=profile,
        auth_email=auth_email,
    )
    _apply_local_only_profile_cleanup(document, existing=profile)

    if document:
        try:
            user_ref.set(document, merge=True)
        except (FirebaseError, GoogleAPICallError, RetryError) as exc:
            logger.exception(
                "Failed to update user profile bootstrap metadata.",
                extra={"user_id": user_id},
            )
            raise FirestoreServiceError(
                "Failed to update user profile bootstrap metadata."
            ) from exc
        profile = _merge_document_for_response(profile, document)

    return _merge_document_for_response(profile, {})


async def upsert_user_profile_data(
    user_id: str,
    payload: dict[str, Any],
    *,
    client_mutation_id: str,
    auth_email: str | None = None,
) -> dict[str, Any]:
    sanitized_patch = _sanitize_profile_patch(payload)
    normalized_client_mutation_id = _require_profile_client_mutation_id(
        client_mutation_id
    )
    payload_hash = _stable_profile_payload_hash(
        {"kind": "profile_update", "profile": sanitized_patch}
    )
    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)
    mutation_ref = _profile_mutation_ref(client, user_id, normalized_client_mutation_id)

    try:
        profile, applied, should_sync_streak = _upsert_user_profile_mutation_transaction(
            client.transaction(),
            mutation_ref=mutation_ref,
            user_ref=user_ref,
            user_id=user_id,
            client_mutation_id=normalized_client_mutation_id,
            payload_hash=payload_hash,
            sanitized_patch=sanitized_patch,
            auth_email=auth_email,
        )
    except UserProfileValidationError:
        raise
    except UserProfileMutationDedupeConflictError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to upsert user profile data.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to upsert user profile data.") from exc

    if applied and should_sync_streak:
        await streak_service.sync_streak_from_meals(user_id)

    return profile


async def complete_onboarding_profile(
    user_id: str,
    profile_patch: dict[str, Any],
    *,
    auth_email: str | None = None,
) -> dict[str, Any]:
    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)

    try:
        snapshot = user_ref.get()
        existing = dict(snapshot.to_dict() or {}) if snapshot.exists else {}
        username = str(existing.get("username") or "").strip()
        if not username:
            raise OnboardingValidationError("Onboarding profile must be initialized.")

        now_iso = _utc_timestamp()
        existing_profile = existing.get("profile")
        patch_profile = profile_patch.get("profile")
        canonical_profile = _canonicalize_profile_contract(
            _deep_merge_dict(
                cast(dict[str, Any], existing_profile)
                if isinstance(existing_profile, dict)
                else _default_profile(),
                cast(dict[str, Any], patch_profile) if isinstance(patch_profile, dict) else {},
            )
        )
        document: dict[str, Any] = {
            **_legacy_delete_document(),
            "uid": user_id,
            "lastLogin": now_iso,
            "profile": _profile_write_with_legacy_consents_delete(canonical_profile),
        }
        _apply_confirmed_auth_email(
            document,
            existing=existing,
            auth_email=auth_email,
        )
        if "createdAt" not in existing:
            document["createdAt"] = _utc_timestamp_ms()
        if "plan" not in existing:
            document["plan"] = "free"
        if "syncState" not in existing:
            document["syncState"] = "pending"

        user_ref.set(document, merge=True)
    except OnboardingValidationError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to complete onboarding profile.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to complete onboarding profile.") from exc

    merged = _merge_document_for_response(existing, document)
    await streak_service.sync_streak_from_meals(user_id)
    return merged


async def grant_ai_consent(
    user_id: str,
    *,
    auth_email: str | None = None,
) -> AiConsentDocument:
    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)
    now_iso = _utc_timestamp()

    try:
        snapshot = user_ref.get()
        existing = dict(snapshot.to_dict() or {}) if snapshot.exists else {}

        existing_profile = existing.get("profile")
        canonical_profile = _canonicalize_profile_contract(
            cast(dict[str, Any], existing_profile)
            if isinstance(existing_profile, dict)
            else _default_profile()
        )
        current_ai_consent = _normalize_ai_consent(canonical_profile.get("aiConsent"))
        next_ai_consent: AiConsentDocument = (
            current_ai_consent
            if _has_active_ai_consent(current_ai_consent)
            else {
                "status": "granted",
                "grantedAt": now_iso,
                "revokedAt": None,
            }
        )
        canonical_profile["aiConsent"] = next_ai_consent
        document: dict[str, Any] = {
            **_legacy_delete_document(),
            "uid": user_id,
            "profile": _profile_write_with_legacy_consents_delete(canonical_profile),
        }
        _apply_confirmed_auth_email(
            document,
            existing=existing,
            auth_email=auth_email,
        )
        if "createdAt" not in existing:
            document["createdAt"] = _utc_timestamp_ms()
        if "plan" not in existing:
            document["plan"] = "free"
        if "syncState" not in existing:
            document["syncState"] = "pending"
        if "lastLogin" not in existing:
            document["lastLogin"] = now_iso

        user_ref.set(document, merge=True)
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to grant AI consent.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to grant AI consent.") from exc

    return _normalize_ai_consent(canonical_profile.get("aiConsent"))


async def revoke_ai_consent(
    user_id: str,
    *,
    auth_email: str | None = None,
) -> AiConsentDocument:
    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)
    now_iso = _utc_timestamp()

    try:
        snapshot = user_ref.get()
        existing = dict(snapshot.to_dict() or {}) if snapshot.exists else {}

        existing_profile = existing.get("profile")
        canonical_profile = _canonicalize_profile_contract(
            cast(dict[str, Any], existing_profile)
            if isinstance(existing_profile, dict)
            else _default_profile()
        )
        current_ai_consent = _normalize_ai_consent(canonical_profile.get("aiConsent"))
        next_ai_consent: AiConsentDocument = (
            current_ai_consent
            if current_ai_consent.get("status") == "revoked"
            else {
                "status": "revoked",
                "grantedAt": current_ai_consent.get("grantedAt"),
                "revokedAt": now_iso,
            }
        )
        canonical_profile["aiConsent"] = next_ai_consent
        document: dict[str, Any] = {
            **_legacy_delete_document(),
            "uid": user_id,
            "profile": _profile_write_with_legacy_consents_delete(canonical_profile),
        }
        _apply_confirmed_auth_email(
            document,
            existing=existing,
            auth_email=auth_email,
        )
        if "createdAt" not in existing:
            document["createdAt"] = _utc_timestamp_ms()
        if "plan" not in existing:
            document["plan"] = "free"
        if "syncState" not in existing:
            document["syncState"] = "pending"
        if "lastLogin" not in existing:
            document["lastLogin"] = now_iso

        user_ref.set(document, merge=True)
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to revoke AI consent.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to revoke AI consent.") from exc

    return _normalize_ai_consent(canonical_profile.get("aiConsent"))


async def initialize_onboarding_profile(
    user_id: str,
    *,
    username: str,
    language: str | None = None,
    auth_email: str | None = None,
) -> tuple[str, dict[str, Any]]:
    normalized_username = normalize_username(username)
    if not _is_valid_username(normalized_username):
        raise OnboardingValidationError(
            f"Username must be at least {MIN_USERNAME_LENGTH} characters long."
        )

    normalized_language = _normalize_language(language)
    normalized_email = normalize_email(auth_email)

    client: firestore.Client = get_firestore()
    users_collection = client.collection(USERS_COLLECTION)
    usernames_collection = client.collection(USERNAMES_COLLECTION)
    user_ref = users_collection.document(user_id)
    username_ref = usernames_collection.document(normalized_username)
    transaction = client.transaction()
    now_iso = _utc_timestamp()
    now_ms = _utc_timestamp_ms()

    try:
        profile = _initialize_onboarding_profile_transaction(
            transaction,
            user_ref=user_ref,
            usernames_collection=usernames_collection,
            username_ref=username_ref,
            user_id=user_id,
            normalized_username=normalized_username,
            normalized_language=normalized_language,
            auth_email=normalized_email or None,
            now_iso=now_iso,
            now_ms=now_ms,
        )
    except OnboardingUsernameUnavailableError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to initialize onboarding profile.",
            extra={"user_id": user_id, "username": normalized_username},
        )
        raise FirestoreServiceError("Failed to initialize onboarding profile.") from exc

    return normalized_username, profile


def _delete_documents_in_batches(
    client: firestore.Client,
    documents: list[firestore.DocumentSnapshot],
) -> None:
    for index in range(0, len(documents), BATCH_DELETE_LIMIT):
        batch = client.batch()
        for document in documents[index : index + BATCH_DELETE_LIMIT]:
            batch.delete(document.reference)
        batch.commit()


def _read_subcollection_documents(
    user_ref: firestore.DocumentReference,
    subcollection_name: str,
) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for document in user_ref.collection(subcollection_name).stream():
        payload = dict(document.to_dict() or {})
        payload.setdefault("id", document.id)
        documents.append(payload)
    return documents


def _read_billing_export(
    user_ref: firestore.DocumentReference,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    billing_documents = _read_subcollection_documents(user_ref, BILLING_SUBCOLLECTION)
    ai_credits: list[dict[str, Any]] = []
    ai_credit_transactions: list[dict[str, Any]] = []
    ai_credit_idempotency: list[dict[str, Any]] = []
    billing_ids = [str(document.get("id") or "") for document in billing_documents]
    if BILLING_DOCUMENT_ID not in billing_ids:
        billing_ids.insert(0, BILLING_DOCUMENT_ID)

    for billing_id in billing_ids:
        if not billing_id:
            continue
        billing_ref = user_ref.collection(BILLING_SUBCOLLECTION).document(billing_id)
        for document in _read_subcollection_documents(
            billing_ref,
            AI_CREDITS_SUBCOLLECTION,
        ):
            document.setdefault("billingId", billing_id)
            ai_credits.append(document)
        for document in _read_subcollection_documents(
            billing_ref,
            AI_CREDIT_TRANSACTIONS_SUBCOLLECTION,
        ):
            document.setdefault("billingId", billing_id)
            ai_credit_transactions.append(document)
        for document in _read_subcollection_documents(
            billing_ref,
            AI_CREDIT_IDEMPOTENCY_SUBCOLLECTION,
        ):
            document.setdefault("billingId", billing_id)
            ai_credit_idempotency.append(document)

    return billing_documents, ai_credits, ai_credit_transactions, ai_credit_idempotency


def _read_telemetry_events(
    client: firestore.Client,
    user_id: str,
) -> list[dict[str, Any]]:
    query = client.collection(TELEMETRY_EVENTS_COLLECTION).where(
        filter=FieldFilter("userHash", "==", telemetry_service.build_user_hash(user_id))
    )
    events: list[dict[str, Any]] = []
    for document in query.stream():
        payload = dict(document.to_dict() or {})
        payload.setdefault("id", document.id)
        events.append(payload)
    return events


def _delete_chat_threads(
    client: firestore.Client,
    user_ref: firestore.DocumentReference,
) -> None:
    thread_documents = list(user_ref.collection(CHAT_THREADS_SUBCOLLECTION).stream())
    for thread_document in thread_documents:
        memory_documents = list(
            thread_document.reference.collection(MEMORY_SUBCOLLECTION).stream()
        )
        if memory_documents:
            _delete_documents_in_batches(client, memory_documents)

        message_documents = list(
            thread_document.reference.collection(MESSAGES_SUBCOLLECTION).stream()
        )
        if message_documents:
            _delete_documents_in_batches(client, message_documents)

    if thread_documents:
        _delete_documents_in_batches(client, thread_documents)


def _delete_telemetry_events(
    client: firestore.Client,
    user_id: str,
) -> None:
    query = client.collection(TELEMETRY_EVENTS_COLLECTION).where(
        filter=FieldFilter("userHash", "==", telemetry_service.build_user_hash(user_id))
    )
    documents = list(query.stream())
    if documents:
        _delete_documents_in_batches(client, documents)


def _read_chat_thread_messages(
    user_ref: firestore.DocumentReference,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for thread_document in user_ref.collection(CHAT_THREADS_SUBCOLLECTION).stream():
        thread_id = thread_document.id
        thread_data = dict(thread_document.to_dict() or {})
        for message_document in thread_document.reference.collection(
            MESSAGES_SUBCOLLECTION
        ).stream():
            payload = dict(message_document.to_dict() or {})
            payload.setdefault("id", message_document.id)
            payload.setdefault("threadId", thread_id)
            if thread_data.get("title") and "threadTitle" not in payload:
                payload["threadTitle"] = thread_data["title"]
            messages.append(payload)
    return messages


def _read_chat_thread_memory(
    user_ref: firestore.DocumentReference,
) -> list[dict[str, Any]]:
    memory_entries: list[dict[str, Any]] = []
    for thread_document in user_ref.collection(CHAT_THREADS_SUBCOLLECTION).stream():
        thread_id = thread_document.id
        for memory_document in thread_document.reference.collection(
            MEMORY_SUBCOLLECTION
        ).stream():
            payload = dict(memory_document.to_dict() or {})
            payload.setdefault("id", memory_document.id)
            payload.setdefault("threadId", thread_id)
            memory_entries.append(payload)
    return memory_entries


def _read_ai_runs(
    client: firestore.Client,
    user_id: str,
) -> list[dict[str, Any]]:
    query = client.collection(AI_RUNS_COLLECTION).where(
        filter=FieldFilter("userId", "==", user_id)
    )
    runs: list[dict[str, Any]] = []
    for document in query.stream():
        payload = dict(document.to_dict() or {})
        payload.setdefault("id", document.id)
        runs.append(payload)
    return runs


def _is_feedback_attachment_storage_path_for_user(
    *,
    user_id: str,
    storage_path: str,
) -> bool:
    parts = storage_path.split("/")
    return (
        len(parts) == 4
        and parts[0] == "feedback"
        and parts[1] == user_id
        and all(part.strip() and part != ".." for part in parts)
    )


def _feedback_attachment_storage_paths(
    *,
    payload: dict[str, Any],
    user_id: str,
) -> list[str]:
    storage_paths: list[str] = []
    attachment_ref = payload.get("attachmentRef")
    if isinstance(attachment_ref, dict):
        attachment_ref_payload = cast("dict[str, Any]", attachment_ref)
        storage_path = attachment_ref_payload.get("storagePath")
        if (
            isinstance(storage_path, str)
            and _is_feedback_attachment_storage_path_for_user(
                user_id=user_id,
                storage_path=storage_path,
            )
        ):
            storage_paths.append(storage_path)

    return storage_paths


def _delete_feedback_attachments(
    *,
    feedback_documents: list[firestore.DocumentSnapshot],
    user_id: str,
) -> None:
    if not feedback_documents:
        return

    bucket = get_storage_bucket()
    for document in feedback_documents:
        payload = dict(document.to_dict() or {})
        for storage_path in _feedback_attachment_storage_paths(
            payload=payload,
            user_id=user_id,
        ):
            try:
                bucket.blob(storage_path).delete()
            except NotFound:
                continue
            except Exception as exc:
                logger.exception(
                    "Failed to delete feedback attachment.",
                    extra={
                        "feedback_id": document.id,
                        "storage_path": storage_path,
                    },
                )
                raise FirestoreServiceError(
                    "Failed to delete feedback attachment."
                ) from exc


def _delete_billing_data(
    client: firestore.Client,
    user_ref: firestore.DocumentReference,
) -> None:
    def delete_billing_document_tree(
        billing_document_ref: firestore.DocumentReference,
    ) -> None:
        for subcollection_name in (
            AI_CREDITS_SUBCOLLECTION,
            AI_CREDIT_TRANSACTIONS_SUBCOLLECTION,
            AI_CREDIT_IDEMPOTENCY_SUBCOLLECTION,
        ):
            documents = list(billing_document_ref.collection(subcollection_name).stream())
            if documents:
                _delete_documents_in_batches(client, documents)

        snapshot = billing_document_ref.get()
        if snapshot.exists:
            billing_document_ref.delete()

    billing_collection = user_ref.collection(BILLING_SUBCOLLECTION)
    main_billing_ref = billing_collection.document(BILLING_DOCUMENT_ID)
    delete_billing_document_tree(main_billing_ref)

    billing_documents = list(user_ref.collection(BILLING_SUBCOLLECTION).stream())
    for billing_document in billing_documents:
        if billing_document.id == BILLING_DOCUMENT_ID:
            continue
        delete_billing_document_tree(billing_document.reference)


def _delete_ai_runs(
    client: firestore.Client,
    user_id: str,
) -> None:
    query = client.collection(AI_RUNS_COLLECTION).where(
        filter=FieldFilter("userId", "==", user_id)
    )
    documents = list(query.stream())
    if documents:
        _delete_documents_in_batches(client, documents)


def _delete_storage_prefix(bucket: Any, prefix: str) -> None:
    for blob in bucket.list_blobs(prefix=prefix):
        blob.delete()


def _delete_user_storage_assets(user_id: str) -> None:
    bucket = get_storage_bucket()
    prefixes = (
        f"avatars/{user_id}/",
        f"meals/{user_id}/",
        f"mealTemplates/{user_id}/",
    )
    for prefix in prefixes:
        _delete_storage_prefix(bucket, prefix)


async def delete_account_data(user_id: str) -> None:
    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)

    try:
        user_snapshot = user_ref.get()
        username = ""
        if user_snapshot.exists:
            user_data: dict[str, object] = user_snapshot.to_dict() or {}
            username = normalize_username(user_data.get("username"))

        feedback_documents = list(user_ref.collection(FEEDBACK_SUBCOLLECTION).stream())
        _delete_feedback_attachments(
            feedback_documents=feedback_documents,
            user_id=user_id,
        )
        _delete_telemetry_events(client, user_id)
        _delete_user_storage_assets(user_id)
        _delete_billing_data(client, user_ref)
        _delete_ai_runs(client, user_id)

        for subcollection_name in DELETE_SUBCOLLECTIONS:
            documents = (
                feedback_documents
                if subcollection_name == FEEDBACK_SUBCOLLECTION
                else list(user_ref.collection(subcollection_name).stream())
            )
            if documents:
                _delete_documents_in_batches(client, documents)

        _delete_chat_threads(client, user_ref)

        if username:
            client.collection(USERNAMES_COLLECTION).document(username).delete()

        user_ref.delete()
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to delete account data.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to delete account data.") from exc


async def get_user_export_data(
    user_id: str,
) -> tuple[
    dict[str, Any] | None,  # profile
    list[dict[str, Any]],  # meals
    list[dict[str, Any]],  # my meals
    list[dict[str, Any]],  # chat messages
    list[dict[str, Any]],  # chat memory
    list[dict[str, Any]],  # ai runs
    list[dict[str, Any]],  # notifications
    dict[str, Any],  # notification prefs
    list[dict[str, Any]],  # feedback
    list[dict[str, Any]],  # meal mutation dedupe
    list[dict[str, Any]],  # ingredient products
    list[dict[str, Any]],  # smart memory items
    list[dict[str, Any]],  # smart memory candidates
    list[dict[str, Any]],  # smart memory settings
    list[dict[str, Any]],  # smart memory tombstones
    list[dict[str, Any]],  # smart memory mutation dedupe
    list[dict[str, Any]],  # billing
    list[dict[str, Any]],  # ai credits
    list[dict[str, Any]],  # ai credit transactions
    list[dict[str, Any]],  # ai credit idempotency
    list[dict[str, Any]],  # badges
    list[dict[str, Any]],  # streak
    list[dict[str, Any]],  # reminder daily stats
    list[dict[str, Any]],  # telemetry events
]:
    client: firestore.Client = get_firestore()
    user_ref = client.collection(USERS_COLLECTION).document(user_id)

    try:
        user_snapshot = user_ref.get()
        profile = dict(user_snapshot.to_dict() or {}) if user_snapshot.exists else None
        meals = _read_subcollection_documents(user_ref, "meals")
        my_meals = _read_subcollection_documents(user_ref, MEAL_TEMPLATES_SUBCOLLECTION)
        chat_messages = _read_chat_thread_messages(user_ref)
        chat_memory = _read_chat_thread_memory(user_ref)
        ai_runs = _read_ai_runs(client, user_id)
        notifications = _read_subcollection_documents(user_ref, "notifications")
        prefs_documents = _read_subcollection_documents(user_ref, "prefs")
        feedback = _read_subcollection_documents(user_ref, FEEDBACK_SUBCOLLECTION)
        meal_mutation_dedupe = _read_subcollection_documents(
            user_ref,
            MEAL_MUTATION_DEDUPE_SUBCOLLECTION,
        )
        ingredient_products = _read_subcollection_documents(
            user_ref,
            INGREDIENT_PRODUCTS_SUBCOLLECTION,
        )
        smart_memory_export = smart_memory_service.read_export(user_ref)
        smart_memory_items = smart_memory_export["items"]
        smart_memory_candidates = smart_memory_export["candidates"]
        smart_memory_settings = smart_memory_export["settings"]
        smart_memory_tombstones = smart_memory_export["tombstones"]
        smart_memory_mutation_dedupe = smart_memory_export["mutationDedupe"]
        (
            billing,
            ai_credits,
            ai_credit_transactions,
            ai_credit_idempotency,
        ) = _read_billing_export(user_ref)
        badges = _read_subcollection_documents(user_ref, BADGES_SUBCOLLECTION)
        streak = _read_subcollection_documents(user_ref, STREAK_SUBCOLLECTION)
        reminder_daily_stats = _read_subcollection_documents(
            user_ref,
            DAILY_STATS_SUBCOLLECTION,
        )
        notification_prefs = {}
        for document in prefs_documents:
            notifications_value = document.get("notifications")
            if isinstance(notifications_value, dict):
                notification_prefs = dict(cast(dict[str, Any], notifications_value))
                break
        telemetry_events = _read_telemetry_events(client, user_id)
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to build user export payload.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to build user export payload.") from exc

    return (
        profile,
        meals,
        my_meals,
        chat_messages,
        chat_memory,
        ai_runs,
        notifications,
        notification_prefs,
        feedback,
        meal_mutation_dedupe,
        ingredient_products,
        smart_memory_items,
        smart_memory_candidates,
        smart_memory_settings,
        smart_memory_tombstones,
        smart_memory_mutation_dedupe,
        billing,
        ai_credits,
        ai_credit_transactions,
        ai_credit_idempotency,
        badges,
        streak,
        reminder_daily_stats,
        telemetry_events,
    )
