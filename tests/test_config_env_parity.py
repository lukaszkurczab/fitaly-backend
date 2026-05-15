from pathlib import Path

from app.core.config import Settings


RUNTIME_ONLY_ENV_VARS = {
    "PORT",
    "WEB_CONCURRENCY",
}


def _env_example_vars() -> set[str]:
    env_path = Path(__file__).resolve().parents[1] / ".env.example"
    names: set[str] = set()
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _ = line.split("=", 1)
        names.add(name)
    return names


def test_env_example_only_contains_settings_or_runtime_vars() -> None:
    env_vars = _env_example_vars()
    settings_vars = set(Settings.model_fields)

    assert env_vars - settings_vars - RUNTIME_ONLY_ENV_VARS == set()


def test_all_settings_are_documented_in_env_example() -> None:
    env_vars = _env_example_vars()
    settings_vars = set(Settings.model_fields)

    assert settings_vars - env_vars == set()


def _settings_from_runtime_env_label(environment: str) -> Settings:
    return Settings.model_validate({"ENVIRONMENT": environment})


def test_environment_accepts_railway_environment_labels() -> None:
    assert _settings_from_runtime_env_label("prod").ENVIRONMENT == "production"
    assert _settings_from_runtime_env_label("smoke").ENVIRONMENT == "production"


def test_environment_accepts_dev_alias() -> None:
    assert _settings_from_runtime_env_label("dev").ENVIRONMENT == "development"
