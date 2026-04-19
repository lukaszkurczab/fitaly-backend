from __future__ import annotations

import json

from app.schemas.ai_chat.prompt import PromptBuildInputDto

SYSTEM_PROMPT = (
    "You are the Fitaly nutrition assistant. "
    "Use only grounded backend-provided facts. "
    "Do not invent missing data. "
    "If coverage is low or missing, say that clearly. "
    "Allowed scope: Fitaly app usage, user in-app nutrition data, meals, calories, macros, healthy eating. "
    "Never provide medical diagnosis or treatment. "
    "Keep answers concise, practical, and transparent about data quality."
)


class PromptComposer:
    def build_prompt_input(
        self,
        *,
        language: str,
        response_mode: str,
        grounding: dict,
        user_message: str,
    ) -> dict:
        payload = {
            "language": language if language in {"pl", "en"} else "pl",
            "responseMode": response_mode,
            "grounding": grounding,
            "userMessage": user_message,
        }
        dto = PromptBuildInputDto.model_validate(payload)
        return dto.model_dump(by_alias=True, exclude_none=True)

    def compose_messages(self, prompt_input: dict) -> list[dict[str, str]]:
        dto = PromptBuildInputDto.model_validate(prompt_input)
        grounding_payload = dto.grounding.model_dump(by_alias=True, exclude_none=True)
        developer_payload = {
            "contract": "fitaly_chat_v2_grounded_response",
            "language": dto.language,
            "responseMode": dto.response_mode,
            "grounding": grounding_payload,
            "rules": [
                "Do not use facts outside grounding payload.",
                "If grounding coverage is low, mention uncertainty.",
                "If planner marked out_of_scope, refuse and redirect briefly.",
                "Do not output hidden reasoning or internal tool names unless needed for transparency.",
            ],
        }

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "developer",
                "content": json.dumps(developer_payload, ensure_ascii=False),
            },
            {"role": "user", "content": dto.user_message},
        ]

    def build_refusal_response(self, language: str) -> str:
        if language == "en":
            return (
                "I can only help with Fitaly app usage, your in-app data, and nutrition topics."
            )
        return (
            "Mogę pomóc tylko w kwestiach Fitaly, Twoich danych w aplikacji oraz tematów żywieniowych."
        )
