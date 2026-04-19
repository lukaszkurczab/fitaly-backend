from __future__ import annotations

import json

from app.domain.chat.prompt_composer import PromptComposer


def test_prompt_composer_builds_structured_messages_without_blob_sections() -> None:
    composer = PromptComposer()
    prompt_input = composer.build_prompt_input(
        language="pl",
        response_mode="assessment_plus_guidance",
        grounding={
            "scope": {"type": "today"},
            "nutritionSummary": {"loggingCoverage": {"coverageLevel": "low"}},
        },
        user_message="Ile kalorii mialem dzis?",
    )

    messages = composer.compose_messages(prompt_input)
    assert len(messages) == 3
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "developer"
    assert messages[2]["role"] == "user"
    assert messages[2]["content"] == "Ile kalorii mialem dzis?"

    developer_payload = json.loads(messages[1]["content"])
    assert developer_payload["contract"] == "fitaly_chat_v2_grounded_response"
    assert "grounding" in developer_payload

    # Guard against legacy PROFILE/HISTORY prompt blobs.
    developer_raw = messages[1]["content"]
    assert "PROFILE=" not in developer_raw
    assert "HISTORY=" not in developer_raw
    assert "MEALS_CONTEXT=" not in developer_raw


def test_prompt_composer_refusal_helper() -> None:
    composer = PromptComposer()
    assert "Fitaly" in composer.build_refusal_response("pl")
    assert "Fitaly" in composer.build_refusal_response("en")
