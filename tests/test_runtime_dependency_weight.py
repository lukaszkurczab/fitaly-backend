import ast
from pathlib import Path

from app.core.config import Settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
OPTIONAL_ML_REQUIREMENTS = PROJECT_ROOT / "requirements-ml.txt"
RUNTIME_CODE_DIRS = (
    PROJECT_ROOT / "app",
    PROJECT_ROOT / "scripts",
)
OPTIONAL_ML_IMPORTS = {"joblib", "sklearn"}
OPTIONAL_ML_ENV_FIELDS = {
    "AI_GATEWAY_ML_ENABLED",
    "AI_GATEWAY_ML_MODEL_PATH",
    "AI_GATEWAY_ML_THRESHOLD_OFF_TOPIC",
}


def _requirement_names(path: Path) -> set[str]:
    names: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split("==", 1)[0].split(">=", 1)[0].split("[", 1)[0]
        names.add(name)
    return names


def _python_files() -> list[Path]:
    files: list[Path] = []
    for directory in RUNTIME_CODE_DIRS:
        files.extend(directory.rglob("*.py"))
    return files


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_runtime_requirements_exclude_optional_ml_dependencies() -> None:
    runtime_names = _requirement_names(RUNTIME_REQUIREMENTS)
    optional_ml_names = _requirement_names(OPTIONAL_ML_REQUIREMENTS)

    assert "scikit-learn" not in runtime_names
    assert "joblib" not in runtime_names
    assert optional_ml_names == {"scikit-learn", "joblib"}


def test_runtime_code_does_not_import_optional_ml_dependencies() -> None:
    offenders = {
        str(path.relative_to(PROJECT_ROOT)): sorted(_import_roots(path) & OPTIONAL_ML_IMPORTS)
        for path in _python_files()
        if _import_roots(path) & OPTIONAL_ML_IMPORTS
    }

    assert offenders == {}


def test_ml_gateway_env_flags_are_not_runtime_settings() -> None:
    assert OPTIONAL_ML_ENV_FIELDS.isdisjoint(Settings.model_fields)
