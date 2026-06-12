import asyncio
import hashlib
from io import BytesIO
from typing import Any
from unittest.mock import ANY

from fastapi import UploadFile
import pytest
from google.api_core.exceptions import GoogleAPICallError
from pytest_mock import MockerFixture
from starlette.datastructures import Headers

from app.core.exceptions import FirestoreServiceError
from app.services.meal_storage import MAX_UPLOAD_BYTES
from app.services import user_account_service
from app.services import telemetry_service
from app.services.user_account_service import (
    EmailValidationError,
    OnboardingUsernameUnavailableError,
    OnboardingValidationError,
    UserProfileMutationDedupeConflictError,
    UserProfileValidationError,
)


AVATAR_BYTES = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"avatar-unit-test"
    b"\xff\xd9"
)


def _avatar_upload(content: bytes = AVATAR_BYTES, *, content_type: str = "image/jpeg") -> UploadFile:
    return UploadFile(
        BytesIO(content),
        filename="avatar.jpg",
        headers=Headers({"content-type": content_type}),
    )


class FakeTransaction:
    def __init__(self) -> None:
        self._id = b"transaction-id"
        self._max_attempts = 1
        self._read_only = False
        self.set_calls: list[tuple[object, dict[str, Any], bool | None]] = []
        self.delete_calls: list[object] = []

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
        data: dict[str, Any],
        merge: bool | None = None,
    ) -> None:
        self.set_calls.append((document_ref, data, merge))

    def delete(self, document_ref: object) -> None:
        self.delete_calls.append(document_ref)


class FakeBatch:
    def __init__(self) -> None:
        self.deleted_refs: list[object] = []
        self.commit_count = 0

    def delete(self, document_ref: object) -> None:
        self.deleted_refs.append(document_ref)

    def commit(self) -> None:
        self.commit_count += 1


def _build_client(mocker: MockerFixture):
    client = mocker.Mock()
    users_collection_ref = mocker.Mock()
    usernames_collection_ref = mocker.Mock()
    user_ref = mocker.Mock()
    username_ref = mocker.Mock()
    mutation_collection_ref = mocker.Mock()
    mutation_ref = mocker.Mock()

    def collection_side_effect(name: str):
        if name == "users":
            return users_collection_ref
        if name == "usernames":
            return usernames_collection_ref
        raise AssertionError(f"Unexpected collection {name}")

    client.collection.side_effect = collection_side_effect
    client.batch.return_value = mocker.Mock()
    client.transaction.return_value = FakeTransaction()
    users_collection_ref.document.return_value = user_ref
    usernames_collection_ref.document.return_value = username_ref
    user_ref.collection.return_value = mutation_collection_ref
    mutation_collection_ref.document.return_value = mutation_ref
    mutation_ref.get.return_value = _build_snapshot(mocker, exists=False)

    return client, users_collection_ref, usernames_collection_ref, user_ref, username_ref


def _build_snapshot(
    mocker: MockerFixture,
    *,
    exists: bool,
    data: dict[str, object] | None = None,
):
    snapshot = mocker.Mock()
    snapshot.exists = exists
    snapshot.to_dict.return_value = data or {}
    return snapshot


def _transaction_set_payload(
    transaction: FakeTransaction,
    document_ref: object,
) -> dict[str, Any]:
    for ref, data, _merge in transaction.set_calls:
        if ref is document_ref:
            return data
    raise AssertionError("Expected transaction.set call was not recorded")


def test_set_email_pending_updates_user_document(mocker: MockerFixture) -> None:
    client, users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    normalized_email = asyncio.run(
        user_account_service.set_email_pending("user-1", " new@example.com ")
    )

    users_collection_ref.document.assert_any_call("user-1")
    user_ref.set.assert_called_once_with({"emailPending": "new@example.com"}, merge=True)
    assert normalized_email == "new@example.com"


def test_set_email_pending_raises_for_invalid_email() -> None:
    with pytest.raises(EmailValidationError):
        asyncio.run(user_account_service.set_email_pending("user-1", "bad"))


def test_set_email_pending_wraps_firestore_errors(mocker: MockerFixture) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.set.side_effect = GoogleAPICallError("boom")
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    with pytest.raises(FirestoreServiceError):
        asyncio.run(user_account_service.set_email_pending("user-1", "new@example.com"))


def test_delete_account_data_deletes_subcollections_username_and_user_doc(
    mocker: MockerFixture,
) -> None:
    client, users_collection_ref, usernames_collection_ref, user_ref, username_ref = (
        _build_client(mocker)
    )
    telemetry_collection_ref = mocker.Mock()
    telemetry_query = mocker.Mock()
    telemetry_doc = mocker.Mock()
    telemetry_doc_ref = mocker.Mock()
    telemetry_doc.reference = telemetry_doc_ref
    meals_collection_ref = mocker.Mock()
    my_meals_collection_ref = mocker.Mock()
    legacy_chat_collection_ref = mocker.Mock()
    notifications_collection_ref = mocker.Mock()
    prefs_collection_ref = mocker.Mock()
    notif_meta_collection_ref = mocker.Mock()
    feedback_collection_ref = mocker.Mock()
    meal_mutation_dedupe_collection_ref = mocker.Mock()
    badges_collection_ref = mocker.Mock()
    streak_collection_ref = mocker.Mock()
    billing_collection_ref = mocker.Mock()
    chat_threads_collection_ref = mocker.Mock()
    ai_runs_collection_ref = mocker.Mock()
    ai_runs_query = mocker.Mock()
    meals_doc_1 = mocker.Mock()
    meals_doc_2 = mocker.Mock()
    my_meal_doc = mocker.Mock()
    legacy_chat_doc = mocker.Mock()
    notification_doc = mocker.Mock()
    prefs_doc = mocker.Mock()
    notif_meta_doc = mocker.Mock()
    feedback_doc = mocker.Mock()
    feedback_doc.to_dict.return_value = {}
    meal_mutation_dedupe_doc = mocker.Mock()
    badge_doc = mocker.Mock()
    streak_doc = mocker.Mock()
    billing_doc = mocker.Mock()
    billing_doc.id = "main"
    main_billing_ref = mocker.Mock()
    billing_doc.reference = main_billing_ref
    ai_credits_collection_ref = mocker.Mock()
    ai_credit_transactions_collection_ref = mocker.Mock()
    ai_credit_idempotency_collection_ref = mocker.Mock()
    ai_credit_doc = mocker.Mock()
    ai_credit_transaction_doc = mocker.Mock()
    ai_credit_idempotency_doc = mocker.Mock()
    chat_thread_doc = mocker.Mock()
    chat_thread_memory_collection_ref = mocker.Mock()
    chat_thread_memory_doc = mocker.Mock()
    chat_thread_messages_collection_ref = mocker.Mock()
    chat_thread_message_doc = mocker.Mock()
    ai_run_doc = mocker.Mock()

    def top_level_collection_side_effect(name: str):
        if name == "users":
            return users_collection_ref
        if name == "usernames":
            return usernames_collection_ref
        if name == "telemetry_events":
            return telemetry_collection_ref
        if name == "ai_runs":
            return ai_runs_collection_ref
        raise AssertionError(f"Unexpected collection {name}")

    client.collection.side_effect = top_level_collection_side_effect
    telemetry_collection_ref.where.return_value = telemetry_query
    telemetry_query.stream.return_value = [telemetry_doc]

    def collection_side_effect(name: str):
        if name == "meals":
            return meals_collection_ref
        if name == "mealTemplates":
            return my_meals_collection_ref
        if name == "chat_messages":
            return legacy_chat_collection_ref
        if name == "notifications":
            return notifications_collection_ref
        if name == "prefs":
            return prefs_collection_ref
        if name == "notif_meta":
            return notif_meta_collection_ref
        if name == "feedback":
            return feedback_collection_ref
        if name == "mealMutationDedupe":
            return meal_mutation_dedupe_collection_ref
        if name == "badges":
            return badges_collection_ref
        if name == "streak":
            return streak_collection_ref
        if name == "billing":
            return billing_collection_ref
        if name == "chat_threads":
            return chat_threads_collection_ref
        raise AssertionError(f"Unexpected subcollection {name}")

    user_ref.collection.side_effect = collection_side_effect
    meals_collection_ref.stream.return_value = [meals_doc_1, meals_doc_2]
    my_meals_collection_ref.stream.return_value = [my_meal_doc]
    legacy_chat_collection_ref.stream.return_value = [legacy_chat_doc]
    notifications_collection_ref.stream.return_value = [notification_doc]
    prefs_collection_ref.stream.return_value = [prefs_doc]
    notif_meta_collection_ref.stream.return_value = [notif_meta_doc]
    feedback_collection_ref.stream.return_value = [feedback_doc]
    meal_mutation_dedupe_collection_ref.stream.return_value = [meal_mutation_dedupe_doc]
    badges_collection_ref.stream.return_value = [badge_doc]
    streak_collection_ref.stream.return_value = [streak_doc]
    billing_collection_ref.stream.return_value = [billing_doc]
    billing_collection_ref.document.return_value = main_billing_ref
    main_billing_ref.get.return_value = _build_snapshot(mocker, exists=True)
    chat_threads_collection_ref.stream.return_value = [chat_thread_doc]
    ai_runs_collection_ref.where.return_value = ai_runs_query
    ai_runs_query.stream.return_value = [ai_run_doc]

    def billing_child_collection_side_effect(name: str):
        if name == "aiCredits":
            return ai_credits_collection_ref
        if name == "aiCreditTransactions":
            return ai_credit_transactions_collection_ref
        if name == "aiCreditIdempotency":
            return ai_credit_idempotency_collection_ref
        raise AssertionError(f"Unexpected billing subcollection {name}")

    main_billing_ref.collection.side_effect = billing_child_collection_side_effect
    ai_credits_collection_ref.stream.return_value = [ai_credit_doc]
    ai_credit_transactions_collection_ref.stream.return_value = [ai_credit_transaction_doc]
    ai_credit_idempotency_collection_ref.stream.return_value = [ai_credit_idempotency_doc]

    def chat_thread_child_collection_side_effect(name: str):
        if name == "memory":
            return chat_thread_memory_collection_ref
        if name == "messages":
            return chat_thread_messages_collection_ref
        raise AssertionError(f"Unexpected chat thread subcollection {name}")

    chat_thread_doc.reference.collection.side_effect = chat_thread_child_collection_side_effect
    chat_thread_memory_collection_ref.stream.return_value = [chat_thread_memory_doc]
    chat_thread_messages_collection_ref.stream.return_value = [chat_thread_message_doc]
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"username": "neo"},
    )
    batches: list[FakeBatch] = []

    def batch_factory() -> FakeBatch:
        batch = FakeBatch()
        batches.append(batch)
        return batch

    client.batch.side_effect = batch_factory
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    bucket = mocker.Mock()
    avatar_blob = mocker.Mock()
    meal_blob = mocker.Mock()
    my_meal_blob = mocker.Mock()
    bucket.list_blobs.side_effect = [
        [avatar_blob],
        [meal_blob],
        [my_meal_blob],
    ]
    mocker.patch("app.services.user_account_service.get_storage_bucket", return_value=bucket)

    asyncio.run(user_account_service.delete_account_data("user-1"))

    deleted_refs = [ref for batch in batches for ref in batch.deleted_refs]
    assert meals_doc_1.reference in deleted_refs
    assert meals_doc_2.reference in deleted_refs
    assert my_meal_doc.reference in deleted_refs
    assert legacy_chat_doc.reference in deleted_refs
    assert notification_doc.reference in deleted_refs
    assert prefs_doc.reference in deleted_refs
    assert notif_meta_doc.reference in deleted_refs
    assert feedback_doc.reference in deleted_refs
    assert meal_mutation_dedupe_doc.reference in deleted_refs
    assert badge_doc.reference in deleted_refs
    assert streak_doc.reference in deleted_refs
    assert ai_credit_doc.reference in deleted_refs
    assert ai_credit_transaction_doc.reference in deleted_refs
    assert ai_credit_idempotency_doc.reference in deleted_refs
    billing_collection_ref.document.assert_called_once_with("main")
    main_billing_ref.delete.assert_called_once_with()
    assert ai_run_doc.reference in deleted_refs
    assert chat_thread_memory_doc.reference in deleted_refs
    assert chat_thread_message_doc.reference in deleted_refs
    assert chat_thread_doc.reference in deleted_refs
    assert all(batch.commit_count == 1 for batch in batches)
    ai_runs_collection_ref.where.assert_called_once()
    ai_runs_filter = ai_runs_collection_ref.where.call_args.kwargs["filter"]
    assert ai_runs_filter.field_path == "userId"
    assert ai_runs_filter.op_string == "=="
    assert ai_runs_filter.value == "user-1"
    telemetry_collection_ref.where.assert_called_once_with(
        filter=ANY,
    )
    telemetry_filter = telemetry_collection_ref.where.call_args.kwargs["filter"]
    assert telemetry_filter.field_path == "userHash"
    assert telemetry_filter.op_string == "=="
    assert telemetry_filter.value == telemetry_service.build_user_hash("user-1")
    assert telemetry_doc.reference in deleted_refs
    usernames_collection_ref.document.assert_called_once_with("neo")
    username_ref.delete.assert_called_once_with()
    user_ref.delete.assert_called_once_with()
    bucket.list_blobs.assert_any_call(prefix="avatars/user-1/")
    bucket.list_blobs.assert_any_call(prefix="meals/user-1/")
    bucket.list_blobs.assert_any_call(prefix="mealTemplates/user-1/")
    avatar_blob.delete.assert_called_once_with()
    meal_blob.delete.assert_called_once_with()
    my_meal_blob.delete.assert_called_once_with()


def test_delete_feedback_attachments_uses_canonical_attachment_ref(
    mocker: MockerFixture,
) -> None:
    feedback_doc = mocker.Mock()
    feedback_doc.id = "feedback-1"
    feedback_doc.to_dict.return_value = {
        "attachmentRef": {
            "storagePath": "feedback/user-1/feedback-1/feedback.jpg",
        },
    }
    bucket = mocker.Mock()
    blob = mocker.Mock()
    bucket.blob.return_value = blob
    mocker.patch("app.services.user_account_service.get_storage_bucket", return_value=bucket)

    user_account_service._delete_feedback_attachments(
        feedback_documents=[feedback_doc],
        user_id="user-1",
    )

    bucket.blob.assert_called_once_with("feedback/user-1/feedback-1/feedback.jpg")
    blob.delete.assert_called_once_with()


def test_delete_feedback_attachments_handles_legacy_attachment_path(
    mocker: MockerFixture,
) -> None:
    feedback_doc = mocker.Mock()
    feedback_doc.id = "feedback-1"
    feedback_doc.to_dict.return_value = {
        "attachmentPath": "feedback/user-1/feedback-1/legacy.jpg",
    }
    bucket = mocker.Mock()
    blob = mocker.Mock()
    bucket.blob.return_value = blob
    mocker.patch("app.services.user_account_service.get_storage_bucket", return_value=bucket)

    user_account_service._delete_feedback_attachments(
        feedback_documents=[feedback_doc],
        user_id="user-1",
    )

    bucket.blob.assert_called_once_with("feedback/user-1/feedback-1/legacy.jpg")
    blob.delete.assert_called_once_with()


@pytest.mark.parametrize(
    "payload",
    [
        {
            "attachmentRef": {
                "storagePath": "feedback/other-user/feedback-1/feedback.jpg",
            },
        },
        {
            "attachmentPath": "feedback/other-user/feedback-1/legacy.jpg",
        },
        {
            "attachmentRef": {
                "storagePath": "feedbacks/user-1/feedback-1/feedback.jpg",
            },
        },
        {
            "attachmentPath": "meals/user-1/meal-1/photo.jpg",
        },
        {
            "attachmentRef": {
                "storagePath": "",
            },
        },
        {
            "attachmentPath": "feedback/user-1/feedback-1",
        },
        {
            "attachmentRef": {
                "storagePath": "feedback/user-1/feedback-1/../feedback.jpg",
            },
        },
    ],
)
def test_delete_feedback_attachments_ignores_out_of_scope_paths(
    mocker: MockerFixture,
    payload: dict[str, object],
) -> None:
    feedback_doc = mocker.Mock()
    feedback_doc.id = "feedback-1"
    feedback_doc.to_dict.return_value = payload
    bucket = mocker.Mock()
    mocker.patch("app.services.user_account_service.get_storage_bucket", return_value=bucket)

    user_account_service._delete_feedback_attachments(
        feedback_documents=[feedback_doc],
        user_id="user-1",
    )

    bucket.blob.assert_not_called()


def test_delete_feedback_attachments_does_not_delete_duplicate_path_twice(
    mocker: MockerFixture,
) -> None:
    feedback_doc = mocker.Mock()
    feedback_doc.id = "feedback-1"
    feedback_doc.to_dict.return_value = {
        "attachmentRef": {
            "storagePath": "feedback/user-1/feedback-1/feedback.jpg",
        },
        "attachmentPath": "feedback/user-1/feedback-1/feedback.jpg",
    }
    bucket = mocker.Mock()
    blob = mocker.Mock()
    bucket.blob.return_value = blob
    mocker.patch("app.services.user_account_service.get_storage_bucket", return_value=bucket)

    user_account_service._delete_feedback_attachments(
        feedback_documents=[feedback_doc],
        user_id="user-1",
    )

    bucket.blob.assert_called_once_with("feedback/user-1/feedback-1/feedback.jpg")
    blob.delete.assert_called_once_with()


def test_delete_billing_data_deletes_main_children_when_parent_doc_is_missing(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    user_ref = mocker.Mock()
    billing_collection_ref = mocker.Mock()
    main_billing_ref = mocker.Mock()
    ai_credits_collection_ref = mocker.Mock()
    ai_credit_transactions_collection_ref = mocker.Mock()
    ai_credit_idempotency_collection_ref = mocker.Mock()
    ai_credit_doc = mocker.Mock()
    ai_credit_transaction_doc = mocker.Mock()
    ai_credit_idempotency_doc = mocker.Mock()
    batches: list[FakeBatch] = []

    def batch_factory() -> FakeBatch:
        batch = FakeBatch()
        batches.append(batch)
        return batch

    def user_collection_side_effect(name: str):
        if name == "billing":
            return billing_collection_ref
        raise AssertionError(f"Unexpected subcollection {name}")

    def billing_child_collection_side_effect(name: str):
        if name == "aiCredits":
            return ai_credits_collection_ref
        if name == "aiCreditTransactions":
            return ai_credit_transactions_collection_ref
        if name == "aiCreditIdempotency":
            return ai_credit_idempotency_collection_ref
        raise AssertionError(f"Unexpected billing subcollection {name}")

    client.batch.side_effect = batch_factory
    user_ref.collection.side_effect = user_collection_side_effect
    billing_collection_ref.document.return_value = main_billing_ref
    billing_collection_ref.stream.return_value = []
    main_billing_ref.collection.side_effect = billing_child_collection_side_effect
    main_billing_ref.get.return_value = _build_snapshot(mocker, exists=False)
    ai_credits_collection_ref.stream.return_value = [ai_credit_doc]
    ai_credit_transactions_collection_ref.stream.return_value = [ai_credit_transaction_doc]
    ai_credit_idempotency_collection_ref.stream.return_value = [ai_credit_idempotency_doc]

    user_account_service._delete_billing_data(client, user_ref)

    deleted_refs = [ref for batch in batches for ref in batch.deleted_refs]
    assert ai_credit_doc.reference in deleted_refs
    assert ai_credit_transaction_doc.reference in deleted_refs
    assert ai_credit_idempotency_doc.reference in deleted_refs
    assert all(batch.commit_count == 1 for batch in batches)
    billing_collection_ref.document.assert_called_once_with("main")
    main_billing_ref.delete.assert_not_called()


def test_delete_account_data_wraps_firestore_errors(mocker: MockerFixture) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.side_effect = GoogleAPICallError("boom")
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    with pytest.raises(FirestoreServiceError):
        asyncio.run(user_account_service.delete_account_data("user-1"))


def test_upload_avatar_persists_file_and_avatar_ref_metadata(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FIREBASE_STORAGE_EMULATOR_HOST", raising=False)
    client, users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    bucket = mocker.Mock()
    bucket.name = "bucket-name"
    blob = mocker.Mock()
    bucket.blob.return_value = blob
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    mocker.patch("app.services.user_account_service.get_storage_bucket", return_value=bucket)
    mocker.patch(
        "app.services.user_account_service.uuid4",
        return_value="download-token",
    )
    upload = _avatar_upload()
    expected_hash = hashlib.sha256(b"avatar-mutation-1").hexdigest()
    expected_path = f"avatars/user-1/avatar.{expected_hash}"

    avatar_url, synced_at, avatar_ref = asyncio.run(
        user_account_service.upload_avatar(
            "user-1",
            upload,
            client_mutation_id=" avatar-mutation-1 ",
        )
    )

    bucket.blob.assert_called_once_with(expected_path)
    blob.upload_from_file.assert_called_once_with(
        upload.file,
        content_type="image/jpeg",
    )
    blob.patch.assert_called_once_with()
    assert upload.file.closed is True
    users_collection_ref.document.assert_any_call("user-1")
    user_ref.set.assert_called_once_with(
        {
            "avatarRef": {"storagePath": expected_path},
            "avatarUrl": avatar_url,
            "avatarlastSyncedAt": synced_at,
            "avatarLocalPath": user_account_service.firestore.DELETE_FIELD,
        },
        merge=True,
    )
    assert f"avatars%2Fuser-1%2Favatar.{expected_hash}" in avatar_url
    assert "download-token" in avatar_url
    assert synced_at.endswith("Z")
    assert avatar_ref == {"storagePath": expected_path}


def test_upload_avatar_skips_blob_patch_under_storage_emulator(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIREBASE_STORAGE_EMULATOR_HOST", "127.0.0.1:9199")
    client, _users_collection_ref, _usernames_collection_ref, _user_ref, _username_ref = (
        _build_client(mocker)
    )
    bucket = mocker.Mock()
    bucket.name = "bucket-name"
    blob = mocker.Mock()
    bucket.blob.return_value = blob
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    mocker.patch("app.services.user_account_service.get_storage_bucket", return_value=bucket)
    mocker.patch(
        "app.services.user_account_service.uuid4",
        return_value="download-token",
    )
    upload = _avatar_upload()

    asyncio.run(
        user_account_service.upload_avatar(
            "user-1",
            upload,
            client_mutation_id="avatar-mutation-1",
        )
    )

    blob.upload_from_file.assert_called_once_with(
        upload.file,
        content_type="image/jpeg",
    )
    blob.patch.assert_not_called()
    assert upload.file.closed is True


def test_upload_avatar_rejects_blank_client_mutation_id(mocker: MockerFixture) -> None:
    upload = mocker.Mock()
    upload.file = mocker.Mock()

    with pytest.raises(ValueError, match="Missing clientMutationId"):
        asyncio.run(
            user_account_service.upload_avatar(
                "user-1",
                upload,
                client_mutation_id="   ",
            )
        )

    upload.file.close.assert_not_called()


def test_upload_avatar_rejects_invalid_declared_mime_with_image_bytes_before_storage_or_profile_write(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    bucket = mocker.Mock()
    bucket.name = "bucket-name"
    blob = mocker.Mock()
    bucket.blob.return_value = blob
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    mocker.patch("app.services.user_account_service.get_storage_bucket", return_value=bucket)
    upload = _avatar_upload(AVATAR_BYTES, content_type="text/plain")

    with pytest.raises(ValueError, match="Unsupported or unrecognized file type"):
        asyncio.run(
            user_account_service.upload_avatar(
                "user-1",
                upload,
                client_mutation_id="avatar-mutation-1",
            )
        )

    blob.upload_from_file.assert_not_called()
    blob.patch.assert_not_called()
    user_ref.set.assert_not_called()
    assert upload.file.closed is True


def test_upload_avatar_rejects_spoofed_image_bytes_before_storage_or_profile_write(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    bucket = mocker.Mock()
    bucket.name = "bucket-name"
    blob = mocker.Mock()
    bucket.blob.return_value = blob
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    mocker.patch("app.services.user_account_service.get_storage_bucket", return_value=bucket)
    upload = _avatar_upload(b"not an actual image", content_type="image/jpeg")

    with pytest.raises(ValueError, match="Unsupported or unrecognized file type"):
        asyncio.run(
            user_account_service.upload_avatar(
                "user-1",
                upload,
                client_mutation_id="avatar-mutation-1",
            )
        )

    blob.upload_from_file.assert_not_called()
    blob.patch.assert_not_called()
    user_ref.set.assert_not_called()
    assert upload.file.closed is True


def test_upload_avatar_rejects_oversized_file_before_storage_or_profile_write(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    bucket = mocker.Mock()
    bucket.name = "bucket-name"
    blob = mocker.Mock()
    bucket.blob.return_value = blob
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    mocker.patch("app.services.user_account_service.get_storage_bucket", return_value=bucket)
    upload = _avatar_upload(
        b"\xff\xd8\xff" + (b"x" * (MAX_UPLOAD_BYTES - 2)),
        content_type="image/jpeg",
    )

    with pytest.raises(ValueError, match="File exceeds maximum allowed size"):
        asyncio.run(
            user_account_service.upload_avatar(
                "user-1",
                upload,
                client_mutation_id="avatar-mutation-1",
            )
        )

    blob.upload_from_file.assert_not_called()
    blob.patch.assert_not_called()
    user_ref.set.assert_not_called()
    assert upload.file.closed is True


def test_upload_avatar_storage_failure_does_not_persist_avatar_metadata(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    bucket = mocker.Mock()
    bucket.name = "bucket-name"
    blob = mocker.Mock()
    blob.upload_from_file.side_effect = GoogleAPICallError("storage unavailable")
    bucket.blob.return_value = blob
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    mocker.patch("app.services.user_account_service.get_storage_bucket", return_value=bucket)
    upload = _avatar_upload()

    with pytest.raises(FirestoreServiceError, match="Failed to upload avatar"):
        asyncio.run(
            user_account_service.upload_avatar(
                "user-1",
                upload,
                client_mutation_id="avatar-mutation-1",
            )
        )

    blob.upload_from_file.assert_called_once_with(
        upload.file,
        content_type="image/jpeg",
    )
    blob.patch.assert_not_called()
    user_ref.set.assert_not_called()
    assert upload.file.closed is True


def test_get_user_profile_data_returns_profile_document(
    mocker: MockerFixture,
) -> None:
    client, users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"uid": "user-1", "username": "neo"},
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    profile = asyncio.run(user_account_service.get_user_profile_data("user-1"))

    users_collection_ref.document.assert_called_once_with("user-1")
    assert profile == {"uid": "user-1", "username": "neo"}
    user_ref.set.assert_not_called()


def test_get_user_profile_data_returns_none_when_missing(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.return_value = _build_snapshot(mocker, exists=False)
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    profile = asyncio.run(user_account_service.get_user_profile_data("user-1"))

    assert profile is None
    user_ref.set.assert_not_called()


def test_get_user_profile_data_can_touch_last_login_for_session_bootstrap(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"uid": "user-1", "lastLogin": "2026-01-01T00:00:00Z"},
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.user_account_service._utc_timestamp",
        return_value="2026-05-17T10:30:00Z",
    )

    profile = asyncio.run(
        user_account_service.get_user_profile_data(
            "user-1",
            touch_last_login=True,
        )
    )

    user_ref.set.assert_called_once_with(
        {"lastLogin": "2026-05-17T10:30:00Z"},
        merge=True,
    )
    assert profile == {"uid": "user-1", "lastLogin": "2026-05-17T10:30:00Z"}


def test_get_user_profile_data_omits_stale_backend_avatar_local_path(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "uid": "user-1",
            "username": "neo",
            "avatarUrl": "https://cdn/avatar.jpg",
            "avatarLocalPath": "file:///stale-backend-avatar.jpg",
        },
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.user_account_service._utc_timestamp",
        return_value="2026-05-17T10:30:00Z",
    )

    profile = asyncio.run(
        user_account_service.get_user_profile_data(
            "user-1",
            touch_last_login=True,
        )
    )

    user_ref.set.assert_called_once_with(
        {
            "lastLogin": "2026-05-17T10:30:00Z",
            "avatarLocalPath": user_account_service.firestore.DELETE_FIELD,
        },
        merge=True,
    )
    assert profile == {
        "uid": "user-1",
        "username": "neo",
        "avatarUrl": "https://cdn/avatar.jpg",
        "lastLogin": "2026-05-17T10:30:00Z",
    }


def test_get_user_profile_data_clears_confirmed_email_pending(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "uid": "user-1",
            "email": "old@example.com",
            "emailPending": "new@example.com",
            "lastLogin": "2026-01-01T00:00:00Z",
        },
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.user_account_service._utc_timestamp",
        return_value="2026-05-17T10:30:00Z",
    )

    profile = asyncio.run(
        user_account_service.get_user_profile_data(
            "user-1",
            touch_last_login=True,
            auth_email="new@example.com",
        )
    )

    document = user_ref.set.call_args.args[0]
    assert user_ref.set.call_args.kwargs == {"merge": True}
    assert document["lastLogin"] == "2026-05-17T10:30:00Z"
    assert document["email"] == "new@example.com"
    assert document["emailPending"] is user_account_service.firestore.DELETE_FIELD
    assert profile == {
        "uid": "user-1",
        "email": "new@example.com",
        "lastLogin": "2026-05-17T10:30:00Z",
    }


def test_get_user_profile_data_preserves_unmatched_email_pending(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "uid": "user-1",
            "email": "old@example.com",
            "emailPending": "pending@example.com",
        },
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    profile = asyncio.run(
        user_account_service.get_user_profile_data(
            "user-1",
            auth_email="other@example.com",
        )
    )

    document = user_ref.set.call_args.args[0]
    assert document == {"email": "other@example.com"}
    assert profile == {
        "uid": "user-1",
        "email": "other@example.com",
        "emailPending": "pending@example.com",
    }


def test_get_user_profile_data_wraps_last_login_write_errors(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"uid": "user-1"},
    )
    user_ref.set.side_effect = GoogleAPICallError("boom")
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    with pytest.raises(FirestoreServiceError):
        asyncio.run(
            user_account_service.get_user_profile_data(
                "user-1",
                touch_last_login=True,
            )
        )


def test_upsert_user_profile_data_bootstraps_server_owned_fields(
    mocker: MockerFixture,
) -> None:
    client, users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "username": "neo",
            "profile": {
                "aiConsent": {
                    "status": "revoked",
                    "grantedAt": "2026-05-01T10:00:00Z",
                    "revokedAt": "2026-05-02T10:00:00Z",
                },
                "consents": {"aiHealthDataConsentAt": "2026-04-01T10:00:00Z"},
                "readiness": {
                    "status": "needs_ai_consent",
                    "onboardingCompletedAt": "2026-04-01T09:00:00Z",
                    "readyAt": None,
                },
            },
        },
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.user_account_service.streak_service.sync_streak_from_meals")

    profile = asyncio.run(
        user_account_service.upsert_user_profile_data(
            "user-1",
            {"profile": {"language": "pl"}},
            client_mutation_id="profile-mutation-1",
            auth_email="user-1@example.com",
        )
    )

    transaction = client.transaction.return_value
    users_collection_ref.document.assert_any_call("user-1")
    document = _transaction_set_payload(transaction, user_ref)
    assert document["uid"] == "user-1"
    assert document["email"] == "user-1@example.com"
    assert document["createdAt"] == ANY
    assert document["plan"] == "free"
    assert document["syncState"] == "pending"
    assert document["lastLogin"] == ANY
    assert document["profile"]["language"] == "pl"
    assert document["profile"]["aiConsent"] == {
        "status": "revoked",
        "grantedAt": "2026-05-01T10:00:00Z",
        "revokedAt": "2026-05-02T10:00:00Z",
    }
    assert document["profile"]["consents"] is user_account_service.firestore.DELETE_FIELD
    assert document["profile"]["readiness"] == {
        "status": "needs_ai_consent",
        "onboardingCompletedAt": "2026-04-01T09:00:00Z",
        "readyAt": None,
    }
    assert profile["uid"] == "user-1"
    assert profile["email"] == "user-1@example.com"
    assert profile["username"] == "neo"
    assert profile["profile"]["language"] == "pl"
    assert "consents" not in profile["profile"]
    sync_streak.assert_not_called()
    assert len(transaction.set_calls) == 2


def test_upsert_user_profile_data_clears_confirmed_email_pending(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "username": "neo",
            "email": "old@example.com",
            "emailPending": "new@example.com",
            "profile": {"language": "en"},
        },
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.user_account_service.streak_service.sync_streak_from_meals"
    )

    profile = asyncio.run(
        user_account_service.upsert_user_profile_data(
            "user-1",
            {"profile": {"language": "pl"}},
            client_mutation_id="profile-mutation-2",
            auth_email="new@example.com",
        )
    )

    document = _transaction_set_payload(client.transaction.return_value, user_ref)
    assert document["email"] == "new@example.com"
    assert document["emailPending"] is user_account_service.firestore.DELETE_FIELD
    assert profile["email"] == "new@example.com"
    assert "emailPending" not in profile


def test_upsert_user_profile_data_recomputes_streak_when_calorie_target_changes(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "username": "neo",
            "profile": {
                "nutritionProfile": {
                    "calorieTarget": 2000,
                },
            },
        },
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.user_account_service.streak_service.sync_streak_from_meals")

    profile = asyncio.run(
        user_account_service.upsert_user_profile_data(
            "user-1",
            {"profile": {"nutritionProfile": {"calorieTarget": 1800}}},
            client_mutation_id="profile-mutation-calorie",
            auth_email="user-1@example.com",
        )
    )

    assert profile["profile"]["nutritionProfile"]["calorieTarget"] == 1800
    sync_streak.assert_called_once_with("user-1")


def test_upsert_user_profile_data_rejects_forbidden_fields(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, _user_ref, _username_ref = (
        _build_client(mocker)
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    with pytest.raises(UserProfileValidationError):
        asyncio.run(
            user_account_service.upsert_user_profile_data(
                "user-1",
                {"username": "neo"},
                client_mutation_id="profile-mutation-forbidden",
                auth_email="user-1@example.com",
            )
        )


def test_upsert_user_profile_data_rejects_nested_ai_consent_before_firestore(
    mocker: MockerFixture,
) -> None:
    get_firestore = mocker.patch("app.services.user_account_service.get_firestore")

    with pytest.raises(UserProfileValidationError):
        asyncio.run(
            user_account_service.upsert_user_profile_data(
                "user-1",
                {
                    "profile": {
                        "aiConsent": {
                            "status": "granted",
                            "grantedAt": "2026-05-01T10:00:00Z",
                            "revokedAt": None,
                        }
                    }
                },
                client_mutation_id="profile-mutation-ai-consent",
                auth_email="user-1@example.com",
            )
        )

    get_firestore.assert_not_called()


def test_upsert_user_profile_data_rejects_nested_readiness_before_firestore(
    mocker: MockerFixture,
) -> None:
    get_firestore = mocker.patch("app.services.user_account_service.get_firestore")

    with pytest.raises(UserProfileValidationError):
        asyncio.run(
            user_account_service.upsert_user_profile_data(
                "user-1",
                {
                    "profile": {
                        "language": "pl",
                        "readiness": {
                            "status": "ready",
                            "onboardingCompletedAt": "2026-05-01T10:00:00Z",
                            "readyAt": "2026-05-01T10:00:00Z",
                        },
                    }
                },
                client_mutation_id="profile-mutation-readiness",
                auth_email="user-1@example.com",
            )
        )

    get_firestore.assert_not_called()


def test_upsert_user_profile_data_accepts_editable_nested_profile_fields(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"uid": "user-1", "profile": {"language": "en"}},
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    sync_streak = mocker.patch(
        "app.services.user_account_service.streak_service.sync_streak_from_meals"
    )

    profile = asyncio.run(
        user_account_service.upsert_user_profile_data(
            "user-1",
            {
                "profile": {
                    "language": "pl",
                    "nutritionProfile": {"goal": "gain"},
                    "aiPreferences": {"stylePersona": "focused_coach"},
                }
            },
            client_mutation_id="profile-mutation-editable",
            auth_email="user-1@example.com",
        )
    )

    document = _transaction_set_payload(client.transaction.return_value, user_ref)
    assert document["profile"]["language"] == "pl"
    assert document["profile"]["nutritionProfile"]["goal"] == "gain"
    assert document["profile"]["aiPreferences"]["stylePersona"] == "focused_coach"
    assert profile["profile"]["language"] == "pl"
    assert profile["profile"]["nutritionProfile"]["goal"] == "gain"
    assert profile["profile"]["aiPreferences"]["stylePersona"] == "focused_coach"
    sync_streak.assert_not_called()


def test_upsert_user_profile_data_replays_duplicate_mutation_without_second_write(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    mutation_ref = user_ref.collection.return_value.document.return_value
    result_profile: dict[str, Any] = {
        "uid": "user-1",
        "profile": {
            "language": "pl",
            "nutritionProfile": {"calorieTarget": 1800},
        },
    }
    payload_hash = user_account_service._stable_profile_payload_hash(
        {
            "kind": "profile_update",
            "profile": {"profile": {"nutritionProfile": {"calorieTarget": 1800}}},
        }
    )
    mutation_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "userId": "user-1",
            "clientMutationId": "profile-mutation-replay",
            "kind": "profile_update",
            "profileDocumentId": "user_profile",
            "payloadHash": payload_hash,
            "resultProfile": result_profile,
            "applied": True,
        },
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    sync_streak = mocker.patch(
        "app.services.user_account_service.streak_service.sync_streak_from_meals"
    )

    profile = asyncio.run(
        user_account_service.upsert_user_profile_data(
            "user-1",
            {"profile": {"nutritionProfile": {"calorieTarget": 1800}}},
            client_mutation_id="profile-mutation-replay",
            auth_email="user-1@example.com",
        )
    )

    assert profile == result_profile
    user_ref.get.assert_not_called()
    assert client.transaction.return_value.set_calls == []
    sync_streak.assert_not_called()


def test_upsert_user_profile_data_rejects_reused_mutation_id_for_different_patch(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    mutation_ref = user_ref.collection.return_value.document.return_value
    mutation_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "userId": "user-1",
            "clientMutationId": "profile-mutation-conflict",
            "kind": "profile_update",
            "profileDocumentId": "user_profile",
            "payloadHash": "different-payload",
            "resultProfile": {"uid": "user-1"},
            "applied": True,
        },
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    with pytest.raises(UserProfileMutationDedupeConflictError):
        asyncio.run(
            user_account_service.upsert_user_profile_data(
                "user-1",
                {"profile": {"language": "pl"}},
                client_mutation_id="profile-mutation-conflict",
                auth_email="user-1@example.com",
            )
        )

    user_ref.get.assert_not_called()
    assert client.transaction.return_value.set_calls == []


def test_upsert_user_profile_data_rejects_reused_mutation_id_for_different_kind(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    mutation_ref = user_ref.collection.return_value.document.return_value
    payload_hash = user_account_service._stable_profile_payload_hash(
        {"kind": "profile_update", "profile": {"profile": {"language": "pl"}}}
    )
    mutation_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "userId": "user-1",
            "clientMutationId": "profile-mutation-kind-conflict",
            "kind": "delete",
            "profileDocumentId": "user_profile",
            "payloadHash": payload_hash,
            "resultProfile": {"uid": "user-1"},
            "applied": True,
        },
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    with pytest.raises(UserProfileMutationDedupeConflictError):
        asyncio.run(
            user_account_service.upsert_user_profile_data(
                "user-1",
                {"profile": {"language": "pl"}},
                client_mutation_id="profile-mutation-kind-conflict",
                auth_email="user-1@example.com",
            )
        )

    user_ref.get.assert_not_called()
    assert client.transaction.return_value.set_calls == []


def test_complete_onboarding_profile_marks_ready_and_preserves_revoked_ai_consent(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    existing_ai_consent: dict[str, str | None] = {
        "status": "revoked",
        "grantedAt": "2026-05-01T10:00:00Z",
        "revokedAt": "2026-05-02T10:00:00Z",
    }
    completion_readiness: dict[str, str | None] = {
        "status": "ready",
        "onboardingCompletedAt": "2026-05-05T10:00:00Z",
        "readyAt": "2026-05-05T10:00:00Z",
    }
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "uid": "user-1",
            "username": "neo",
            "profile": {
                "aiConsent": existing_ai_consent,
                "consents": {"aiHealthDataConsentAt": "2026-04-01T10:00:00Z"},
                "readiness": {
                    "status": "needs_profile",
                    "onboardingCompletedAt": None,
                    "readyAt": None,
                },
            },
        },
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.user_account_service._utc_timestamp",
        return_value="2026-05-05T10:00:00Z",
    )
    sync_streak = mocker.patch(
        "app.services.user_account_service.streak_service.sync_streak_from_meals"
    )

    profile = asyncio.run(
        user_account_service.complete_onboarding_profile(
            "user-1",
            {
                "profile": {
                    "nutritionProfile": {"calorieTarget": 2200},
                    "aiPreferences": {"stylePersona": "focused_coach"},
                    "readiness": completion_readiness,
                }
            },
            auth_email="user-1@example.com",
        )
    )

    document = user_ref.set.call_args.args[0]
    assert user_ref.set.call_args.kwargs == {"merge": True}
    assert document["profile"]["aiConsent"] == existing_ai_consent
    assert document["profile"]["readiness"] == completion_readiness
    assert document["profile"]["consents"] is user_account_service.firestore.DELETE_FIELD
    assert profile["profile"]["aiConsent"] == existing_ai_consent
    assert profile["profile"]["readiness"] == completion_readiness
    assert "consents" not in profile["profile"]
    sync_streak.assert_called_once_with("user-1")


def test_grant_ai_consent_creates_release_contract_and_removes_legacy_consents(
    mocker: MockerFixture,
) -> None:
    client, users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    existing_readiness: dict[str, str | None] = {
        "status": "ready",
        "onboardingCompletedAt": "2026-04-01T09:00:00Z",
        "readyAt": "2026-04-01T09:00:00Z",
    }
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "username": "neo",
            "profile": {
                "consents": {"retiredAt": "2026-04-01T10:00:00Z"},
                "readiness": existing_readiness,
            },
        },
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.user_account_service._utc_timestamp",
        return_value="2026-05-01T10:00:00Z",
    )

    ai_consent = asyncio.run(
        user_account_service.grant_ai_consent(
            "user-1",
            auth_email="user-1@example.com",
        )
    )

    users_collection_ref.document.assert_called_once_with("user-1")
    assert ai_consent == {
        "status": "granted",
        "grantedAt": "2026-05-01T10:00:00Z",
        "revokedAt": None,
    }
    document = user_ref.set.call_args.args[0]
    assert document["email"] == "user-1@example.com"
    assert document["profile"]["aiConsent"] == ai_consent
    assert document["profile"]["consents"] is user_account_service.firestore.DELETE_FIELD
    assert document["profile"]["readiness"] == existing_readiness


def test_grant_ai_consent_does_not_refresh_already_active_consent(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    active_consent: dict[str, str | None] = {
        "status": "granted",
        "grantedAt": "2026-05-01T10:00:00Z",
        "revokedAt": None,
    }
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"profile": {"aiConsent": active_consent}},
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.user_account_service._utc_timestamp",
        return_value="2026-05-02T10:00:00Z",
    )

    ai_consent = asyncio.run(user_account_service.grant_ai_consent("user-1"))

    assert ai_consent == active_consent
    document = user_ref.set.call_args.args[0]
    assert document["profile"]["aiConsent"] == active_consent
    assert document["profile"]["consents"] is user_account_service.firestore.DELETE_FIELD


def test_grant_ai_consent_after_revoked_creates_fresh_grant(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "profile": {
                "aiConsent": {
                    "status": "revoked",
                    "grantedAt": "2026-05-01T10:00:00Z",
                    "revokedAt": "2026-05-02T10:00:00Z",
                }
            }
        },
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.user_account_service._utc_timestamp",
        return_value="2026-05-03T10:00:00Z",
    )

    ai_consent = asyncio.run(user_account_service.grant_ai_consent("user-1"))

    assert ai_consent == {
        "status": "granted",
        "grantedAt": "2026-05-03T10:00:00Z",
        "revokedAt": None,
    }


def test_revoke_ai_consent_sets_inactive_state_and_is_repeat_idempotent(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    existing_readiness: dict[str, str | None] = {
        "status": "ready",
        "onboardingCompletedAt": "2026-04-01T09:00:00Z",
        "readyAt": "2026-04-01T09:00:00Z",
    }
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "profile": {
                "aiConsent": {
                    "status": "granted",
                    "grantedAt": "2026-05-01T10:00:00Z",
                    "revokedAt": None,
                },
                "readiness": existing_readiness,
            }
        },
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.user_account_service._utc_timestamp",
        return_value="2026-05-02T10:00:00Z",
    )

    ai_consent = asyncio.run(user_account_service.revoke_ai_consent("user-1"))

    assert ai_consent == {
        "status": "revoked",
        "grantedAt": "2026-05-01T10:00:00Z",
        "revokedAt": "2026-05-02T10:00:00Z",
    }
    document = user_ref.set.call_args.args[0]
    assert document["profile"]["readiness"] == existing_readiness

    user_ref.set.reset_mock()
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"profile": {"aiConsent": ai_consent, "readiness": existing_readiness}},
    )
    mocker.patch(
        "app.services.user_account_service._utc_timestamp",
        return_value="2026-05-03T10:00:00Z",
    )

    repeated = asyncio.run(user_account_service.revoke_ai_consent("user-1"))

    assert repeated == ai_consent
    document = user_ref.set.call_args.args[0]
    assert document["profile"]["aiConsent"] == ai_consent
    assert document["profile"]["readiness"] == existing_readiness
    assert document["profile"]["consents"] is user_account_service.firestore.DELETE_FIELD


def test_initialize_onboarding_profile_creates_atomic_profile_and_username(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, usernames_collection_ref, user_ref, username_ref = (
        _build_client(mocker)
    )
    previous_username_ref = mocker.Mock()
    def _document_for_key(key: str) -> object:
        return previous_username_ref if key == "old-name" else username_ref

    usernames_collection_ref.document.side_effect = _document_for_key
    transaction = FakeTransaction()
    client.transaction.return_value = transaction
    username_ref.get.return_value = _build_snapshot(mocker, exists=False)
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"username": "old-name"},
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    normalized_username, profile = asyncio.run(
        user_account_service.initialize_onboarding_profile(
            "user-1",
            username=" Neo ",
            language="pl-PL",
            auth_email="user@example.com",
        )
    )

    assert normalized_username == "neo"
    assert profile["uid"] == "user-1"
    assert profile["username"] == "neo"
    assert profile["email"] == "user@example.com"
    assert profile["profile"]["language"] == "pl"
    assert any(
        call[0] is username_ref and call[1] == {"uid": "user-1"} and call[2] is True
        for call in transaction.set_calls
    )
    assert any(
        call[0] is user_ref and call[2] is True and call[1]["username"] == "neo"
        for call in transaction.set_calls
    )
    assert transaction.delete_calls == [previous_username_ref]


def test_initialize_onboarding_profile_deletes_legacy_nested_consents(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, username_ref = (
        _build_client(mocker)
    )
    transaction = FakeTransaction()
    client.transaction.return_value = transaction
    username_ref.get.return_value = _build_snapshot(mocker, exists=False)
    existing_ai_consent: dict[str, str | None] = {
        "status": "revoked",
        "grantedAt": "2026-05-01T10:00:00Z",
        "revokedAt": "2026-05-02T10:00:00Z",
    }
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "uid": "user-1",
            "username": "neo",
            "profile": {
                "aiConsent": existing_ai_consent,
                "consents": {"aiHealthDataConsentAt": "2026-04-01T10:00:00Z"},
                "readiness": {
                    "status": "needs_profile",
                    "onboardingCompletedAt": None,
                    "readyAt": None,
                },
            },
        },
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    _normalized_username, profile = asyncio.run(
        user_account_service.initialize_onboarding_profile(
            "user-1",
            username="Neo",
            language="pl",
            auth_email="user@example.com",
        )
    )

    user_write = next(
        call[1] for call in transaction.set_calls if call[0] is user_ref
    )
    written_profile = user_write["profile"]
    assert isinstance(written_profile, dict)
    assert written_profile["aiConsent"] == existing_ai_consent
    assert written_profile["consents"] is user_account_service.firestore.DELETE_FIELD
    assert profile["profile"]["aiConsent"] == existing_ai_consent
    assert "consents" not in profile["profile"]


def test_initialize_onboarding_profile_repeated_same_uid_and_username_succeeds(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, username_ref = (
        _build_client(mocker)
    )
    first_transaction = FakeTransaction()
    second_transaction = FakeTransaction()
    client.transaction.side_effect = [first_transaction, second_transaction]
    username_ref.get.side_effect = [
        _build_snapshot(mocker, exists=False),
        _build_snapshot(mocker, exists=True, data={"uid": "user-1"}),
    ]
    user_ref.get.side_effect = [
        _build_snapshot(mocker, exists=False),
        _build_snapshot(
            mocker,
            exists=True,
            data={"uid": "user-1", "username": "neo", "email": "user@example.com"},
        ),
    ]
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    first_username, first_profile = asyncio.run(
        user_account_service.initialize_onboarding_profile(
            "user-1",
            username="Neo",
            language="pl",
            auth_email="user@example.com",
        )
    )
    second_username, second_profile = asyncio.run(
        user_account_service.initialize_onboarding_profile(
            "user-1",
            username="Neo",
            language="pl",
            auth_email="user@example.com",
        )
    )

    assert first_username == second_username == "neo"
    assert first_profile["uid"] == second_profile["uid"] == "user-1"
    assert first_profile["username"] == second_profile["username"] == "neo"
    assert first_transaction.delete_calls == []
    assert second_transaction.delete_calls == []


def test_initialize_onboarding_profile_is_idempotent_for_same_username_owner(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, username_ref = (
        _build_client(mocker)
    )
    transaction = FakeTransaction()
    client.transaction.return_value = transaction
    username_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"uid": "user-1"},
    )
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"uid": "user-1", "username": "neo", "email": "existing@example.com"},
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    normalized_username, profile = asyncio.run(
        user_account_service.initialize_onboarding_profile(
            "user-1",
            username="neo",
            language="en",
            auth_email=None,
        )
    )

    assert normalized_username == "neo"
    assert profile["username"] == "neo"
    assert profile["email"] == "existing@example.com"
    assert transaction.delete_calls == []


def test_initialize_onboarding_profile_raises_when_username_taken(
    mocker: MockerFixture,
) -> None:
    client, _users_collection_ref, _usernames_collection_ref, _user_ref, username_ref = (
        _build_client(mocker)
    )
    transaction = FakeTransaction()
    client.transaction.return_value = transaction
    username_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"uid": "other-user"},
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    with pytest.raises(OnboardingUsernameUnavailableError):
        asyncio.run(
            user_account_service.initialize_onboarding_profile(
                "user-1",
                username="neo",
                language="pl",
                auth_email="user@example.com",
            )
        )


def test_initialize_onboarding_profile_rejects_short_username() -> None:
    with pytest.raises(OnboardingValidationError):
        asyncio.run(
            user_account_service.initialize_onboarding_profile(
                "user-1",
                username="ab",
                language="pl",
                auth_email="user@example.com",
            )
        )


def test_get_user_export_data_returns_profile_and_subcollections(
    mocker: MockerFixture,
) -> None:
    client, users_collection_ref, usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    telemetry_collection_ref = mocker.Mock()
    telemetry_query = mocker.Mock()
    meals_collection_ref = mocker.Mock()
    my_meals_collection_ref = mocker.Mock()
    notifications_collection_ref = mocker.Mock()
    prefs_collection_ref = mocker.Mock()
    feedback_collection_ref = mocker.Mock()
    meal_mutation_dedupe_collection_ref = mocker.Mock()
    billing_collection_ref = mocker.Mock()
    main_billing_ref = mocker.Mock()
    annual_billing_ref = mocker.Mock()
    main_ai_credits_collection_ref = mocker.Mock()
    main_ai_credit_transactions_collection_ref = mocker.Mock()
    main_ai_credit_idempotency_collection_ref = mocker.Mock()
    annual_ai_credits_collection_ref = mocker.Mock()
    annual_ai_credit_transactions_collection_ref = mocker.Mock()
    annual_ai_credit_idempotency_collection_ref = mocker.Mock()
    badges_collection_ref = mocker.Mock()
    streak_collection_ref = mocker.Mock()
    reminder_daily_stats_collection_ref = mocker.Mock()
    chat_threads_collection_ref = mocker.Mock()
    ai_runs_collection_ref = mocker.Mock()
    ai_runs_query = mocker.Mock()
    meal_document = mocker.Mock()
    meal_document.id = "meal-1"
    meal_document.to_dict.return_value = {"id": "meal-1"}
    my_meal_document = mocker.Mock()
    my_meal_document.id = "saved-1"
    my_meal_document.to_dict.return_value = {"id": "saved-1"}
    notification_document = mocker.Mock()
    notification_document.id = "notif-1"
    notification_document.to_dict.return_value = {"id": "notif-1", "enabled": True}
    prefs_document = mocker.Mock()
    prefs_document.id = "notifications"
    prefs_document.to_dict.return_value = {
        "notifications": {"motivationEnabled": True, "daysAhead": 7}
    }
    feedback_document = mocker.Mock()
    feedback_document.id = "feedback-1"
    feedback_document.to_dict.return_value = {"id": "feedback-1", "message": "hello"}
    meal_mutation_dedupe_document = mocker.Mock()
    meal_mutation_dedupe_document.id = "profile-mutation-1"
    meal_mutation_dedupe_document.to_dict.return_value = {
        "clientMutationId": "profile-mutation-1",
        "kind": "profile_update",
    }
    main_billing_document = mocker.Mock()
    main_billing_document.id = "main"
    main_billing_document.to_dict.return_value = {"status": "active"}
    main_billing_document.reference = main_billing_ref
    annual_billing_document = mocker.Mock()
    annual_billing_document.id = "annual"
    annual_billing_document.to_dict.return_value = {"status": "past_due"}
    annual_billing_document.reference = annual_billing_ref
    main_ai_credit_document = mocker.Mock()
    main_ai_credit_document.id = "current"
    main_ai_credit_document.to_dict.return_value = {"balance": 8}
    main_ai_credit_transaction_document = mocker.Mock()
    main_ai_credit_transaction_document.id = "tx-1"
    main_ai_credit_transaction_document.to_dict.return_value = {"amount": -1}
    main_ai_credit_idempotency_document = mocker.Mock()
    main_ai_credit_idempotency_document.id = "idem-1"
    main_ai_credit_idempotency_document.to_dict.return_value = {"state": "deducted"}
    annual_ai_credit_document = mocker.Mock()
    annual_ai_credit_document.id = "renewal"
    annual_ai_credit_document.to_dict.return_value = {"balance": 20}
    annual_ai_credit_transaction_document = mocker.Mock()
    annual_ai_credit_transaction_document.id = "tx-annual"
    annual_ai_credit_transaction_document.to_dict.return_value = {"amount": 20}
    annual_ai_credit_idempotency_document = mocker.Mock()
    annual_ai_credit_idempotency_document.id = "idem-annual"
    annual_ai_credit_idempotency_document.to_dict.return_value = {"state": "applied"}
    badge_document = mocker.Mock()
    badge_document.id = "streak_7"
    badge_document.to_dict.return_value = {"type": "streak", "unlockedAt": 1}
    streak_document = mocker.Mock()
    streak_document.id = "main"
    streak_document.to_dict.return_value = {"current": 7, "lastDate": "2026-03-03"}
    reminder_daily_stats_document = mocker.Mock()
    reminder_daily_stats_document.id = "2026-03-03"
    reminder_daily_stats_document.to_dict.return_value = {
        "sendCount": 2,
        "emittedDecisionKeys": ["2026-03-03:breakfast:2026-03-03T08:00:00Z"],
    }
    telemetry_document = mocker.Mock()
    telemetry_document.id = "telemetry-1"
    telemetry_document.to_dict.return_value = {
        "eventId": "telemetry-1",
        "name": "meal_logged",
        "userHash": telemetry_service.build_user_hash("user-1"),
    }
    chat_thread_document = mocker.Mock()
    chat_thread_document.id = "thread-1"
    chat_thread_document.to_dict.return_value = {"title": "First chat"}
    chat_messages_collection_ref = mocker.Mock()
    chat_memory_collection_ref = mocker.Mock()
    chat_document = mocker.Mock()
    chat_document.id = "chat-1"
    chat_document.to_dict.return_value = {"role": "assistant", "content": "hello"}
    memory_document = mocker.Mock()
    memory_document.id = "current"
    memory_document.to_dict.return_value = {"summary": "likes breakfast"}
    ai_run_document = mocker.Mock()
    ai_run_document.id = "run-1"
    ai_run_document.to_dict.return_value = {"userId": "user-1", "status": "completed"}

    def top_level_collection_side_effect(name: str):
        if name == "users":
            return users_collection_ref
        if name == "usernames":
            return usernames_collection_ref
        if name == "telemetry_events":
            return telemetry_collection_ref
        if name == "ai_runs":
            return ai_runs_collection_ref
        raise AssertionError(f"Unexpected collection {name}")

    client.collection.side_effect = top_level_collection_side_effect
    telemetry_collection_ref.where.return_value = telemetry_query
    telemetry_query.stream.return_value = [telemetry_document]

    def collection_side_effect(name: str):
        if name == "meals":
            return meals_collection_ref
        if name == "mealTemplates":
            return my_meals_collection_ref
        if name == "notifications":
            return notifications_collection_ref
        if name == "prefs":
            return prefs_collection_ref
        if name == "feedback":
            return feedback_collection_ref
        if name == "mealMutationDedupe":
            return meal_mutation_dedupe_collection_ref
        if name == "billing":
            return billing_collection_ref
        if name == "badges":
            return badges_collection_ref
        if name == "streak":
            return streak_collection_ref
        if name == "reminderDailyStats":
            return reminder_daily_stats_collection_ref
        if name == "chat_threads":
            return chat_threads_collection_ref
        raise AssertionError(f"Unexpected subcollection {name}")

    user_ref.collection.side_effect = collection_side_effect
    meals_collection_ref.stream.return_value = [meal_document]
    my_meals_collection_ref.stream.return_value = [my_meal_document]
    notifications_collection_ref.stream.return_value = [notification_document]
    prefs_collection_ref.stream.return_value = [prefs_document]
    feedback_collection_ref.stream.return_value = [feedback_document]
    meal_mutation_dedupe_collection_ref.stream.return_value = [
        meal_mutation_dedupe_document
    ]
    billing_collection_ref.stream.return_value = [
        main_billing_document,
        annual_billing_document,
    ]
    def billing_document_side_effect(document_id: str):
        if document_id == "main":
            return main_billing_ref
        if document_id == "annual":
            return annual_billing_ref
        raise AssertionError(f"Unexpected billing document {document_id}")

    billing_collection_ref.document.side_effect = billing_document_side_effect
    badges_collection_ref.stream.return_value = [badge_document]
    streak_collection_ref.stream.return_value = [streak_document]
    reminder_daily_stats_collection_ref.stream.return_value = [
        reminder_daily_stats_document
    ]
    chat_threads_collection_ref.stream.return_value = [chat_thread_document]
    ai_runs_collection_ref.where.return_value = ai_runs_query
    ai_runs_query.stream.return_value = [ai_run_document]

    def main_billing_child_collection_side_effect(name: str):
        if name == "aiCredits":
            return main_ai_credits_collection_ref
        if name == "aiCreditTransactions":
            return main_ai_credit_transactions_collection_ref
        if name == "aiCreditIdempotency":
            return main_ai_credit_idempotency_collection_ref
        raise AssertionError(f"Unexpected main billing subcollection {name}")

    def annual_billing_child_collection_side_effect(name: str):
        if name == "aiCredits":
            return annual_ai_credits_collection_ref
        if name == "aiCreditTransactions":
            return annual_ai_credit_transactions_collection_ref
        if name == "aiCreditIdempotency":
            return annual_ai_credit_idempotency_collection_ref
        raise AssertionError(f"Unexpected annual billing subcollection {name}")

    main_billing_ref.collection.side_effect = main_billing_child_collection_side_effect
    annual_billing_ref.collection.side_effect = annual_billing_child_collection_side_effect
    main_ai_credits_collection_ref.stream.return_value = [main_ai_credit_document]
    main_ai_credit_transactions_collection_ref.stream.return_value = [
        main_ai_credit_transaction_document
    ]
    main_ai_credit_idempotency_collection_ref.stream.return_value = [
        main_ai_credit_idempotency_document
    ]
    annual_ai_credits_collection_ref.stream.return_value = [annual_ai_credit_document]
    annual_ai_credit_transactions_collection_ref.stream.return_value = [
        annual_ai_credit_transaction_document
    ]
    annual_ai_credit_idempotency_collection_ref.stream.return_value = [
        annual_ai_credit_idempotency_document
    ]

    def chat_thread_child_collection_side_effect(name: str):
        if name == "messages":
            return chat_messages_collection_ref
        if name == "memory":
            return chat_memory_collection_ref
        raise AssertionError(f"Unexpected chat thread subcollection {name}")

    chat_thread_document.reference.collection.side_effect = (
        chat_thread_child_collection_side_effect
    )
    chat_messages_collection_ref.stream.return_value = [chat_document]
    chat_memory_collection_ref.stream.return_value = [memory_document]
    user_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={"uid": "user-1", "username": "neo"},
    )
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

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
    ) = asyncio.run(
        user_account_service.get_user_export_data("user-1")
    )

    assert profile == {"uid": "user-1", "username": "neo"}
    assert meals == [{"id": "meal-1"}]
    assert my_meals == [{"id": "saved-1"}]
    assert chat_messages == [
        {
            "id": "chat-1",
            "role": "assistant",
            "content": "hello",
            "threadId": "thread-1",
            "threadTitle": "First chat",
        }
    ]
    assert chat_memory == [
        {"id": "current", "summary": "likes breakfast", "threadId": "thread-1"}
    ]
    assert ai_runs == [
        {"id": "run-1", "userId": "user-1", "status": "completed"}
    ]
    assert notifications == [{"id": "notif-1", "enabled": True}]
    assert notification_prefs == {"motivationEnabled": True, "daysAhead": 7}
    assert feedback == [{"id": "feedback-1", "message": "hello"}]
    assert meal_mutation_dedupe == [
        {
            "clientMutationId": "profile-mutation-1",
            "kind": "profile_update",
            "id": "profile-mutation-1",
        }
    ]
    assert billing == [
        {"status": "active", "id": "main"},
        {"status": "past_due", "id": "annual"},
    ]
    assert ai_credits == [
        {"balance": 8, "id": "current", "billingId": "main"},
        {"balance": 20, "id": "renewal", "billingId": "annual"},
    ]
    assert ai_credit_transactions == [
        {"amount": -1, "id": "tx-1", "billingId": "main"},
        {"amount": 20, "id": "tx-annual", "billingId": "annual"},
    ]
    assert ai_credit_idempotency == [
        {"state": "deducted", "id": "idem-1", "billingId": "main"},
        {"state": "applied", "id": "idem-annual", "billingId": "annual"},
    ]
    assert badges == [
        {"type": "streak", "unlockedAt": 1, "id": "streak_7"}
    ]
    assert streak == [
        {"current": 7, "lastDate": "2026-03-03", "id": "main"}
    ]
    assert reminder_daily_stats == [
        {
            "sendCount": 2,
            "emittedDecisionKeys": ["2026-03-03:breakfast:2026-03-03T08:00:00Z"],
            "id": "2026-03-03",
        }
    ]
    assert telemetry_events == [
        {
            "eventId": "telemetry-1",
            "name": "meal_logged",
            "userHash": telemetry_service.build_user_hash("user-1"),
            "id": "telemetry-1",
        }
    ]
    ai_runs_collection_ref.where.assert_called_once()
    ai_runs_filter = ai_runs_collection_ref.where.call_args.kwargs["filter"]
    assert ai_runs_filter.field_path == "userId"
    assert ai_runs_filter.op_string == "=="
    assert ai_runs_filter.value == "user-1"
    telemetry_collection_ref.where.assert_called_once()
    telemetry_filter = telemetry_collection_ref.where.call_args.kwargs["filter"]
    assert telemetry_filter.field_path == "userHash"
    assert telemetry_filter.op_string == "=="
    assert telemetry_filter.value == telemetry_service.build_user_hash("user-1")


def test_read_billing_export_reads_main_children_when_parent_doc_is_missing(
    mocker: MockerFixture,
) -> None:
    user_ref = mocker.Mock()
    billing_collection_ref = mocker.Mock()
    main_billing_ref = mocker.Mock()
    ai_credits_collection_ref = mocker.Mock()
    ai_credit_transactions_collection_ref = mocker.Mock()
    ai_credit_idempotency_collection_ref = mocker.Mock()
    ai_credit_doc = mocker.Mock()
    ai_credit_doc.id = "current"
    ai_credit_doc.to_dict.return_value = {"balance": 8}
    ai_credit_transaction_doc = mocker.Mock()
    ai_credit_transaction_doc.id = "tx-1"
    ai_credit_transaction_doc.to_dict.return_value = {"amount": -1}
    ai_credit_idempotency_doc = mocker.Mock()
    ai_credit_idempotency_doc.id = "idem-1"
    ai_credit_idempotency_doc.to_dict.return_value = {"state": "deducted"}

    def user_collection_side_effect(name: str):
        if name == "billing":
            return billing_collection_ref
        raise AssertionError(f"Unexpected subcollection {name}")

    def main_billing_child_collection_side_effect(name: str):
        if name == "aiCredits":
            return ai_credits_collection_ref
        if name == "aiCreditTransactions":
            return ai_credit_transactions_collection_ref
        if name == "aiCreditIdempotency":
            return ai_credit_idempotency_collection_ref
        raise AssertionError(f"Unexpected billing subcollection {name}")

    user_ref.collection.side_effect = user_collection_side_effect
    billing_collection_ref.stream.return_value = []
    billing_collection_ref.document.assert_not_called()
    billing_collection_ref.document.return_value = main_billing_ref
    main_billing_ref.collection.side_effect = main_billing_child_collection_side_effect
    ai_credits_collection_ref.stream.return_value = [ai_credit_doc]
    ai_credit_transactions_collection_ref.stream.return_value = [ai_credit_transaction_doc]
    ai_credit_idempotency_collection_ref.stream.return_value = [ai_credit_idempotency_doc]

    billing, ai_credits, ai_credit_transactions, ai_credit_idempotency = (
        user_account_service._read_billing_export(user_ref)
    )

    assert billing == []
    assert ai_credits == [{"balance": 8, "id": "current", "billingId": "main"}]
    assert ai_credit_transactions == [
        {"amount": -1, "id": "tx-1", "billingId": "main"}
    ]
    assert ai_credit_idempotency == [
        {"state": "deducted", "id": "idem-1", "billingId": "main"}
    ]
    billing_collection_ref.document.assert_called_once_with("main")


def test_get_user_export_data_wraps_firestore_errors(mocker: MockerFixture) -> None:
    client, _users_collection_ref, _usernames_collection_ref, user_ref, _username_ref = (
        _build_client(mocker)
    )
    user_ref.get.side_effect = GoogleAPICallError("boom")
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    with pytest.raises(FirestoreServiceError):
        asyncio.run(user_account_service.get_user_export_data("user-1"))
