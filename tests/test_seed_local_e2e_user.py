from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest
from pytest_mock import MockerFixture

from scripts import seed_local_e2e_user as seed


def test_profile_document_is_ready_for_login_bootstrap() -> None:
    profile = seed._profile_document("uid-1")

    assert profile["uid"] == "uid-1"
    assert profile["email"] == "e2e@example.com"
    assert profile["profile"]["language"] == "pl"
    assert profile["profile"]["readiness"]["status"] == "ready"
    assert profile["profile"]["nutritionProfile"]["calorieTarget"] == 2200


def test_main_seeds_auth_user_profile_document(
    mocker: MockerFixture,
    capsys: Any,
) -> None:
    document = mocker.Mock()
    collection = mocker.Mock()
    collection.document.return_value = document
    client = mocker.Mock()
    client.collection.return_value = collection
    mocker.patch("scripts.seed_local_e2e_user._seed_auth_user", return_value=("uid-1", "token"))
    mocker.patch("scripts.seed_local_e2e_user._emulator_firestore_client", return_value=client)

    seed.main()

    client.collection.assert_called_once_with("users")
    collection.document.assert_called_once_with("uid-1")
    document.set.assert_called_once_with(seed._profile_document("uid-1"), merge=True)
    assert '"profileDocument": "users/uid-1"' in capsys.readouterr().out


def test_script_file_execution_resolves_backend_imports() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop("FIRESTORE_EMULATOR_HOST", None)
    env.pop("FIREBASE_AUTH_EMULATOR_HOST", None)

    result = subprocess.run(
        [sys.executable, "scripts/seed_local_e2e_user.py"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "ModuleNotFoundError" not in result.stderr
    assert "FIREBASE_AUTH_EMULATOR_HOST must be set" in result.stderr


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
