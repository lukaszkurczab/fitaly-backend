from typing import Any

from pytest import MonkeyPatch
from pytest_mock import MockerFixture

from app.services import meal_effect_outbox_service


class FakeTransaction:
    def __init__(self) -> None:
        self._id = b"transaction-id"
        self._max_attempts = 1
        self._read_only = False
        self.set_calls: list[tuple[object, dict[str, object], bool | None]] = []

    def _begin(self, *args: Any, **kwargs: Any) -> None:
        return None

    def _commit(self) -> list[object]:
        return []

    def _rollback(self) -> None:
        return None

    def _clean_up(self) -> None:
        return None

    def set(
        self,
        document_ref: object,
        data: dict[str, object],
        merge: bool | None = None,
    ) -> None:
        self.set_calls.append((document_ref, data, merge))


def _snapshot(
    mocker: MockerFixture,
    *,
    doc_id: str,
    data: dict[str, Any],
):
    snapshot = mocker.Mock()
    snapshot.exists = True
    snapshot.id = doc_id
    snapshot.reference = mocker.Mock()
    snapshot.to_dict.return_value = data
    return snapshot


def _client_with_transaction(mocker: MockerFixture) -> tuple[Any, FakeTransaction]:
    client = mocker.Mock()
    transaction = FakeTransaction()
    client.transaction.return_value = transaction
    return client, transaction


def test_mark_failed_sets_backoff_before_dead_letter(
    mocker: MockerFixture,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        meal_effect_outbox_service,
        "_now_iso",
        lambda: "2026-03-03T12:00:00.000Z",
    )
    client, transaction = _client_with_transaction(mocker)
    event_ref = mocker.Mock()
    event_ref.get.return_value = _snapshot(
        mocker,
        doc_id="event-1",
        data={
            "status": meal_effect_outbox_service.STATUS_PENDING,
            "attemptCount": 0,
            "leaseToken": "lease-token",
            "leaseExpiresAt": "2026-03-03T12:05:00.000Z",
        },
    )

    meal_effect_outbox_service.mark_failed(
        client,
        event_ref,
        {"attemptCount": 0, "leaseToken": "lease-token"},
        RuntimeError("streak failed"),
    )

    update = transaction.set_calls[0][1]
    assert update["status"] == meal_effect_outbox_service.STATUS_PENDING
    assert update["attemptCount"] == 1
    assert update["nextAttemptAt"] == "2026-03-03T12:01:00.000Z"
    assert update["lastErrorCode"] == "RuntimeError"
    assert update["lastErrorMessage"] == "streak failed"
    assert update["deadLetterAt"] is None
    assert update["leaseToken"] is None
    assert transaction.set_calls == [(event_ref, update, True)]


def test_mark_failed_dead_letters_at_attempt_threshold(
    mocker: MockerFixture,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        meal_effect_outbox_service,
        "_now_iso",
        lambda: "2026-03-03T12:00:00.000Z",
    )
    client, transaction = _client_with_transaction(mocker)
    event_ref = mocker.Mock()
    event_ref.get.return_value = _snapshot(
        mocker,
        doc_id="event-1",
        data={
            "status": meal_effect_outbox_service.STATUS_PENDING,
            "attemptCount": meal_effect_outbox_service.MAX_ATTEMPT_COUNT - 1,
            "leaseToken": "lease-token",
            "leaseExpiresAt": "2026-03-03T12:05:00.000Z",
        },
    )

    meal_effect_outbox_service.mark_failed(
        client,
        event_ref,
        {
            "attemptCount": meal_effect_outbox_service.MAX_ATTEMPT_COUNT - 1,
            "leaseToken": "lease-token",
        },
        RuntimeError("still broken"),
    )

    update = transaction.set_calls[0][1]
    assert update["status"] == meal_effect_outbox_service.STATUS_DEAD_LETTER
    assert update["attemptCount"] == meal_effect_outbox_service.MAX_ATTEMPT_COUNT
    assert update["nextAttemptAt"] is None
    assert update["lastErrorCode"] == "RuntimeError"
    assert update["lastErrorMessage"] == "still broken"
    assert update["deadLetterAt"] == "2026-03-03T12:00:00.000Z"


def test_claim_pending_event_sets_processing_lease(
    mocker: MockerFixture,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        meal_effect_outbox_service,
        "_now_iso",
        lambda: "2026-03-03T12:00:00.000Z",
    )
    monkeypatch.setattr(
        meal_effect_outbox_service,
        "uuid4",
        lambda: mocker.Mock(hex="lease-token"),
    )
    client, transaction = _client_with_transaction(mocker)
    event_ref = mocker.Mock()
    event_ref.get.return_value = _snapshot(
        mocker,
        doc_id="event-1",
        data={
            "status": meal_effect_outbox_service.STATUS_PENDING,
            "nextAttemptAt": "2026-03-03T11:59:00.000Z",
        },
    )

    claimed_event = meal_effect_outbox_service.claim_pending_event(
        client,
        event_ref,
        lease_owner="unit-test",
    )

    assert claimed_event is not None
    assert claimed_event["leaseToken"] == "lease-token"
    assert claimed_event["leaseOwner"] == "unit-test"
    assert claimed_event["leaseExpiresAt"] == "2026-03-03T12:05:00.000Z"
    assert transaction.set_calls == [
        (
            event_ref,
            {
                "leaseToken": "lease-token",
                "leaseOwner": "unit-test",
                "leaseExpiresAt": "2026-03-03T12:05:00.000Z",
                "leasedAt": "2026-03-03T12:00:00.000Z",
                "nextAttemptAt": "2026-03-03T12:05:00.000Z",
                "updatedAt": "2026-03-03T12:00:00.000Z",
            },
            True,
        )
    ]


def test_claim_pending_event_skips_active_lease(
    mocker: MockerFixture,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        meal_effect_outbox_service,
        "_now_iso",
        lambda: "2026-03-03T12:00:00.000Z",
    )
    client, transaction = _client_with_transaction(mocker)
    event_ref = mocker.Mock()
    event_ref.get.return_value = _snapshot(
        mocker,
        doc_id="event-1",
        data={
            "status": meal_effect_outbox_service.STATUS_PENDING,
            "nextAttemptAt": "2026-03-03T11:59:00.000Z",
            "leaseToken": "other-lease",
            "leaseExpiresAt": "2026-03-03T12:01:00.000Z",
        },
    )

    claimed_event = meal_effect_outbox_service.claim_pending_event(client, event_ref)

    assert claimed_event is None
    assert transaction.set_calls == []


def test_mark_succeeded_skips_when_lease_token_changed(
    mocker: MockerFixture,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        meal_effect_outbox_service,
        "_now_iso",
        lambda: "2026-03-03T12:00:00.000Z",
    )
    client, transaction = _client_with_transaction(mocker)
    event_ref = mocker.Mock()
    event_ref.get.return_value = _snapshot(
        mocker,
        doc_id="event-1",
        data={
            "status": meal_effect_outbox_service.STATUS_PENDING,
            "attemptCount": 0,
            "leaseToken": "new-lease",
            "leaseExpiresAt": "2026-03-03T12:05:00.000Z",
        },
    )

    updated = meal_effect_outbox_service.mark_succeeded(
        client,
        event_ref,
        {"attemptCount": 0, "leaseToken": "old-lease"},
    )

    assert updated is False
    assert transaction.set_calls == []


def test_list_pending_events_returns_only_due_pending_events(
    mocker: MockerFixture,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        meal_effect_outbox_service,
        "_now_iso",
        lambda: "2026-03-03T12:00:00.000Z",
    )
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    outbox_collection = mocker.Mock()
    query = mocker.Mock()
    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = outbox_collection
    outbox_collection.where.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.stream.return_value = [
        _snapshot(
            mocker,
            doc_id="due",
            data={
                "status": meal_effect_outbox_service.STATUS_PENDING,
                "nextAttemptAt": "2026-03-03T11:59:00.000Z",
            },
        ),
        _snapshot(
            mocker,
            doc_id="missing-next-at",
            data={"status": meal_effect_outbox_service.STATUS_PENDING},
        ),
        _snapshot(
            mocker,
            doc_id="future",
            data={
                "status": meal_effect_outbox_service.STATUS_PENDING,
                "nextAttemptAt": "2026-03-03T12:05:00.000Z",
            },
        ),
        _snapshot(
            mocker,
            doc_id="dead",
            data={"status": meal_effect_outbox_service.STATUS_DEAD_LETTER},
        ),
    ]

    events = meal_effect_outbox_service.list_pending_events(
        client,
        "user-1",
        limit_count=100,
    )

    assert [event["eventId"] for _ref, event in events] == [
        "due",
        "missing-next-at",
    ]
    query.order_by.assert_called_once_with("nextAttemptAt")
    query.limit.assert_called_once_with(meal_effect_outbox_service.MAX_RECONCILIATION_EVENTS)
