"""Firestore emulator evidence for account export/delete telemetry isolation."""

from __future__ import annotations

import json
import os
from typing import Any, cast
from uuid import uuid4

import pytest
from google.cloud import firestore
from pytest_mock import MockerFixture

from app.core.firestore_constants import (
    AI_CREDITS_SUBCOLLECTION,
    AI_CREDIT_IDEMPOTENCY_SUBCOLLECTION,
    AI_CREDIT_TRANSACTIONS_SUBCOLLECTION,
    AI_RUNS_COLLECTION,
    BADGES_SUBCOLLECTION,
    BILLING_SUBCOLLECTION,
    CHAT_THREADS_SUBCOLLECTION,
    FEEDBACK_SUBCOLLECTION,
    MEAL_TEMPLATES_SUBCOLLECTION,
    MEMORY_SUBCOLLECTION,
    MESSAGES_SUBCOLLECTION,
    STREAK_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.services.meal_service import MEAL_MUTATION_DEDUPE_SUBCOLLECTION
from app.services.reminder_decision_store import DAILY_STATS_SUBCOLLECTION
from app.services import telemetry_service, user_account_service


pytestmark = pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST")
    or not os.getenv("FIREBASE_STORAGE_EMULATOR_HOST"),
    reason="Firestore and Storage emulators are not configured.",
)


def _emulator_client() -> firestore.Client:
    project_id = os.getenv("FIREBASE_PROJECT_ID") or "demo-fitaly-local"
    database_id = os.getenv("FIRESTORE_DATABASE_ID") or "(default)"
    client_class = cast(Any, firestore.Client)
    return cast(firestore.Client, client_class(project=project_id, database=database_id))


def _export_payload(
    export_data: tuple[
        dict[str, Any] | None,
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, Any],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
    ],
) -> dict[str, Any]:
    (
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
        billing,
        ai_credits,
        ai_credit_transactions,
        ai_credit_idempotency,
        badges,
        streak,
        reminder_daily_stats,
        telemetry_events,
    ) = export_data
    return {
        "profile": profile,
        "meals": meals,
        "myMeals": my_meals,
        "chatMessages": chat_messages,
        "chatMemory": chat_memory,
        "aiRuns": ai_runs,
        "notifications": notifications,
        "notificationPrefs": notification_prefs,
        "feedback": feedback,
        "mealMutationDedupe": meal_mutation_dedupe,
        "billing": billing,
        "aiCredits": ai_credits,
        "aiCreditTransactions": ai_credit_transactions,
        "aiCreditIdempotency": ai_credit_idempotency,
        "badges": badges,
        "streak": streak,
        "reminderDailyStats": reminder_daily_stats,
        "telemetryEvents": telemetry_events,
    }


def _ids(items: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("id")) for item in items}


async def test_account_export_scopes_every_release_surface_and_preserves_refs(
    mocker: MockerFixture,
) -> None:
    client = _emulator_client()
    run_id = uuid4().hex
    current_user_id = f"ch-07-003-current-{run_id}"
    other_user_id = f"ch-07-003-other-{run_id}"
    current_user_hash = telemetry_service.build_user_hash(current_user_id)
    other_user_hash = telemetry_service.build_user_hash(other_user_id)
    current_user_ref = client.collection(USERS_COLLECTION).document(current_user_id)
    other_user_ref = client.collection(USERS_COLLECTION).document(other_user_id)
    current_event_ref = client.collection(telemetry_service.COLLECTION_NAME).document(
        f"telemetry-current-{run_id}"
    )
    other_event_ref = client.collection(telemetry_service.COLLECTION_NAME).document(
        f"telemetry-other-{run_id}"
    )
    anonymous_event_ref = client.collection(telemetry_service.COLLECTION_NAME).document(
        f"telemetry-anon-{run_id}"
    )
    current_ai_run_ref = client.collection(AI_RUNS_COLLECTION).document(
        f"ai-run-current-{run_id}"
    )
    other_ai_run_ref = client.collection(AI_RUNS_COLLECTION).document(
        f"ai-run-other-{run_id}"
    )
    provider_payload_ref = client.collection("provider_payloads").document(
        f"provider-payload-{run_id}"
    )
    log_ref = client.collection("operational_logs").document(f"log-{run_id}")
    sentry_ref = client.collection("sentry_events").document(f"sentry-{run_id}")
    secret_ref = client.collection("secrets").document(f"secret-{run_id}")
    seeded_refs: list[firestore.DocumentReference] = [
        current_user_ref,
        other_user_ref,
        current_event_ref,
        other_event_ref,
        anonymous_event_ref,
        current_ai_run_ref,
        other_ai_run_ref,
        provider_payload_ref,
        log_ref,
        sentry_ref,
        secret_ref,
    ]

    bucket = mocker.Mock()
    bucket.list_blobs.return_value = []

    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    mocker.patch("app.services.user_account_service.get_storage_bucket", return_value=bucket)

    current_user_ref.set(
        {
            "uid": current_user_id,
            "username": f"current-{run_id}",
            "profileMarker": f"current-profile-{run_id}",
            "avatarRef": {
                "storagePath": f"avatars/{current_user_id}/avatar.{run_id}.jpg"
            },
        }
    )
    other_user_ref.set(
        {
            "uid": other_user_id,
            "username": f"other-{run_id}",
            "profileMarker": f"other-profile-{run_id}",
            "avatarRef": {"storagePath": f"avatars/{other_user_id}/avatar.{run_id}.jpg"},
        }
    )

    surface_docs: tuple[tuple[firestore.DocumentReference, dict[str, Any]], ...] = (
        (
            current_user_ref.collection("meals").document(f"meal-current-{run_id}"),
            {
                "id": f"meal-current-{run_id}",
                "ownerMarker": f"current-meal-{run_id}",
                "imageRef": {
                    "storagePath": f"meals/{current_user_id}/meal-{run_id}.jpg"
                },
            },
        ),
        (
            other_user_ref.collection("meals").document(f"meal-other-{run_id}"),
            {
                "id": f"meal-other-{run_id}",
                "ownerMarker": f"other-meal-{run_id}",
                "imageRef": {"storagePath": f"meals/{other_user_id}/meal-{run_id}.jpg"},
            },
        ),
        (
            current_user_ref.collection(MEAL_TEMPLATES_SUBCOLLECTION).document(
                f"saved-current-{run_id}"
            ),
            {
                "id": f"saved-current-{run_id}",
                "ownerMarker": f"current-saved-meal-{run_id}",
                "imageRef": {
                    "storagePath": (
                        f"mealTemplates/{current_user_id}/saved-{run_id}.jpg"
                    )
                },
            },
        ),
        (
            other_user_ref.collection(MEAL_TEMPLATES_SUBCOLLECTION).document(
                f"saved-other-{run_id}"
            ),
            {
                "id": f"saved-other-{run_id}",
                "ownerMarker": f"other-saved-meal-{run_id}",
                "imageRef": {
                    "storagePath": f"mealTemplates/{other_user_id}/saved-{run_id}.jpg"
                },
            },
        ),
        (
            current_user_ref.collection("notifications").document(
                f"notification-current-{run_id}"
            ),
            {
                "id": f"notification-current-{run_id}",
                "ownerMarker": f"current-notification-{run_id}",
            },
        ),
        (
            other_user_ref.collection("notifications").document(
                f"notification-other-{run_id}"
            ),
            {
                "id": f"notification-other-{run_id}",
                "ownerMarker": f"other-notification-{run_id}",
            },
        ),
        (
            current_user_ref.collection("prefs").document("notifications"),
            {
                "id": "notifications",
                "notifications": {
                    "smartRemindersEnabled": True,
                    "motivationEnabled": True,
                    "statsEnabled": False,
                    "ownerMarker": f"current-notification-prefs-{run_id}",
                },
            },
        ),
        (
            other_user_ref.collection("prefs").document("notifications"),
            {
                "id": "notifications",
                "notifications": {
                    "smartRemindersEnabled": False,
                    "ownerMarker": f"other-notification-prefs-{run_id}",
                },
            },
        ),
        (
            current_user_ref.collection(FEEDBACK_SUBCOLLECTION).document(
                f"feedback-current-{run_id}"
            ),
            {
                "id": f"feedback-current-{run_id}",
                "ownerMarker": f"current-feedback-{run_id}",
                "attachmentRef": {
                    "storagePath": f"feedback/{current_user_id}/{run_id}/ticket.txt"
                },
            },
        ),
        (
            other_user_ref.collection(FEEDBACK_SUBCOLLECTION).document(
                f"feedback-other-{run_id}"
            ),
            {
                "id": f"feedback-other-{run_id}",
                "ownerMarker": f"other-feedback-{run_id}",
                "attachmentRef": {
                    "storagePath": f"feedback/{other_user_id}/{run_id}/ticket.txt"
                },
            },
        ),
        (
            current_user_ref.collection(MEAL_MUTATION_DEDUPE_SUBCOLLECTION).document(
                f"mutation-current-{run_id}"
            ),
            {
                "id": f"mutation-current-{run_id}",
                "ownerMarker": f"current-meal-mutation-{run_id}",
                "clientMutationId": f"current-mutation-{run_id}",
            },
        ),
        (
            other_user_ref.collection(MEAL_MUTATION_DEDUPE_SUBCOLLECTION).document(
                f"mutation-other-{run_id}"
            ),
            {
                "id": f"mutation-other-{run_id}",
                "ownerMarker": f"other-meal-mutation-{run_id}",
                "clientMutationId": f"other-mutation-{run_id}",
            },
        ),
        (
            current_user_ref.collection(DAILY_STATS_SUBCOLLECTION).document(
                f"2026-03-03-{run_id}"
            ),
            {
                "sendCount": 2,
                "emittedDecisionKeys": [
                    f"2026-03-03:breakfast:current-reminder-{run_id}"
                ],
                "ownerMarker": f"current-reminder-daily-stats-{run_id}",
            },
        ),
        (
            other_user_ref.collection(DAILY_STATS_SUBCOLLECTION).document(
                f"2026-03-03-{run_id}"
            ),
            {
                "sendCount": 9,
                "emittedDecisionKeys": [
                    f"2026-03-03:breakfast:other-reminder-{run_id}"
                ],
                "ownerMarker": f"other-reminder-daily-stats-{run_id}",
            },
        ),
    )
    for document_ref, payload in surface_docs:
        document_ref.set(payload)
        seeded_refs.append(document_ref)

    billing_docs: tuple[tuple[firestore.DocumentReference, dict[str, Any]], ...] = (
        (
            current_user_ref.collection(BILLING_SUBCOLLECTION).document("main"),
            {
                "id": "main",
                "status": "active",
                "ownerMarker": f"current-billing-main-{run_id}",
            },
        ),
        (
            current_user_ref.collection(BILLING_SUBCOLLECTION).document("annual"),
            {
                "id": "annual",
                "status": "trialing",
                "ownerMarker": f"current-billing-annual-{run_id}",
            },
        ),
        (
            other_user_ref.collection(BILLING_SUBCOLLECTION).document("main"),
            {
                "id": "main",
                "status": "active",
                "ownerMarker": f"other-billing-main-{run_id}",
            },
        ),
    )
    for document_ref, payload in billing_docs:
        document_ref.set(payload)
        seeded_refs.append(document_ref)

    billing_child_docs: tuple[tuple[firestore.DocumentReference, dict[str, Any]], ...] = (
        (
            billing_docs[0][0].collection(AI_CREDITS_SUBCOLLECTION).document("current"),
            {
                "id": "current",
                "balance": 8,
                "ownerMarker": f"current-ai-credit-main-{run_id}",
            },
        ),
        (
            billing_docs[1][0].collection(AI_CREDITS_SUBCOLLECTION).document("renewal"),
            {
                "id": "renewal",
                "balance": 20,
                "ownerMarker": f"current-ai-credit-annual-{run_id}",
            },
        ),
        (
            billing_docs[2][0].collection(AI_CREDITS_SUBCOLLECTION).document("current"),
            {
                "id": "current",
                "balance": 999,
                "ownerMarker": f"other-ai-credit-main-{run_id}",
            },
        ),
        (
            billing_docs[0][0]
            .collection(AI_CREDIT_TRANSACTIONS_SUBCOLLECTION)
            .document(f"tx-current-{run_id}"),
            {
                "id": f"tx-current-{run_id}",
                "amount": -1,
                "ownerMarker": f"current-ai-credit-transaction-{run_id}",
            },
        ),
        (
            billing_docs[2][0]
            .collection(AI_CREDIT_TRANSACTIONS_SUBCOLLECTION)
            .document(f"tx-other-{run_id}"),
            {
                "id": f"tx-other-{run_id}",
                "amount": -1,
                "ownerMarker": f"other-ai-credit-transaction-{run_id}",
            },
        ),
        (
            billing_docs[0][0]
            .collection(AI_CREDIT_IDEMPOTENCY_SUBCOLLECTION)
            .document(f"idem-current-{run_id}"),
            {
                "id": f"idem-current-{run_id}",
                "state": "deducted",
                "ownerMarker": f"current-ai-credit-idempotency-{run_id}",
            },
        ),
        (
            billing_docs[2][0]
            .collection(AI_CREDIT_IDEMPOTENCY_SUBCOLLECTION)
            .document(f"idem-other-{run_id}"),
            {
                "id": f"idem-other-{run_id}",
                "state": "deducted",
                "ownerMarker": f"other-ai-credit-idempotency-{run_id}",
            },
        ),
    )
    for document_ref, payload in billing_child_docs:
        document_ref.set(payload)
        seeded_refs.append(document_ref)

    gamification_docs: tuple[tuple[firestore.DocumentReference, dict[str, Any]], ...] = (
        (
            current_user_ref.collection(BADGES_SUBCOLLECTION).document("streak_7"),
            {
                "id": "streak_7",
                "type": "streak",
                "ownerMarker": f"current-badge-{run_id}",
            },
        ),
        (
            other_user_ref.collection(BADGES_SUBCOLLECTION).document("streak_7"),
            {
                "id": "streak_7",
                "type": "streak",
                "ownerMarker": f"other-badge-{run_id}",
            },
        ),
        (
            current_user_ref.collection(STREAK_SUBCOLLECTION).document("main"),
            {
                "id": "main",
                "current": 7,
                "lastDate": "2026-03-03",
                "ownerMarker": f"current-streak-{run_id}",
            },
        ),
        (
            other_user_ref.collection(STREAK_SUBCOLLECTION).document("main"),
            {
                "id": "main",
                "current": 3,
                "lastDate": "2026-03-01",
                "ownerMarker": f"other-streak-{run_id}",
            },
        ),
    )
    for document_ref, payload in gamification_docs:
        document_ref.set(payload)
        seeded_refs.append(document_ref)

    current_thread_ref = current_user_ref.collection(CHAT_THREADS_SUBCOLLECTION).document(
        f"thread-current-{run_id}"
    )
    other_thread_ref = other_user_ref.collection(CHAT_THREADS_SUBCOLLECTION).document(
        f"thread-other-{run_id}"
    )
    current_message_ref = current_thread_ref.collection(MESSAGES_SUBCOLLECTION).document(
        f"message-current-{run_id}"
    )
    other_message_ref = other_thread_ref.collection(MESSAGES_SUBCOLLECTION).document(
        f"message-other-{run_id}"
    )
    current_memory_ref = current_thread_ref.collection(MEMORY_SUBCOLLECTION).document(
        f"memory-current-{run_id}"
    )
    other_memory_ref = other_thread_ref.collection(MEMORY_SUBCOLLECTION).document(
        f"memory-other-{run_id}"
    )
    chat_docs: tuple[tuple[firestore.DocumentReference, dict[str, Any]], ...] = (
        (
            current_thread_ref,
            {"id": current_thread_ref.id, "title": f"Current thread {run_id}"},
        ),
        (other_thread_ref, {"id": other_thread_ref.id, "title": f"Other thread {run_id}"}),
        (
            current_message_ref,
            {
                "id": current_message_ref.id,
                "role": "user",
                "content": f"current-chat-message-{run_id}",
            },
        ),
        (
            other_message_ref,
            {
                "id": other_message_ref.id,
                "role": "user",
                "content": f"other-chat-message-{run_id}",
            },
        ),
        (
            current_memory_ref,
            {
                "id": current_memory_ref.id,
                "summary": f"current-chat-memory-{run_id}",
                "resolvedFacts": ["current user prefers simple dinners"],
            },
        ),
        (
            other_memory_ref,
            {
                "id": other_memory_ref.id,
                "summary": f"other-chat-memory-{run_id}",
                "resolvedFacts": ["other user prefers pasta"],
            },
        ),
    )
    for document_ref, payload in chat_docs:
        document_ref.set(payload)
        seeded_refs.append(document_ref)

    current_ai_run_ref.set(
        {
            "id": current_ai_run_ref.id,
            "userId": current_user_id,
            "status": "completed",
            "ownerMarker": f"current-ai-run-{run_id}",
        }
    )
    other_ai_run_ref.set(
        {
            "id": other_ai_run_ref.id,
            "userId": other_user_id,
            "status": "completed",
            "ownerMarker": f"other-ai-run-{run_id}",
        }
    )
    current_event_ref.set(
        {
            "eventId": current_event_ref.id,
            "name": "meal_logged",
            "userHash": current_user_hash,
            "ownerMarker": f"current-telemetry-{run_id}",
        }
    )
    other_event_ref.set(
        {
            "eventId": other_event_ref.id,
            "name": "meal_logged",
            "userHash": other_user_hash,
            "ownerMarker": f"other-telemetry-{run_id}",
        }
    )
    anonymous_event_ref.set(
        {
            "eventId": anonymous_event_ref.id,
            "name": "meal_logged",
            "anonymousId": f"anon-{run_id}",
            "userId": None,
            "userHash": None,
            "expiresAt": "2026-04-17T12:00:00Z",
        }
    )
    provider_payload_ref.set(
        {
            "userId": current_user_id,
            "rawPrompt": f"secret-provider-prompt-{run_id}",
            "rawResponse": f"secret-provider-response-{run_id}",
            "providerMessages": [f"secret-provider-message-{run_id}"],
            "fullPayload": f"secret-provider-full-payload-{run_id}",
        }
    )
    log_ref.set(
        {
            "userId": current_user_id,
            "message": f"secret-infra-log-{run_id}",
            "stack": f"secret-log-provider-stack-{run_id}",
        }
    )
    sentry_ref.set(
        {
            "userId": current_user_id,
            "event": f"secret-sentry-event-{run_id}",
        }
    )
    secret_ref.set(
        {
            "userId": current_user_id,
            "apiKey": f"secret-api-key-{run_id}",
        }
    )

    try:
        export = _export_payload(
            await user_account_service.get_user_export_data(current_user_id)
        )

        assert set(export) == {
            "profile",
            "meals",
            "myMeals",
            "chatMessages",
            "chatMemory",
            "aiRuns",
            "notifications",
            "notificationPrefs",
            "feedback",
            "mealMutationDedupe",
            "billing",
            "aiCredits",
            "aiCreditTransactions",
            "aiCreditIdempotency",
            "badges",
            "streak",
            "reminderDailyStats",
            "telemetryEvents",
        }
        assert export["profile"] == {
            "uid": current_user_id,
            "username": f"current-{run_id}",
            "profileMarker": f"current-profile-{run_id}",
            "avatarRef": {
                "storagePath": f"avatars/{current_user_id}/avatar.{run_id}.jpg"
            },
        }
        assert _ids(export["meals"]) == {f"meal-current-{run_id}"}
        assert _ids(export["myMeals"]) == {f"saved-current-{run_id}"}
        assert _ids(export["chatMessages"]) == {f"message-current-{run_id}"}
        assert _ids(export["chatMemory"]) == {f"memory-current-{run_id}"}
        assert _ids(export["aiRuns"]) == {current_ai_run_ref.id}
        assert _ids(export["notifications"]) == {f"notification-current-{run_id}"}
        assert export["notificationPrefs"] == {
            "smartRemindersEnabled": True,
            "motivationEnabled": True,
            "statsEnabled": False,
            "ownerMarker": f"current-notification-prefs-{run_id}",
        }
        assert _ids(export["feedback"]) == {f"feedback-current-{run_id}"}
        assert _ids(export["mealMutationDedupe"]) == {f"mutation-current-{run_id}"}
        assert _ids(export["billing"]) == {"main", "annual"}
        assert _ids(export["aiCredits"]) == {"current", "renewal"}
        assert _ids(export["aiCreditTransactions"]) == {f"tx-current-{run_id}"}
        assert _ids(export["aiCreditIdempotency"]) == {f"idem-current-{run_id}"}
        assert _ids(export["badges"]) == {"streak_7"}
        assert _ids(export["streak"]) == {"main"}
        assert _ids(export["reminderDailyStats"]) == {f"2026-03-03-{run_id}"}
        assert _ids(export["telemetryEvents"]) == {current_event_ref.id}
        assert {item["billingId"] for item in export["aiCredits"]} == {
            "main",
            "annual",
        }
        assert export["aiCreditTransactions"][0]["billingId"] == "main"
        assert export["aiCreditIdempotency"][0]["billingId"] == "main"

        assert export["profile"]["avatarRef"]["storagePath"] == (
            f"avatars/{current_user_id}/avatar.{run_id}.jpg"
        )
        assert export["meals"][0]["imageRef"]["storagePath"] == (
            f"meals/{current_user_id}/meal-{run_id}.jpg"
        )
        assert export["myMeals"][0]["imageRef"]["storagePath"] == (
            f"mealTemplates/{current_user_id}/saved-{run_id}.jpg"
        )
        assert export["feedback"][0]["attachmentRef"]["storagePath"] == (
            f"feedback/{current_user_id}/{run_id}/ticket.txt"
        )

        serialized_export = json.dumps(export, sort_keys=True)
        for forbidden in (
            f"other-profile-{run_id}",
            f"other-meal-{run_id}",
            f"other-saved-meal-{run_id}",
            f"other-chat-message-{run_id}",
            f"other-chat-memory-{run_id}",
            f"other-ai-run-{run_id}",
            f"other-notification-{run_id}",
            f"other-notification-prefs-{run_id}",
            f"other-feedback-{run_id}",
            f"other-meal-mutation-{run_id}",
            f"other-reminder-daily-stats-{run_id}",
            f"2026-03-03:breakfast:other-reminder-{run_id}",
            f"other-billing-main-{run_id}",
            f"other-ai-credit-main-{run_id}",
            f"other-ai-credit-transaction-{run_id}",
            f"other-ai-credit-idempotency-{run_id}",
            f"other-badge-{run_id}",
            f"other-streak-{run_id}",
            f"other-telemetry-{run_id}",
            f"anon-{run_id}",
            f"secret-provider-prompt-{run_id}",
            f"secret-provider-response-{run_id}",
            f"secret-provider-message-{run_id}",
            f"secret-provider-full-payload-{run_id}",
            f"secret-infra-log-{run_id}",
            f"secret-log-provider-stack-{run_id}",
            f"secret-sentry-event-{run_id}",
            f"secret-api-key-{run_id}",
        ):
            assert forbidden not in serialized_export

        bucket.blob.assert_not_called()
        bucket.list_blobs.assert_not_called()
    finally:
        for document_ref in reversed(seeded_refs):
            document_ref.delete()


async def test_delete_account_data_scopes_user_hash_telemetry_events(
    mocker: MockerFixture,
) -> None:
    client = _emulator_client()
    run_id = uuid4().hex
    current_user_id = f"ch-07-001-delete-current-{run_id}"
    other_user_id = f"ch-07-001-delete-other-{run_id}"
    current_user_hash = telemetry_service.build_user_hash(current_user_id)
    other_user_hash = telemetry_service.build_user_hash(other_user_id)
    current_user_ref = client.collection(USERS_COLLECTION).document(current_user_id)
    other_user_ref = client.collection(USERS_COLLECTION).document(other_user_id)
    current_event_ref = client.collection(telemetry_service.COLLECTION_NAME).document(
        f"telemetry-current-delete-{run_id}"
    )
    other_event_ref = client.collection(telemetry_service.COLLECTION_NAME).document(
        f"telemetry-other-delete-{run_id}"
    )
    anonymous_event_ref = client.collection(telemetry_service.COLLECTION_NAME).document(
        f"telemetry-anon-delete-{run_id}"
    )
    current_reminder_stats_ref = current_user_ref.collection(
        DAILY_STATS_SUBCOLLECTION
    ).document(f"2026-03-03-{run_id}")
    other_reminder_stats_ref = other_user_ref.collection(
        DAILY_STATS_SUBCOLLECTION
    ).document(f"2026-03-03-{run_id}")
    seeded_refs: list[firestore.DocumentReference] = [
        current_user_ref,
        other_user_ref,
        current_reminder_stats_ref,
        other_reminder_stats_ref,
        current_event_ref,
        other_event_ref,
        anonymous_event_ref,
    ]

    bucket = mocker.Mock()
    bucket.list_blobs.return_value = []

    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    mocker.patch("app.services.user_account_service.get_storage_bucket", return_value=bucket)

    current_user_ref.set({"uid": current_user_id, "username": f"current-{run_id}"})
    other_user_ref.set({"uid": other_user_id, "username": f"other-{run_id}"})
    current_reminder_stats_ref.set(
        {
            "sendCount": 2,
            "emittedDecisionKeys": [
                f"2026-03-03:breakfast:current-reminder-delete-{run_id}"
            ],
            "ownerMarker": f"current-reminder-daily-stats-delete-{run_id}",
        }
    )
    other_reminder_stats_ref.set(
        {
            "sendCount": 9,
            "emittedDecisionKeys": [
                f"2026-03-03:breakfast:other-reminder-delete-{run_id}"
            ],
            "ownerMarker": f"other-reminder-daily-stats-delete-{run_id}",
        }
    )
    current_event_ref.set(
        {
            "eventId": current_event_ref.id,
            "name": "meal_logged",
            "userHash": current_user_hash,
            "ownerMarker": f"current-telemetry-delete-{run_id}",
        }
    )
    other_event_ref.set(
        {
            "eventId": other_event_ref.id,
            "name": "meal_logged",
            "userHash": other_user_hash,
            "ownerMarker": f"other-telemetry-delete-{run_id}",
        }
    )
    anonymous_event_ref.set(
        {
            "eventId": anonymous_event_ref.id,
            "name": "meal_logged",
            "anonymousId": f"anon-{run_id}",
            "userId": None,
            "userHash": None,
            "expiresAt": "2026-04-17T12:00:00Z",
        }
    )

    try:
        await user_account_service.delete_account_data(current_user_id)

        assert current_reminder_stats_ref.get().exists is False
        other_reminder_snapshot = other_reminder_stats_ref.get()
        assert other_reminder_snapshot.exists is True
        other_reminder_payload = other_reminder_snapshot.to_dict() or {}
        assert other_reminder_payload["ownerMarker"] == (
            f"other-reminder-daily-stats-delete-{run_id}"
        )
        assert current_event_ref.get().exists is False
        assert other_event_ref.get().exists is True
        anonymous_snapshot = anonymous_event_ref.get()
        assert anonymous_snapshot.exists is True
        anonymous_payload = anonymous_snapshot.to_dict() or {}
        assert anonymous_payload["userId"] is None
        assert anonymous_payload["userHash"] is None
        assert anonymous_payload["anonymousId"] == f"anon-{run_id}"
        assert anonymous_payload["expiresAt"] == "2026-04-17T12:00:00Z"
        bucket.list_blobs.assert_any_call(prefix=f"avatars/{current_user_id}/")
        bucket.list_blobs.assert_any_call(prefix=f"meals/{current_user_id}/")
        bucket.list_blobs.assert_any_call(prefix=f"mealTemplates/{current_user_id}/")
    finally:
        for document_ref in reversed(seeded_refs):
            document_ref.delete()
