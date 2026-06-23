from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest
from pytest_mock import MockerFixture

from app.services.food_library_seed_validator import FoodLibrarySeedValidationError
from scripts import seed_ingredient_autocomplete_e2e as seed


def test_global_e2e_seed_records_validate_without_production_approval_claim() -> None:
    report = seed._validate_global_seed_records(seed._global_ingredient_product_documents())

    assert report.hasErrors is False
    assert report.summary.recordCount == 2
    assert report.summary.sourceTypes == {"internal_seed": 2}
    assert report.summary.scopeCounts == {"global_seed": 2}
    assert (
        "Local E2E seed validation is emulator import evidence only, not approved production corpus evidence."
        in report.summary.coverageNotes
    )


def test_main_blocks_invalid_global_seed_before_emulator_writes(
    mocker: MockerFixture,
) -> None:
    records: list[dict[str, Any]] = seed._global_ingredient_product_documents()
    del records[0]["sourceAttribution"]
    mocker.patch(
        "scripts.seed_ingredient_autocomplete_e2e._global_ingredient_product_documents",
        return_value=records,
    )
    post_auth = mocker.patch(
        "scripts.seed_ingredient_autocomplete_e2e._post_auth_emulator"
    )
    firestore_client = mocker.patch(
        "scripts.seed_ingredient_autocomplete_e2e.firestore.Client"
    )

    with pytest.raises(FoodLibrarySeedValidationError) as exc_info:
        seed.main()

    assert "schema_error" in {issue.code for issue in exc_info.value.report.issues}
    post_auth.assert_not_called()
    firestore_client.assert_not_called()


def test_script_file_execution_resolves_backend_imports() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop("FIRESTORE_EMULATOR_HOST", None)
    env.pop("FIREBASE_AUTH_EMULATOR_HOST", None)

    result = subprocess.run(
        [sys.executable, "scripts/seed_ingredient_autocomplete_e2e.py"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "ModuleNotFoundError" not in result.stderr
    assert "FIRESTORE_EMULATOR_HOST must be set" in result.stderr


def test_emulator_hosts_must_be_loopback(mocker: MockerFixture) -> None:
    mocker.patch.dict(
        os.environ,
        {
            "FIREBASE_AUTH_EMULATOR_HOST": "firebase.example.com:9099",
            "FIRESTORE_EMULATOR_HOST": "10.0.0.4:8080",
        },
    )

    with pytest.raises(RuntimeError, match="FIREBASE_AUTH_EMULATOR_HOST"):
        seed._auth_emulator_url("accounts:signUp")

    with pytest.raises(RuntimeError, match="FIRESTORE_EMULATOR_HOST"):
        seed._emulator_firestore_client()


def test_emulator_hosts_accept_loopback(mocker: MockerFixture) -> None:
    mocker.patch.dict(
        os.environ,
        {
            "FIREBASE_AUTH_EMULATOR_HOST": "127.0.0.1:9099",
            "FIRESTORE_EMULATOR_HOST": "localhost:8080",
        },
    )

    assert seed._auth_emulator_url("accounts:signUp").startswith(
        "http://127.0.0.1:9099/"
    )
    assert seed._require_local_emulator_host("FIRESTORE_EMULATOR_HOST") == (
        "localhost:8080"
    )


def test_main_blocks_non_loopback_firestore_before_auth_or_firestore_writes(
    mocker: MockerFixture,
) -> None:
    mocker.patch.dict(
        os.environ,
        {
            "FIREBASE_AUTH_EMULATOR_HOST": "127.0.0.1:9099",
            "FIRESTORE_EMULATOR_HOST": "10.0.0.4:8080",
        },
    )
    post_auth = mocker.patch(
        "scripts.seed_ingredient_autocomplete_e2e._post_auth_emulator"
    )
    firestore_client = mocker.patch(
        "scripts.seed_ingredient_autocomplete_e2e.firestore.Client"
    )

    with pytest.raises(RuntimeError, match="FIRESTORE_EMULATOR_HOST"):
        seed.main()

    post_auth.assert_not_called()
    firestore_client.assert_not_called()


def test_main_blocks_non_loopback_auth_before_auth_or_firestore_writes(
    mocker: MockerFixture,
) -> None:
    mocker.patch.dict(
        os.environ,
        {
            "FIREBASE_AUTH_EMULATOR_HOST": "firebase.example.com:9099",
            "FIRESTORE_EMULATOR_HOST": "127.0.0.1:8080",
        },
    )
    post_auth = mocker.patch(
        "scripts.seed_ingredient_autocomplete_e2e._post_auth_emulator"
    )
    firestore_client = mocker.patch(
        "scripts.seed_ingredient_autocomplete_e2e.firestore.Client"
    )

    with pytest.raises(RuntimeError, match="FIREBASE_AUTH_EMULATOR_HOST"):
        seed.main()

    post_auth.assert_not_called()
    firestore_client.assert_not_called()
