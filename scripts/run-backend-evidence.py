#!/usr/bin/env python3
"""Run local backend request evidence checks and write sanitized artifacts."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error, request


SENSITIVE_HEADER_NAMES = {"authorization", "cookie", "set-cookie"}
SENSITIVE_KEY_PATTERN = re.compile(
    r"(authorization|cookie|token|secret|password|private.?key|email)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EvidenceCheck:
    name: str
    method: str
    path: str
    expected_statuses: tuple[int, ...]
    expected_app_behavior: str
    body: dict[str, Any] | None = None
    headers: dict[str, str] | None = None
    forbidden_response_headers: tuple[str, ...] = ()


@dataclass(frozen=True)
class HttpResult:
    status: int
    latency_ms: int
    headers: dict[str, str]
    payload: Any


@dataclass(frozen=True)
class EndpointInventoryItem:
    method: str
    path: str
    operation_id: str
    tags: tuple[str, ...]
    risk_surface: str
    evidence_lanes: tuple[str, ...]


DEFAULT_CHECKS: tuple[EvidenceCheck, ...] = (
    EvidenceCheck(
        name="health",
        method="GET",
        path="/api/v1/health",
        expected_statuses=(200,),
        expected_app_behavior=(
            "Lightweight liveness returns 200 JSON without requiring Firebase, "
            "OpenAI, RevenueCat, or authenticated user state."
        ),
    ),
    EvidenceCheck(
        name="version",
        method="GET",
        path="/api/v1/version",
        expected_statuses=(200,),
        expected_app_behavior=(
            "Public API metadata returns 200 JSON so clients and operators can "
            "confirm the deployed backend version surface."
        ),
    ),
    EvidenceCheck(
        name="profile_requires_auth",
        method="GET",
        path="/api/v1/users/me/profile",
        expected_statuses=(401,),
        expected_app_behavior=(
            "Profile is protected: missing bearer token is rejected before any "
            "user profile data can be read."
        ),
    ),
    EvidenceCheck(
        name="profile_rejects_malformed_bearer_without_firebase",
        method="GET",
        path="/api/v1/users/me/profile",
        expected_statuses=(401,),
        expected_app_behavior=(
            "Profile rejects a malformed bearer token as invalid credentials "
            "before attempting Firebase verification."
        ),
        headers={"Authorization": "Bearer not-a-jwt"},
    ),
    EvidenceCheck(
        name="ai_credits_requires_auth",
        method="GET",
        path="/api/v1/ai/credits",
        expected_statuses=(401,),
        expected_app_behavior=(
            "AI credits are protected: missing bearer token is rejected before "
            "any user billing or credits state can be read."
        ),
    ),
    EvidenceCheck(
        name="ai_chat_requires_auth_with_idempotency_key",
        method="POST",
        path="/api/v2/ai/chat/runs",
        expected_statuses=(401,),
        expected_app_behavior=(
            "AI Chat v2 remains auth-protected when an idempotency key is "
            "present; unauthorized requests must not be replayed from cache."
        ),
        headers={"X-Idempotency-Key": "local-evidence-ai-auth"},
        forbidden_response_headers=("X-Idempotency-Replayed",),
        body={
            "threadId": "evidence-thread",
            "clientMessageId": "evidence-client-message",
            "message": "Summarize today's macros.",
            "language": "en",
        },
    ),
    EvidenceCheck(
        name="ai_chat_repeated_unauthenticated_idempotency_key_not_replayed",
        method="POST",
        path="/api/v2/ai/chat/runs",
        expected_statuses=(401,),
        expected_app_behavior=(
            "A repeated unauthenticated AI Chat request with the same "
            "idempotency key is rejected again, not served as a replay."
        ),
        headers={"X-Idempotency-Key": "local-evidence-ai-auth"},
        forbidden_response_headers=("X-Idempotency-Replayed",),
        body={
            "threadId": "evidence-thread",
            "clientMessageId": "evidence-client-message",
            "message": "Summarize today's macros.",
            "language": "en",
        },
    ),
    EvidenceCheck(
        name="ai_photo_requires_auth_with_idempotency_key",
        method="POST",
        path="/api/v1/ai/photo/analyze",
        expected_statuses=(401,),
        expected_app_behavior=(
            "AI photo analysis remains auth-protected when an idempotency key "
            "is present."
        ),
        headers={"X-Idempotency-Key": "local-evidence-photo-auth"},
        forbidden_response_headers=("X-Idempotency-Replayed",),
        body={"imageBase64": "local-evidence-image", "lang": "en"},
    ),
    EvidenceCheck(
        name="ai_text_meal_requires_auth_with_idempotency_key",
        method="POST",
        path="/api/v1/ai/text-meal/analyze",
        expected_statuses=(401,),
        expected_app_behavior=(
            "AI text meal analysis remains auth-protected when an idempotency "
            "key is present."
        ),
        headers={"X-Idempotency-Key": "local-evidence-text-auth"},
        forbidden_response_headers=("X-Idempotency-Replayed",),
        body={"payload": {"name": "local evidence meal"}, "lang": "en"},
    ),
    EvidenceCheck(
        name="nutrition_state_requires_auth",
        method="GET",
        path="/api/v2/users/me/state?day=2026-03-23",
        expected_statuses=(401,),
        expected_app_behavior=(
            "Nutrition state is protected: missing bearer token is rejected "
            "before any day-level user state can be read."
        ),
    ),
    EvidenceCheck(
        name="telemetry_disabled_is_explicit",
        method="POST",
        path="/api/v2/telemetry/events/batch",
        expected_statuses=(202, 503),
        expected_app_behavior=(
            "Local telemetry evidence accepts a valid batch when enabled or "
            "returns an explicit disabled/degraded response when telemetry is "
            "off; it must not require live Firestore or silently fall back."
        ),
        body={
            "sessionId": "evidence-session",
            "app": {
                "platform": "ios",
                "appVersion": "0.1.0",
                "build": "local",
            },
            "device": {
                "locale": "en-US",
                "tzOffsetMin": 0,
            },
            "events": [
                {
                    "eventId": "evidence-session-start-1",
                    "name": "session_start",
                    "ts": "2026-03-23T12:00:00Z",
                    "sessionId": "evidence-session",
                    "actor": {"anonymousId": "evidence-anon"},
                    "platform": "ios",
                    "appVersion": "0.1.0",
                    "locale": "en-US",
                    "timezone": "UTC",
                    "tzOffsetMin": 0,
                    "schemaVersion": 2,
                    "props": {"origin": "app_boot"},
                }
            ],
        },
    ),
    EvidenceCheck(
        name="revenuecat_webhook_rejects_invalid_secret",
        method="POST",
        path="/webhooks/revenuecat",
        expected_statuses=(401, 503),
        expected_app_behavior=(
            "RevenueCat webhook rejects an invalid secret when configured, or "
            "returns an explicit unconfigured response locally; it must not "
            "process entitlement state from this request."
        ),
        headers={"Authorization": "Bearer invalid-secret"},
        body={
            "event": {
                "type": "INITIAL_PURCHASE",
                "app_user_id": "local-user",
                "id": "evt-local-1",
                "entitlement_id": "premium",
                "purchased_at_ms": 1772150400000,
                "expiration_at_ms": 1774742400000,
            }
        },
    ),
)


def _normalize_base_url(value: str) -> str:
    return value.rstrip("/")


def _now_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, nested in value.items():
            if SENSITIVE_KEY_PATTERN.search(str(key)):
                sanitized[str(key)] = "[redacted]"
            else:
                sanitized[str(key)] = _sanitize(nested)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str) and SENSITIVE_KEY_PATTERN.search(value):
        return "[redacted]"
    return value


def _sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: "[redacted]" if key.lower() in SENSITIVE_HEADER_NAMES else value
        for key, value in headers.items()
    }


def _parse_payload(raw: bytes) -> Any:
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _risk_surface_for_path(path: str, method: str, tags: tuple[str, ...]) -> str:
    tag_text = " ".join(tags).lower()
    method_upper = method.upper()

    if path in {"/api/v1/health", "/api/v1/version"}:
        return "public_foundation"
    if path == "/api/v1/health/firestore":
        return "manual_deep_readiness"
    if path.startswith("/webhooks/"):
        return "payments_webhook"
    if "/ai/" in path or "ai" in tag_text:
        return "ai_cost_privacy"
    if "telemetry" in path or "telemetry" in tag_text:
        return "telemetry_privacy"
    if "reports/weekly" in path or "weekly" in tag_text:
        return "premium_retention"
    if "reminders" in path or "coach" in path or "habits" in path:
        return "retention_decisioning"
    if "delete" in path or method_upper == "DELETE":
        return "destructive_privacy"
    if "billing" in path or "access" in path or "credits" in path:
        return "billing_access"
    if "meals" in path or "state" in path or "streak" in path:
        return "core_loop_data"
    if "users/me" in path or "usernames" in path:
        return "identity_profile"
    if method_upper in {"POST", "PUT", "PATCH"}:
        return "write_surface"
    return "read_surface"


def _evidence_lanes_for_surface(path: str, method: str, risk_surface: str) -> tuple[str, ...]:
    lanes: list[str] = ["route_inventory"]
    method_upper = method.upper()

    if path not in {"/api/v1/health", "/api/v1/version", "/openapi.json"}:
        lanes.append("auth_boundary")
    if method_upper in {"POST", "PUT", "PATCH", "DELETE"}:
        lanes.extend(["malformed_payload", "valid_payload"])
    if risk_surface in {
        "core_loop_data",
        "identity_profile",
        "destructive_privacy",
        "billing_access",
        "ai_cost_privacy",
        "premium_retention",
        "retention_decisioning",
    }:
        lanes.extend(["user_isolation", "emulator_state"])
    if risk_surface in {"ai_cost_privacy", "billing_access", "payments_webhook"}:
        lanes.append("idempotency_or_replay")
    if risk_surface in {"ai_cost_privacy", "telemetry_privacy", "premium_retention", "retention_decisioning"}:
        lanes.append("kill_switch")
    if risk_surface in {"ai_cost_privacy", "telemetry_privacy", "payments_webhook"}:
        lanes.append("redacted_observability")
    if risk_surface == "manual_deep_readiness":
        lanes.append("manual_only")

    return tuple(dict.fromkeys(lanes))


def _build_endpoint_inventory(openapi_payload: Any) -> list[EndpointInventoryItem]:
    if not isinstance(openapi_payload, dict):
        return []

    paths = openapi_payload.get("paths")
    if not isinstance(paths, dict):
        return []

    inventory: list[EndpointInventoryItem] = []
    for path, path_config in sorted(paths.items()):
        if not isinstance(path, str) or not isinstance(path_config, dict):
            continue
        for method, operation in sorted(path_config.items()):
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_dict = operation if isinstance(operation, dict) else {}
            tags_raw = operation_dict.get("tags")
            tags = tuple(str(tag) for tag in tags_raw) if isinstance(tags_raw, list) else ()
            operation_id_raw = operation_dict.get("operationId")
            operation_id = str(operation_id_raw) if operation_id_raw else ""
            risk_surface = _risk_surface_for_path(path, method, tags)
            inventory.append(
                EndpointInventoryItem(
                    method=method.upper(),
                    path=path,
                    operation_id=operation_id,
                    tags=tags,
                    risk_surface=risk_surface,
                    evidence_lanes=_evidence_lanes_for_surface(
                        path=path,
                        method=method,
                        risk_surface=risk_surface,
                    ),
                )
            )

    return inventory


def _inventory_to_json(inventory: list[EndpointInventoryItem]) -> list[dict[str, Any]]:
    return [
        {
            "method": item.method,
            "path": item.path,
            "operationId": item.operation_id,
            "tags": list(item.tags),
            "riskSurface": item.risk_surface,
            "evidenceLanes": list(item.evidence_lanes),
        }
        for item in inventory
    ]


def _matrix_from_inventory(inventory: list[EndpointInventoryItem]) -> dict[str, Any]:
    by_surface: dict[str, list[dict[str, str]]] = defaultdict(list)
    lane_counts: dict[str, int] = defaultdict(int)

    for item in inventory:
        by_surface[item.risk_surface].append(
            {
                "method": item.method,
                "path": item.path,
                "operationId": item.operation_id,
            }
        )
        for lane in item.evidence_lanes:
            lane_counts[lane] += 1

    return {
        "endpointCount": len(inventory),
        "riskSurfaces": {
            surface: {
                "endpointCount": len(items),
                "endpoints": items,
            }
            for surface, items in sorted(by_surface.items())
        },
        "evidenceLaneCounts": dict(sorted(lane_counts.items())),
        "evidenceLaneDefinitions": {
            "route_inventory": "Endpoint is present in generated OpenAPI inventory.",
            "auth_boundary": "Missing, invalid, and cross-user auth behavior must be explicit.",
            "malformed_payload": "Invalid request payload returns bounded validation errors.",
            "valid_payload": "Happy path or safe no-op request is covered locally.",
            "user_isolation": "User A cannot read/write/delete User B data.",
            "emulator_state": "Stateful Firestore/Auth/Storage evidence runs locally.",
            "idempotency_or_replay": "Replay, duplicate, or webhook event idempotency is covered.",
            "kill_switch": "Disabled feature state is explicit and does not fall back silently.",
            "redacted_observability": "Logs/artifacts redact tokens, secrets, PII, and user-authored content.",
            "manual_only": "Endpoint is intentionally excluded from automated cheap checks.",
        },
    }


def _request_json(*, base_url: str, check: EvidenceCheck, timeout_seconds: float) -> HttpResult:
    url = f"{base_url}{check.path}"
    payload = None
    headers = {"Accept": "application/json"}
    if check.headers:
        headers.update(check.headers)
    if check.body is not None:
        payload = json.dumps(check.body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    http_request = request.Request(
        url=url,
        data=payload,
        method=check.method.upper(),
        headers=headers,
    )

    started = time.perf_counter()
    try:
        with request.urlopen(http_request, timeout=timeout_seconds) as response:  # noqa: S310
            raw = response.read()
            latency_ms = round((time.perf_counter() - started) * 1000)
            return HttpResult(
                status=response.status,
                latency_ms=latency_ms,
                headers=dict(response.headers.items()),
                payload=_parse_payload(raw),
            )
    except error.HTTPError as exc:
        raw = exc.read()
        latency_ms = round((time.perf_counter() - started) * 1000)
        return HttpResult(
            status=exc.code,
            latency_ms=latency_ms,
            headers=dict(exc.headers.items()),
            payload=_parse_payload(raw),
        )


def _request_path(*, base_url: str, path: str, timeout_seconds: float) -> HttpResult:
    check = EvidenceCheck(
        name=_safe_filename(path),
        method="GET",
        path=path,
        expected_statuses=(200,),
        expected_app_behavior=(
            "OpenAPI route inventory is available locally so the hardening "
            "matrix can be generated from the actual FastAPI app."
        ),
    )
    return _request_json(
        base_url=base_url,
        check=check,
        timeout_seconds=timeout_seconds,
    )


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _contract_compatibility(check: EvidenceCheck, result: HttpResult) -> dict[str, Any]:
    status_matches = result.status in check.expected_statuses
    response_header_names = {key.lower() for key in result.headers}
    forbidden_headers_present = [
        header
        for header in check.forbidden_response_headers
        if header.lower() in response_header_names
    ]
    reasons: list[str] = []

    if status_matches:
        reasons.append("actual_status_matches_expected_statuses")
    else:
        reasons.append("actual_status_outside_expected_statuses")

    if forbidden_headers_present:
        reasons.append("forbidden_response_header_present")
    elif check.forbidden_response_headers:
        reasons.append("forbidden_response_headers_absent")

    if result.payload is None:
        reasons.append("empty_response_payload")
    else:
        reasons.append("response_payload_captured")

    return {
        "verdict": (
            "compatible"
            if status_matches and not forbidden_headers_present
            else "incompatible"
        ),
        "statusMatchesExpected": status_matches,
        "forbiddenHeadersPresent": forbidden_headers_present,
        "expectedAppBehavior": check.expected_app_behavior,
        "reasons": reasons,
    }


def _write_check_artifact(
    *,
    output_dir: Path,
    index: int,
    check: EvidenceCheck,
    result: HttpResult,
    passed: bool,
) -> Path:
    artifact_path = output_dir / f"{index:02d}-{_safe_filename(check.name)}.json"
    contract_compatibility = _contract_compatibility(check, result)
    _write_json(
        artifact_path,
        {
            "name": check.name,
            "passed": passed,
            "request": {
                "method": check.method,
                "path": check.path,
                "headers": _sanitize_headers(check.headers or {}),
                "body": _sanitize(check.body),
            },
            "expected": {
                "statuses": list(check.expected_statuses),
                "appFacingBehavior": check.expected_app_behavior,
                "forbiddenResponseHeaders": list(check.forbidden_response_headers),
            },
            "expectedStatuses": list(check.expected_statuses),
            "response": {
                "status": result.status,
                "latencyMs": result.latency_ms,
                "headers": _sanitize_headers(result.headers),
                "payload": _sanitize(result.payload),
            },
            "contractCompatibility": contract_compatibility,
        },
    )
    return artifact_path


def run_checks(
    *,
    base_url: str,
    output_dir: Path,
    timeout_seconds: float,
    include_inventory: bool,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    checks: list[dict[str, Any]] = []
    failed = 0

    inventory: list[EndpointInventoryItem] = []
    if include_inventory:
        openapi_result = _request_path(
            base_url=base_url,
            path="/openapi.json",
            timeout_seconds=timeout_seconds,
        )
        if openapi_result.status == 200:
            inventory = _build_endpoint_inventory(openapi_result.payload)
            _write_json(output_dir / "endpoint-inventory.json", _inventory_to_json(inventory))
            _write_json(output_dir / "hardening-matrix.json", _matrix_from_inventory(inventory))
        else:
            failed += 1
            _write_json(
                output_dir / "endpoint-inventory-error.json",
                {
                    "status": openapi_result.status,
                    "payload": _sanitize(openapi_result.payload),
                },
            )

    for index, check in enumerate(DEFAULT_CHECKS, start=1):
        result = _request_json(
            base_url=base_url,
            check=check,
            timeout_seconds=timeout_seconds,
        )
        contract_compatibility = _contract_compatibility(check, result)
        passed = contract_compatibility["verdict"] == "compatible"
        if not passed:
            failed += 1
        artifact_path = _write_check_artifact(
            output_dir=output_dir,
            index=index,
            check=check,
            result=result,
            passed=passed,
        )
        checks.append(
            {
                "name": check.name,
                "passed": passed,
                "status": result.status,
                "expectedStatuses": list(check.expected_statuses),
                "expectedAppBehavior": check.expected_app_behavior,
                "contractCompatibility": contract_compatibility,
                "latencyMs": result.latency_ms,
                "artifact": str(artifact_path),
            }
        )

    passed_count = sum(1 for check in checks if bool(check["passed"]))
    summary = {
        "baseUrl": base_url,
        "checkedAt": datetime.now(UTC).isoformat(),
        "failed": failed,
        "inventoryEndpointCount": len(inventory),
        "passed": passed_count,
        "checks": checks,
    }
    _write_json(output_dir / "summary.json", summary)

    print(f"Backend evidence written to: {output_dir}")
    print(f"Passed: {summary['passed']} Failed: {summary['failed']}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Local backend base URL.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Artifact output directory. Defaults to evidence/runs/<timestamp>.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=10.0,
        help="HTTP timeout per request.",
    )
    parser.add_argument(
        "--no-inventory",
        action="store_true",
        help="Skip OpenAPI endpoint inventory and hardening matrix artifacts.",
    )
    args = parser.parse_args()

    base_url = _normalize_base_url(args.base_url)
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path("evidence") / "runs" / f"local-public-{_now_stamp()}"
    )

    try:
        return run_checks(
            base_url=base_url,
            output_dir=output_dir,
            timeout_seconds=args.timeout_seconds,
            include_inventory=not args.no_inventory,
        )
    except OSError as exc:
        print(f"Failed to reach backend at {base_url}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
