from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from app.core.exceptions import OpenAIServiceError
from app.core.openai_client import OpenAIClient, _normalize_openai_json_schema
from app.domain.chat.generator import _StructuredAnalyticalAnswerDto
from app.schemas.ai_chat.planner import PlannerResultDto


@dataclass
class _Message:
    content: str


@dataclass
class _Choice:
    message: _Message


@dataclass
class _Usage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass
class _Response:
    choices: list[_Choice]
    usage: _Usage


class _BadRequest(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class _FakeCompletions:
    def __init__(self, calls: list[dict[str, Any]], *, mode: str) -> None:
        self.calls = calls
        self.mode = mode

    async def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)

        strict = bool(kwargs["response_format"]["json_schema"]["strict"])
        if self.mode == "strict_fallback" and strict:
            raise _BadRequest(
                "Invalid schema for response_format 'plannerresultdto': additionalProperties"
            )
        if self.mode == "strict_error_only":
            raise _BadRequest("Invalid request payload.")

        schema_name = str(kwargs["response_format"]["json_schema"]["name"])
        if schema_name == "_structuredanalyticalanswerdto":
            payload: dict[str, Any] = {
                "verdict": "Ok.",
                "coverageStatement": "Coverage is medium.",
                "keyObservations": ["Observation."],
                "practicalNextStep": "Do next.",
                "followUpQuestion": None,
            }
        else:
            payload = {
                "taskType": "out_of_scope_refusal",
                "queryUnderstanding": {
                    "requiresUserData": False,
                    "requestedScopeLabel": None,
                    "mixedRequest": False,
                    "topics": ["scope"],
                },
                "capabilities": [],
                "responseMode": "refusal_redirect",
                "needsFollowUp": False,
                "followUpQuestion": None,
            }
        return _Response(
            choices=[_Choice(message=_Message(content=json.dumps(payload)))],
            usage=_Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


class _FakeChat:
    def __init__(self, calls: list[dict[str, Any]], *, mode: str) -> None:
        self.completions = _FakeCompletions(calls, mode=mode)


class _FakeClient:
    def __init__(self, calls: list[dict[str, Any]], *, mode: str) -> None:
        self.chat = _FakeChat(calls, mode=mode)


def test_normalize_openai_json_schema_adds_additional_properties_recursively() -> None:
    raw_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "nested": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                        },
                    }
                },
            },
            "dynamicArgs": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
        },
        "$defs": {
            "Choice": {
                "type": "object",
                "properties": {"label": {"type": "string"}},
            }
        },
        "anyOf": [
            {
                "type": "object",
                "properties": {"value": {"type": "string"}},
            }
        ],
    }

    normalized = _normalize_openai_json_schema(raw_schema)

    assert "additionalProperties" not in raw_schema
    assert normalized["additionalProperties"] is False
    assert normalized["properties"]["nested"]["additionalProperties"] is False
    nested_items = normalized["properties"]["nested"]["properties"]["items"]["items"]
    assert nested_items["additionalProperties"] is False
    assert normalized["$defs"]["Choice"]["additionalProperties"] is False
    assert normalized["anyOf"][0]["additionalProperties"] is False
    assert normalized["properties"]["dynamicArgs"]["additionalProperties"] == {
        "type": "string"
    }


async def test_openai_client_sends_normalized_structured_schema_with_aliases() -> None:
    calls: list[dict[str, Any]] = []
    client = OpenAIClient(client=_FakeClient(calls, mode="success"))

    result = await client.responses_json_with_usage(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": "x"}],
        schema=_StructuredAnalyticalAnswerDto,
        temperature=0.0,
    )

    assert result["data"]["coverageStatement"] == "Coverage is medium."
    assert len(calls) == 1
    schema = calls[0]["response_format"]["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {
        "verdict",
        "coverageStatement",
        "keyObservations",
        "practicalNextStep",
        "followUpQuestion",
    }
    assert "coverage_statement" not in schema["properties"]


async def test_openai_client_falls_back_to_non_strict_json_schema_on_schema_reject() -> None:
    calls: list[dict[str, Any]] = []
    client = OpenAIClient(client=_FakeClient(calls, mode="strict_fallback"))

    result = await client.responses_json_with_usage(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": "x"}],
        schema=PlannerResultDto,
        temperature=0.0,
    )

    assert result["data"]["taskType"] == "out_of_scope_refusal"
    assert result["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    assert len(calls) == 2
    assert calls[0]["response_format"]["json_schema"]["strict"] is True
    assert calls[1]["response_format"]["json_schema"]["strict"] is False


async def test_openai_client_does_not_fallback_for_non_schema_bad_request() -> None:
    calls: list[dict[str, Any]] = []
    client = OpenAIClient(client=_FakeClient(calls, mode="strict_error_only"))

    with pytest.raises(OpenAIServiceError):
        await client.responses_json_with_usage(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": "x"}],
            schema=PlannerResultDto,
            temperature=0.0,
        )

    assert len(calls) == 1
    assert calls[0]["response_format"]["json_schema"]["strict"] is True
