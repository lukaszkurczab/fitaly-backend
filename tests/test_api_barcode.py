from dataclasses import dataclass
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.barcode import BarcodeIngredient, BarcodeLookupFoundResponse
from app.services.barcode_service import (
    BarcodeInvalidError,
    get_barcode_lookup_service,
    BarcodeNotFoundError,
    BarcodeProviderError,
    BarcodeProviderTimeoutError,
)
from tests.types import AuthHeaders

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_dependency_overrides() -> Generator[None, None, None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@dataclass
class FakeBarcodeLookupService:
    result: BarcodeLookupFoundResponse | None = None
    error: Exception | None = None

    async def lookup(self, barcode: str) -> BarcodeLookupFoundResponse:
        del barcode
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _found_response(*, barcode: str = "5901234123457") -> BarcodeLookupFoundResponse:
    return BarcodeLookupFoundResponse(
        kind="found",
        name="Greek yogurt",
        ingredient=BarcodeIngredient(
            id=barcode,
            name="Greek yogurt",
            amount=100,
            unit="g",
            kcal=120,
            protein=12,
            fat=4,
            carbs=8,
        ),
    )


def test_lookup_barcode_requires_authentication() -> None:
    response = client.get("/api/v1/users/me/barcode/lookup?barcode=5901234123457")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_lookup_barcode_returns_found_payload(
    auth_headers: AuthHeaders,
) -> None:
    fake_service = FakeBarcodeLookupService(result=_found_response())
    app.dependency_overrides[get_barcode_lookup_service] = lambda: fake_service

    response = client.get(
        "/api/v1/users/me/barcode/lookup?barcode=5901234123457",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "kind": "found",
        "name": "Greek yogurt",
        "ingredient": {
            "id": "5901234123457",
            "name": "Greek yogurt",
            "amount": 100,
            "unit": "g",
            "kcal": 120,
            "protein": 12,
            "fat": 4,
            "carbs": 8,
        },
    }


def test_lookup_barcode_returns_404_for_not_found(
    auth_headers: AuthHeaders,
) -> None:
    fake_service = FakeBarcodeLookupService(error=BarcodeNotFoundError("missing"))
    app.dependency_overrides[get_barcode_lookup_service] = lambda: fake_service

    response = client.get(
        "/api/v1/users/me/barcode/lookup?barcode=5901234123457",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "BARCODE_NOT_FOUND",
            "message": "missing",
        }
    }


def test_lookup_barcode_returns_504_for_timeout(
    auth_headers: AuthHeaders,
) -> None:
    fake_service = FakeBarcodeLookupService(
        error=BarcodeProviderTimeoutError("Barcode provider timed out"),
    )
    app.dependency_overrides[get_barcode_lookup_service] = lambda: fake_service

    response = client.get(
        "/api/v1/users/me/barcode/lookup?barcode=5901234123457",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 504
    assert response.json() == {
        "detail": {
            "code": "BARCODE_PROVIDER_TIMEOUT",
            "message": "Barcode provider timed out",
        }
    }


def test_lookup_barcode_returns_502_for_provider_error(
    auth_headers: AuthHeaders,
) -> None:
    fake_service = FakeBarcodeLookupService(
        error=BarcodeProviderError("Barcode provider unavailable"),
    )
    app.dependency_overrides[get_barcode_lookup_service] = lambda: fake_service

    response = client.get(
        "/api/v1/users/me/barcode/lookup?barcode=5901234123457",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": {
            "code": "BARCODE_PROVIDER_FAILURE",
            "message": "Barcode provider unavailable",
        }
    }


def test_lookup_barcode_returns_400_for_invalid_barcode(
    auth_headers: AuthHeaders,
) -> None:
    fake_service = FakeBarcodeLookupService(error=BarcodeInvalidError("invalid"))
    app.dependency_overrides[get_barcode_lookup_service] = lambda: fake_service

    response = client.get(
        "/api/v1/users/me/barcode/lookup?barcode=abc",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": {
            "code": "BARCODE_INVALID",
            "message": "invalid",
        }
    }
