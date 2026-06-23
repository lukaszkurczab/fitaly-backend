from __future__ import annotations

import asyncio

import pytest

from scripts import seed_ingredient_autocomplete_e2e as seed
from scripts import verify_ingredient_autocomplete_local_evidence as evidence


def test_local_evidence_reports_pl_en_coverage_and_latency() -> None:
    report = asyncio.run(
        evidence.build_local_evidence(
            latency_iterations=3,
            latency_threshold_ms=1000,
        )
    )

    assert report["productionCorpusApproved"] is False
    assert (
        report["dataSource"]
        == "local_in_process_api_v2_router_with_in_memory_seed_records"
    )
    assert report["coverage"]["passed"] is True
    cases = {case["query"]: case for case in report["coverage"]["cases"]}
    assert cases["Owies"]["matchedIds"][0] == "e2e-local-oats"
    assert "e2e-warning-oats" in cases["Ostrzeżenie"]["matchedIds"]
    assert cases["Oats"]["matchedIds"] == ["e2e-local-oats-en"]
    assert report["latency"]["iterations"] == 3
    assert report["latency"]["passed"] is True
    limitations = " ".join(report["limitations"])
    assert "does not run production app startup" in limitations
    assert "Firebase auth token decoding is locally patched" in limitations
    assert "No provider" in limitations


def test_local_evidence_fails_when_en_seed_record_is_missing() -> None:
    records = [
        record
        for record in seed._global_ingredient_product_documents()
        if record["ingredientProductId"] != "e2e-local-oats-en"
    ]

    report = asyncio.run(
        evidence.build_local_evidence(
            records=records,
            latency_iterations=1,
            latency_threshold_ms=1000,
        )
    )

    cases = {case["query"]: case for case in report["coverage"]["cases"]}
    assert report["coverage"]["passed"] is False
    assert report["latency"]["passed"] is False
    assert report["latency"]["skippedReason"] == "coverage_failed"
    assert cases["Oats"]["matchedIds"] == []
    assert cases["Oats"]["passed"] is False


def test_local_evidence_rejects_invalid_latency_arguments() -> None:
    with pytest.raises(ValueError, match="latency_iterations"):
        asyncio.run(evidence.build_local_evidence(latency_iterations=0))

    with pytest.raises(ValueError, match="latency_threshold_ms"):
        asyncio.run(evidence.build_local_evidence(latency_threshold_ms=0))
