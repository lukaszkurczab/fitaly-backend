"""Firestore emulator evidence for account export/delete telemetry isolation."""

from __future__ import annotations

import os
from typing import Any, cast
from uuid import uuid4

import pytest
from google.cloud import firestore
from pytest_mock import MockerFixture

from app.services import telemetry_service, user_account_service


pytestmark = pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="Firestore emulator is not configured.",
)


def _emulator_client() -> firestore.Client:
    project_id = os.getenv("FIREBASE_PROJECT_ID") or "demo-fitaly-local"
    database_id = os.getenv("FIRESTORE_DATABASE_ID") or "(default)"
    client_class = cast(Any, firestore.Client)
    return cast(firestore.Client, client_class(project=project_id, database=database_id))


async def test_account_export_and_delete_scope_telemetry_events_by_user_hash(
    mocker: MockerFixture,
) -> None:
    client = _emulator_client()
    run_id = uuid4().hex
    current_user_id = f"ch-07-001b-current-{run_id}"
    other_user_id = f"ch-07-001b-other-{run_id}"
    current_user_hash = telemetry_service.build_user_hash(current_user_id)
    other_user_hash = telemetry_service.build_user_hash(other_user_id)

    current_event_ref = client.collection("telemetry_events").document(
        f"telemetry-current-{run_id}"
    )
    other_event_ref = client.collection("telemetry_events").document(
        f"telemetry-other-{run_id}"
    )
    anonymous_event_ref = client.collection("telemetry_events").document(
        f"telemetry-anon-{run_id}"
    )
    bucket = mocker.Mock()
    bucket.list_blobs.return_value = []

    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    mocker.patch("app.services.user_account_service.get_storage_bucket", return_value=bucket)

    current_event_ref.set(
        {
            "eventId": current_event_ref.id,
            "name": "meal_logged",
            "userHash": current_user_hash,
        }
    )
    other_event_ref.set(
        {
            "eventId": other_event_ref.id,
            "name": "meal_logged",
            "userHash": other_user_hash,
        }
    )
    anonymous_event_ref.set(
        {
            "eventId": anonymous_event_ref.id,
            "name": "meal_logged",
        }
    )

    try:
        (
            _profile,
            _meals,
            _my_meals,
            _chat_messages,
            _chat_memory,
            _ai_runs,
            _notifications,
            _notification_prefs,
            _feedback,
            _meal_mutation_dedupe,
            telemetry_events,
        ) = await user_account_service.get_user_export_data(current_user_id)

        assert telemetry_events == [
            {
                "eventId": current_event_ref.id,
                "name": "meal_logged",
                "userHash": current_user_hash,
                "id": current_event_ref.id,
            }
        ]

        await user_account_service.delete_account_data(current_user_id)

        assert current_event_ref.get().exists is False
        assert other_event_ref.get().exists is True
        assert anonymous_event_ref.get().exists is True
    finally:
        current_event_ref.delete()
        other_event_ref.delete()
        anonymous_event_ref.delete()
