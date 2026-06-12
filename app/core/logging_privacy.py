"""Shared privacy redaction for backend observability surfaces."""

from __future__ import annotations

import re
from typing import Any, cast

from sentry_sdk.types import Event, Hint

RAW_PROVIDER_TEXT_MARKERS = (
    "rawprompt",
    "rawresponse",
    "providermessages",
    "fullpayload",
    "rawimage",
    "rawtooloutput",
    "secret-provider-prompt",
    "secret-provider-response",
    "secret-full-payload",
    "secret-raw-image",
    "secret-tool-dump",
    "secret-debug-log",
)
_SENSITIVE_TEXT_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:https?://(?:firebasestorage\.googleapis\.com|storage\.googleapis\.com|"
            r"[^/\s]+\.storage\.googleapis\.com|storage\.cloud\.google\.com)/[^\s)'\"<>]+|"
            r"gs://[^\s)'\"<>]+)",
            re.IGNORECASE,
        ),
        "[REDACTED_STORAGE_URL]",
    ),
    (
        re.compile(
            r"\b(?:avatars|meals|mealTemplates|myMeals|feedback)(?:/|%2F)"
            r"[A-Za-z0-9._-]+(?:/|%2F)[^\s,'\")\]}<>]+",
            re.IGNORECASE,
        ),
        "[REDACTED_STORAGE_PATH]",
    ),
    (
        re.compile(r"\bhttps?://[^\s?'\"<>)]+(?:/[^\s?'\"<>)]*)?\?[^\s'\"<>)]+"),
        "[REDACTED_URL_QUERY]",
    ),
    (
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
        "[REDACTED_EMAIL]",
    ),
    (
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
        "[REDACTED_BEARER_TOKEN]",
    ),
    (
        re.compile(
            r"\b(?:authorization|auth|cookie|set-cookie)\s*[:=]\s*[^\n,;]+",
            re.IGNORECASE,
        ),
        "[REDACTED_AUTH_FIELD]",
    ),
    (
        re.compile(
            r"\b(?:password|passcode|token|authToken|accessToken|refreshToken|idToken|"
            r"access[_-]?token|refresh[_-]?token|apiKey|api[_-]?key|secret|"
            r"clientSecret|client[_-]?secret)\s*[:=]\s*[^\s,;&]+",
            re.IGNORECASE,
        ),
        "[REDACTED_SECRET_FIELD]",
    ),
    (
        re.compile(r"\bsk-(?:proj-|ant-|live-|test-)?[A-Za-z0-9_-]{12,}\b"),
        "[REDACTED_PROVIDER_SECRET]",
    ),
    (
        re.compile(
            r"\b(?:raw\s*(?:request|response)\s*body|request\s*body|response\s*body|"
            r"raw[_-]?body|requestBody|responseBody|prompt|message|text)\s*[:=]\s*[^\n]+",
            re.IGNORECASE,
        ),
        "[REDACTED_USER_CONTENT]",
    ),
    (
        re.compile(
            r"\b(?:rawPrompt|rawResponse|providerMessages|fullPayload|rawImage|"
            r"rawToolOutput|secret-provider-prompt|secret-provider-response|"
            r"secret-full-payload|secret-raw-image|secret-tool-dump|secret-debug-log)\b",
            re.IGNORECASE,
        ),
        "[REDACTED_RAW_PROVIDER_PAYLOAD]",
    ),
)
_SECRET_KEY_MARKERS = (
    "authorization",
    "auth",
    "cookie",
    "password",
    "token",
    "apikey",
    "api_key",
    "secret",
)
_USER_CONTENT_KEY_MARKERS = (
    "prompt",
    "requestbody",
    "responsebody",
    "rawbody",
    "raw_body",
)
_QUERY_KEY_MARKERS = ("query", "querystring", "query_string")
_PROFILE_NAME_KEYS = ("name", "username", "fullname", "displayname")


def contains_raw_provider_text_marker(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in RAW_PROVIDER_TEXT_MARKERS)


def redact_sensitive_log_text(value: str) -> str:
    redacted = value
    for pattern, replacement in _SENSITIVE_TEXT_REPLACEMENTS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _normalized_key(key: str) -> str:
    return key.replace("-", "").replace("_", "").lower()


def _sensitive_key_replacement(key: str) -> str | None:
    normalized = _normalized_key(key)
    lowered = key.lower()
    if any(marker in normalized for marker in _QUERY_KEY_MARKERS):
        return "[REDACTED_URL_QUERY]"
    if any(marker in normalized for marker in _SECRET_KEY_MARKERS):
        return "[REDACTED_SECRET_FIELD]"
    if "email" in normalized:
        return "[REDACTED_EMAIL]"
    if normalized in _PROFILE_NAME_KEYS:
        return "[REDACTED_PROFILE_FIELD]"
    if any(marker in lowered for marker in _USER_CONTENT_KEY_MARKERS):
        return "[REDACTED_USER_CONTENT]"
    return None


def sanitize_observability_value(value: Any, *, key: str | None = None) -> Any:
    replacement = _sensitive_key_replacement(key) if key is not None else None
    if replacement is not None:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return replacement
        if isinstance(value, list):
            list_value = cast("list[Any]", value)
            return [replacement] * len(list_value)
        if isinstance(value, dict):
            return {"redacted": replacement}

    if isinstance(value, str):
        return redact_sensitive_log_text(value)
    if isinstance(value, dict):
        mapped_value = cast("dict[Any, Any]", value)
        return {
            item_key: sanitize_observability_value(item_value, key=str(item_key))
            for item_key, item_value in mapped_value.items()
        }
    if isinstance(value, list):
        list_value = cast("list[Any]", value)
        return [sanitize_observability_value(item) for item in list_value]
    if isinstance(value, tuple):
        tuple_value = cast("tuple[Any, ...]", value)
        return tuple(sanitize_observability_value(item) for item in tuple_value)
    return value


def sanitize_sentry_event(event: Event, hint: Hint | None = None) -> Event:
    del hint
    return cast(Event, sanitize_observability_value(event))
