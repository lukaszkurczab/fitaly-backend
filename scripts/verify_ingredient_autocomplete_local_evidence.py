"""Produce local Product/Ingredient autocomplete coverage and latency evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Callable, Sequence, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.api.deps import auth as auth_deps  # noqa: E402
from app.api.routes import food_library as food_library_route  # noqa: E402
from app.api.v2.router import router as api_v2_router  # noqa: E402
from app.schemas.food_library import IngredientProductSearchResponse  # noqa: E402
from app.services import food_library_service  # noqa: E402
from scripts import seed_ingredient_autocomplete_e2e as seed  # noqa: E402


DEFAULT_LATENCY_ITERATIONS = 30
DEFAULT_LATENCY_THRESHOLD_MS = 50.0


class LocalSearchSnapshot:
    def __init__(self, document_id: str, payload: dict[str, Any]) -> None:
        self.id = document_id
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload)


class LocalSearchQuery:
    def __init__(self, snapshots: Sequence[LocalSearchSnapshot]) -> None:
        self._snapshots = list(snapshots)
        self._limit: int | None = None

    def limit(self, count: int) -> "LocalSearchQuery":
        self._limit = count
        return self

    def stream(self) -> list[LocalSearchSnapshot]:
        if self._limit is None:
            return list(self._snapshots)
        return list(self._snapshots[: self._limit])


class LocalSearchCollection:
    def __init__(self, records: Sequence[dict[str, Any]]) -> None:
        self._snapshots = [
            LocalSearchSnapshot(str(record["ingredientProductId"]), dict(record))
            for record in records
        ]

    def where(self, *, filter: Any) -> LocalSearchQuery:
        field_path = str(filter.field_path)
        op_string = str(filter.op_string)
        value = str(filter.value)
        if (
            field_path != food_library_service.SEARCH_INDEX_FIELD
            or op_string != "array_contains"
        ):
            raise ValueError("Local autocomplete evidence supports searchPrefixes only.")
        matches = [
            snapshot
            for snapshot in self._snapshots
            if value in cast(list[str], snapshot.to_dict().get("searchPrefixes") or [])
        ]
        return LocalSearchQuery(matches)


class LocalSearchClient:
    def __init__(self, global_records: Sequence[dict[str, Any]]) -> None:
        self._global_collection = LocalSearchCollection(global_records)

    def collection(self, name: str) -> LocalSearchCollection:
        if name != "ingredientProducts":
            raise ValueError("Local autocomplete evidence does not use user collections.")
        return self._global_collection


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, round((len(ordered) - 1) * percentile)),
    )
    return ordered[index]


def _local_route_app() -> FastAPI:
    app = FastAPI(title="Fitaly local Ingredient autocomplete evidence")
    app.include_router(api_v2_router, prefix="/api/v2")
    return app


def _search_route_once(
    *,
    client: TestClient,
    query: str,
    locale: str,
) -> IngredientProductSearchResponse:
    response = client.get(
        "/api/v2/users/me/ingredient-products/search",
        params={
            "query": query,
            "locale": locale,
            "limit": "8",
            "includeUserScoped": "false",
            "includeGlobal": "true",
        },
        headers={"Authorization": "Bearer local-evidence-user"},
    )
    response.raise_for_status()
    return IngredientProductSearchResponse.model_validate(response.json())


async def build_local_evidence(
    *,
    records: Sequence[dict[str, Any]] | None = None,
    latency_iterations: int = DEFAULT_LATENCY_ITERATIONS,
    latency_threshold_ms: float = DEFAULT_LATENCY_THRESHOLD_MS,
    timer: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    if latency_iterations < 1:
        raise ValueError("latency_iterations must be at least 1")
    if latency_threshold_ms <= 0:
        raise ValueError("latency_threshold_ms must be positive")

    global_records = list(records or seed._global_ingredient_product_documents())
    seed_report = seed._validate_global_seed_records(global_records)
    local_client = LocalSearchClient(global_records)
    original_get_firestore = food_library_service.get_firestore
    original_decode_firebase_token = auth_deps.decode_firebase_token
    original_food_library_enabled = food_library_route.settings.FOOD_LIBRARY_ENABLED
    food_library_service.get_firestore = lambda: local_client  # type: ignore[assignment]
    auth_deps.decode_firebase_token = lambda token: {"uid": token.strip()}  # type: ignore[assignment]
    food_library_route.settings.FOOD_LIBRARY_ENABLED = True
    try:
        route_client = TestClient(_local_route_app())
        coverage_cases = [
            {
                "language": "pl",
                "locale": "pl-PL",
                "query": "Owies",
                "expectedId": "e2e-local-oats",
            },
            {
                "language": "pl",
                "locale": "pl-PL",
                "query": "Ostrzeżenie",
                "expectedId": "e2e-warning-oats",
            },
            {
                "language": "en",
                "locale": "en-US",
                "query": "Oats",
                "expectedId": "e2e-local-oats-en",
            },
        ]
        coverage_results: list[dict[str, Any]] = []
        for case in coverage_cases:
            response = _search_route_once(
                client=route_client,
                query=cast(str, case["query"]),
                locale=cast(str, case["locale"]),
            )
            matched_ids = [item.ingredientProductId for item in response.items]
            expected_id = cast(str, case["expectedId"])
            coverage_results.append(
                {
                    **case,
                    "normalizedQuery": response.queryEcho.normalizedQuery,
                    "matchedIds": matched_ids,
                    "passed": expected_id in matched_ids,
                }
            )

        latency_measurements_ms: list[float] = []
        latency_skipped_reason: str | None = None
        if not all(result["passed"] for result in coverage_results):
            latency_skipped_reason = "coverage_failed"
        else:
            for _ in range(latency_iterations):
                started_at = timer()
                response = _search_route_once(
                    client=route_client,
                    query="Oats",
                    locale="en-US",
                )
                elapsed_ms = (timer() - started_at) * 1000
                if "e2e-local-oats-en" not in [
                    item.ingredientProductId for item in response.items
                ]:
                    latency_skipped_reason = "latency_probe_missed_expected_record"
                    latency_measurements_ms = []
                    break
                latency_measurements_ms.append(elapsed_ms)
    finally:
        food_library_service.get_firestore = original_get_firestore  # type: ignore[assignment]
        auth_deps.decode_firebase_token = original_decode_firebase_token  # type: ignore[assignment]
        food_library_route.settings.FOOD_LIBRARY_ENABLED = original_food_library_enabled

    latency_p50_ms = (
        statistics.median(latency_measurements_ms) if latency_measurements_ms else None
    )
    latency_p95_ms = (
        _percentile(latency_measurements_ms, 0.95)
        if latency_measurements_ms
        else None
    )
    latency_max_ms = max(latency_measurements_ms) if latency_measurements_ms else None
    latency_passed = (
        latency_p95_ms is not None
        and latency_skipped_reason is None
        and latency_p95_ms <= latency_threshold_ms
    )
    return {
        "evidenceKind": "ingredient_autocomplete_local_api_route_evidence_v1",
        "dataSource": "local_in_process_api_v2_router_with_in_memory_seed_records",
        "productionCorpusApproved": False,
        "seedValidation": seed_report.summary.model_dump(mode="json"),
        "coverage": {
            "requiredLanguages": ["pl", "en"],
            "passed": all(result["passed"] for result in coverage_results),
            "cases": coverage_results,
        },
        "latency": {
            "probeQuery": "Oats",
            "iterations": latency_iterations,
            "thresholdMs": latency_threshold_ms,
            "p50Ms": round(latency_p50_ms, 3) if latency_p50_ms is not None else None,
            "p95Ms": round(latency_p95_ms, 3) if latency_p95_ms is not None else None,
            "maxMs": round(latency_max_ms, 3) if latency_max_ms is not None else None,
            "passed": latency_passed,
            "skippedReason": latency_skipped_reason,
        },
        "limitations": [
            "Local in-process API v2 router evidence only; no production corpus approval.",
            "Verifier mounts a local FastAPI app and does not run production app startup or middleware.",
            "Firebase auth token decoding is locally patched; no provider-backed auth claim.",
            "No provider, production Firebase, deployed backend, network, or physical-device latency evidence.",
            "PL/EN technical query hits do not replace owner nutrition-quality review or rollout approval.",
        ],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify local Ingredient autocomplete PL/EN coverage and latency evidence.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_LATENCY_ITERATIONS,
        help="Number of local service latency probes to run.",
    )
    parser.add_argument(
        "--threshold-ms",
        type=float,
        default=DEFAULT_LATENCY_THRESHOLD_MS,
        help="Maximum allowed p95 local service latency in milliseconds.",
    )
    return parser.parse_args()


async def _async_main() -> int:
    args = _parse_args()
    evidence = await build_local_evidence(
        latency_iterations=args.iterations,
        latency_threshold_ms=args.threshold_ms,
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if evidence["coverage"]["passed"] and evidence["latency"]["passed"] else 1


def main() -> None:
    raise SystemExit(asyncio.run(_async_main()))


if __name__ == "__main__":
    main()
