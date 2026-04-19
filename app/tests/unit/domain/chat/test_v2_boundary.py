from __future__ import annotations

from pathlib import Path


FORBIDDEN_LEGACY_IMPORT_SNIPPETS = (
    "app.services.ai_context_service",
    "app.services.ai_chat_prompt_service",
    "app.services.conversation_memory_service",
    "app.services.ai_token_budget_service",
    "app.services.openai_service",
    "from app.services import ai_context_service",
    "from app.services import ai_chat_prompt_service",
    "from app.services import conversation_memory_service",
    "from app.services import ai_token_budget_service",
    "from app.services import openai_service",
)


def test_canonical_v2_flow_does_not_import_legacy_ai_context_prompt_modules() -> None:
    repo_root = Path(__file__).resolve().parents[5]
    v2_paths = [
        repo_root / "app" / "api" / "v2" / "endpoints" / "ai_chat.py",
        repo_root / "app" / "api" / "v2" / "deps" / "ai_chat.py",
    ]
    v2_paths.extend((repo_root / "app" / "domain" / "chat").glob("*.py"))

    for file_path in v2_paths:
        if not file_path.exists():
            continue
        content = file_path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_LEGACY_IMPORT_SNIPPETS:
            assert forbidden not in content, f"Forbidden legacy import in {file_path}: {forbidden}"
