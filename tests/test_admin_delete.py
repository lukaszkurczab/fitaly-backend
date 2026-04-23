"""Unit tests for scripts/admin_delete.py.

All Firebase / Firestore calls are mocked so the suite runs without any
real credentials.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_mock import MockerFixture

# Make sure the scripts package is importable even when running from the repo root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.admin_delete as admin_delete  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_doc_snap(
    *,
    exists: bool = True,
    data: dict[str, object] | None = None,
) -> MagicMock:
    snap = MagicMock()
    snap.exists = exists
    snap.to_dict.return_value = data or {}
    snap.id = "doc-id"
    return snap


def _make_stream(*docs: MagicMock) -> Callable[[], Generator[MagicMock, None, None]]:
    """Return a function that yields the supplied mock snapshots."""
    def _stream() -> Generator[MagicMock, None, None]:
        yield from docs

    return _stream


# ── _resolve_uid ──────────────────────────────────────────────────────────────

class TestResolveUid:
    def test_returns_uid_directly(self) -> None:
        assert admin_delete._resolve_uid(uid="abc123", email=None) == "abc123"

    def test_strips_whitespace_from_uid(self) -> None:
        assert admin_delete._resolve_uid(uid="  abc123  ", email=None) == "abc123"

    def test_resolves_email_to_uid(self, mocker: MockerFixture) -> None:
        user_record = MagicMock()
        user_record.uid = "uid-from-email"
        mocker.patch(
            "scripts.admin_delete.firebase_auth.get_user_by_email",
            return_value=user_record,
        )
        result = admin_delete._resolve_uid(uid=None, email="user@example.com")
        assert result == "uid-from-email"

    def test_exits_2_when_email_not_found(self, mocker: MockerFixture) -> None:
        import firebase_admin.auth as fa

        mocker.patch(
            "scripts.admin_delete.firebase_auth.get_user_by_email",
            side_effect=fa.UserNotFoundError("not found"),
        )
        with pytest.raises(SystemExit) as exc_info:
            admin_delete._resolve_uid(uid=None, email="ghost@example.com")
        assert exc_info.value.code == 2

    def test_raises_value_error_when_neither_provided(self) -> None:
        with pytest.raises(ValueError):
            admin_delete._resolve_uid(uid=None, email=None)


# ── _confirm ──────────────────────────────────────────────────────────────────

class TestConfirm:
    def test_proceeds_on_yes(self, mocker: MockerFixture) -> None:
        mocker.patch("builtins.input", return_value="yes")
        admin_delete._confirm("uid123", "user@example.com")  # should not raise

    def test_exits_3_on_anything_else(self, mocker: MockerFixture) -> None:
        mocker.patch("builtins.input", return_value="no")
        with pytest.raises(SystemExit) as exc_info:
            admin_delete._confirm("uid123", None)
        assert exc_info.value.code == 3

    def test_exits_3_on_empty_input(self, mocker: MockerFixture) -> None:
        mocker.patch("builtins.input", return_value="")
        with pytest.raises(SystemExit) as exc_info:
            admin_delete._confirm("uid123", None)
        assert exc_info.value.code == 3


# ── _delete_top_level_docs ────────────────────────────────────────────────────

class TestDeleteTopLevelDocs:
    @staticmethod
    def _build_client_for_top_level_docs(
        mocker: MockerFixture,
        *,
        billing_exists: bool,
        rate_limit_exists: bool,
    ) -> tuple[MagicMock, MagicMock, MagicMock]:
        users_collection = MagicMock()
        user_ref = MagicMock()
        billing_collection = MagicMock()
        billing_ref = MagicMock()
        billing_ref.get.return_value = _make_doc_snap(exists=billing_exists)
        billing_ref.collection.return_value.stream.return_value = iter([])
        billing_collection.document.return_value = billing_ref
        user_ref.collection.return_value = billing_collection
        users_collection.document.return_value = user_ref

        rate_limits_collection = MagicMock()
        rate_limit_ref = MagicMock()
        rate_limit_ref.get.return_value = _make_doc_snap(exists=rate_limit_exists)
        rate_limits_collection.document.return_value = rate_limit_ref

        client = mocker.Mock()

        def _collection(name: str) -> MagicMock:
            if name == admin_delete.USERS_COLLECTION:
                return users_collection
            if name == admin_delete.RATE_LIMITS_COLLECTION:
                return rate_limits_collection
            raise AssertionError(f"Unexpected collection: {name}")

        client.collection.side_effect = _collection
        return client, billing_ref, rate_limit_ref

    def test_deletes_existing_docs(self, mocker: MockerFixture) -> None:
        client, billing_ref, rate_limit_ref = self._build_client_for_top_level_docs(
            mocker,
            billing_exists=True,
            rate_limit_exists=True,
        )

        mocker.patch("scripts.admin_delete.get_firestore", return_value=client)

        admin_delete._delete_top_level_docs("uid123", dry_run=False)

        billing_ref.delete.assert_called_once()
        rate_limit_ref.delete.assert_called_once()

    def test_skips_missing_docs(self, mocker: MockerFixture) -> None:
        client, billing_ref, rate_limit_ref = self._build_client_for_top_level_docs(
            mocker,
            billing_exists=False,
            rate_limit_exists=False,
        )

        mocker.patch("scripts.admin_delete.get_firestore", return_value=client)

        admin_delete._delete_top_level_docs("uid123", dry_run=False)

        billing_ref.delete.assert_not_called()
        rate_limit_ref.delete.assert_not_called()

    def test_dry_run_does_not_delete(self, mocker: MockerFixture) -> None:
        client, billing_ref, rate_limit_ref = self._build_client_for_top_level_docs(
            mocker,
            billing_exists=True,
            rate_limit_exists=True,
        )

        mocker.patch("scripts.admin_delete.get_firestore", return_value=client)

        admin_delete._delete_top_level_docs("uid123", dry_run=True)

        billing_ref.delete.assert_not_called()
        rate_limit_ref.delete.assert_not_called()


# ── _delete_auth_user ─────────────────────────────────────────────────────────

class TestDeleteAuthUser:
    def test_deletes_existing_auth_user(self, mocker: MockerFixture) -> None:
        mocker.patch("scripts.admin_delete.firebase_auth.get_user", return_value=MagicMock())
        mock_delete = mocker.patch("scripts.admin_delete.firebase_auth.delete_user")

        admin_delete._delete_auth_user("uid123", dry_run=False)

        mock_delete.assert_called_once_with("uid123")

    def test_skips_missing_auth_user(self, mocker: MockerFixture) -> None:
        import firebase_admin.auth as fa

        mocker.patch(
            "scripts.admin_delete.firebase_auth.get_user",
            side_effect=fa.UserNotFoundError("not found"),
        )
        mock_delete = mocker.patch("scripts.admin_delete.firebase_auth.delete_user")

        admin_delete._delete_auth_user("uid123", dry_run=False)

        mock_delete.assert_not_called()

    def test_dry_run_does_not_call_delete(self, mocker: MockerFixture) -> None:
        mocker.patch("scripts.admin_delete.firebase_auth.get_user", return_value=MagicMock())
        mock_delete = mocker.patch("scripts.admin_delete.firebase_auth.delete_user")

        admin_delete._delete_auth_user("uid123", dry_run=True)

        mock_delete.assert_not_called()


# ── _delete_extra_subcollections ──────────────────────────────────────────────

class TestDeleteExtraSubcollections:
    def test_deletes_badges_and_streak_docs(self, mocker: MockerFixture) -> None:
        doc1 = _make_doc_snap()
        doc1.reference = MagicMock()

        batch = MagicMock()
        client = MagicMock()
        client.batch.return_value = batch

        # stream() is called once per subcollection (badges, streak) — use side_effect
        # so each call gets a fresh iterator rather than a shared exhausted one
        client.collection.return_value.document.return_value.collection.return_value.stream.side_effect = [
            iter([doc1]),
            iter([doc1]),
        ]

        mocker.patch("scripts.admin_delete.get_firestore", return_value=client)

        asyncio.run(admin_delete._delete_extra_subcollections("uid123", dry_run=False))

        # batch.delete called once per subcollection (badges + streak)
        assert batch.delete.call_count == 2
        assert batch.commit.call_count == 2

    def test_skips_empty_subcollections(self, mocker: MockerFixture) -> None:
        batch = MagicMock()
        client = MagicMock()
        client.batch.return_value = batch
        client.collection.return_value.document.return_value.collection.return_value.stream.return_value = iter([])

        mocker.patch("scripts.admin_delete.get_firestore", return_value=client)

        asyncio.run(admin_delete._delete_extra_subcollections("uid123", dry_run=False))

        batch.delete.assert_not_called()
        batch.commit.assert_not_called()

    def test_dry_run_does_not_commit(self, mocker: MockerFixture) -> None:
        doc1 = _make_doc_snap()
        batch = MagicMock()
        client = MagicMock()
        client.batch.return_value = batch
        client.collection.return_value.document.return_value.collection.return_value.stream.return_value = iter([doc1])

        mocker.patch("scripts.admin_delete.get_firestore", return_value=client)

        asyncio.run(admin_delete._delete_extra_subcollections("uid123", dry_run=True))

        batch.commit.assert_not_called()


# ── run (integration of all steps) ───────────────────────────────────────────

class TestRun:
    def test_run_calls_all_steps(self, mocker: MockerFixture) -> None:
        mock_delete_data = mocker.patch(
            "scripts.admin_delete.delete_account_data",
            new_callable=AsyncMock,
        )
        mock_extra = mocker.patch(
            "scripts.admin_delete._delete_extra_subcollections",
            new_callable=AsyncMock,
        )
        mock_top = mocker.patch("scripts.admin_delete._delete_top_level_docs")
        mock_auth = mocker.patch("scripts.admin_delete._delete_auth_user")

        asyncio.run(admin_delete.run("uid123", dry_run=False))

        mock_delete_data.assert_awaited_once_with("uid123")
        mock_extra.assert_awaited_once_with("uid123", dry_run=False)
        mock_top.assert_called_once_with("uid123", dry_run=False)
        mock_auth.assert_called_once_with("uid123", dry_run=False)

    def test_dry_run_calls_report_only(self, mocker: MockerFixture) -> None:
        mock_report = mocker.patch("scripts.admin_delete._dry_run_report")
        mock_delete_data = mocker.patch(
            "scripts.admin_delete.delete_account_data",
            new_callable=AsyncMock,
        )

        asyncio.run(admin_delete.run("uid123", dry_run=True))

        mock_report.assert_called_once_with("uid123")
        mock_delete_data.assert_not_called()

    def test_run_aborts_on_delete_account_data_failure(self, mocker: MockerFixture) -> None:
        from app.core.exceptions import FirestoreServiceError

        mocker.patch(
            "scripts.admin_delete.delete_account_data",
            new_callable=AsyncMock,
            side_effect=FirestoreServiceError("boom"),
        )
        mock_auth = mocker.patch("scripts.admin_delete._delete_auth_user")

        with pytest.raises(FirestoreServiceError):
            asyncio.run(admin_delete.run("uid123", dry_run=False))

        # Auth step must NOT run if Firestore deletion failed
        mock_auth.assert_not_called()
