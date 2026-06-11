from dataclasses import dataclass

import httpx
import pytest
from pytest_mock import MockerFixture

from app.schemas.barcode import BarcodeIngredient
from app.services.barcode_service import (
    BarcodeInvalidError,
    BarcodeLookupService,
    BarcodeNotFoundError,
    BarcodeProduct,
    BarcodeProviderError,
    BarcodeProviderTimeoutError,
    OpenFoodFactsBarcodeProvider,
)


@dataclass(frozen=True)
class FakeBarcodeProvider:
    product: BarcodeProduct | None = None
    error: Exception | None = None

    async def lookup(self, barcode: str) -> BarcodeProduct | None:
        del barcode
        if self.error is not None:
            raise self.error
        return self.product


async def test_barcode_lookup_service_returns_found_response() -> None:
    provider = FakeBarcodeProvider(
        product=BarcodeProduct(
            name="Greek yogurt",
            ingredient=BarcodeIngredient(
                id="5901234123457",
                name="Greek yogurt",
                amount=100,
                unit="g",
                kcal=120,
                protein=12,
                fat=4,
                carbs=8,
            ),
        )
    )
    service = BarcodeLookupService(provider)

    result = await service.lookup("5901234123457")

    assert result.kind == "found"
    assert result.name == "Greek yogurt"
    assert result.ingredient.id == "5901234123457"


async def test_barcode_lookup_service_raises_for_missing_product() -> None:
    service = BarcodeLookupService(FakeBarcodeProvider(product=None))

    with pytest.raises(BarcodeNotFoundError):
        await service.lookup("5901234123457")


async def test_barcode_lookup_service_rejects_invalid_barcodes() -> None:
    service = BarcodeLookupService(FakeBarcodeProvider(product=None))

    with pytest.raises(BarcodeInvalidError):
        await service.lookup("abc")


async def test_open_food_facts_provider_normalizes_equivalent_nutrition_fields(
    mocker: MockerFixture,
) -> None:
    response = mocker.Mock()
    response.status_code = 200
    response.json.return_value = {
        "status": 1,
        "product": {
            "product_name": "Sparkling &amp; Water",
            "quantity": "330 ml",
            "serving_size": "330 ml",
            "nutrition_data_per": "100ml",
            "categories_tags": ["beverage"],
            "nutriments": {
                "energy-kcal_100g": "18",
                "proteins": "0.1",
                "fat_100g": 0,
                "carbohydrates_100g": 4.2,
            },
        },
    }

    async_client = mocker.Mock()
    async_client.get = mocker.AsyncMock(return_value=response)
    async_cm = mocker.Mock()
    async_cm.__aenter__ = mocker.AsyncMock(return_value=async_client)
    async_cm.__aexit__ = mocker.AsyncMock(return_value=None)
    mocker.patch("app.services.barcode_service.httpx.AsyncClient", return_value=async_cm)

    provider = OpenFoodFactsBarcodeProvider(timeout_seconds=1)
    result = await provider.lookup("5901234123457")

    assert result is not None
    assert result.name == "Sparkling & Water"
    assert result.ingredient == BarcodeIngredient(
        id="5901234123457",
        name="Sparkling & Water",
        amount=100,
        unit="ml",
        kcal=18,
        protein=0.1,
        fat=0,
        carbs=4.2,
    )


async def test_open_food_facts_provider_maps_provider_timeout(
    mocker: MockerFixture,
) -> None:
    async_client = mocker.Mock()
    async_client.get = mocker.AsyncMock(side_effect=httpx.TimeoutException("timeout"))
    async_cm = mocker.Mock()
    async_cm.__aenter__ = mocker.AsyncMock(return_value=async_client)
    async_cm.__aexit__ = mocker.AsyncMock(return_value=None)
    mocker.patch("app.services.barcode_service.httpx.AsyncClient", return_value=async_cm)

    provider = OpenFoodFactsBarcodeProvider(timeout_seconds=1)

    with pytest.raises(BarcodeProviderTimeoutError):
        await provider.lookup("5901234123457")


async def test_open_food_facts_provider_maps_provider_errors(
    mocker: MockerFixture,
) -> None:
    response = mocker.Mock()
    response.status_code = 502
    response.json.return_value = {"status": 1, "product": {}}

    async_client = mocker.Mock()
    async_client.get = mocker.AsyncMock(return_value=response)
    async_cm = mocker.Mock()
    async_cm.__aenter__ = mocker.AsyncMock(return_value=async_client)
    async_cm.__aexit__ = mocker.AsyncMock(return_value=None)
    mocker.patch("app.services.barcode_service.httpx.AsyncClient", return_value=async_cm)

    provider = OpenFoodFactsBarcodeProvider(timeout_seconds=1)

    with pytest.raises(BarcodeProviderError):
        await provider.lookup("5901234123457")


async def test_open_food_facts_provider_returns_none_for_off_status_zero(
    mocker: MockerFixture,
) -> None:
    response = mocker.Mock()
    response.status_code = 200
    response.json.return_value = {"status": 0}

    async_client = mocker.Mock()
    async_client.get = mocker.AsyncMock(return_value=response)
    async_cm = mocker.Mock()
    async_cm.__aenter__ = mocker.AsyncMock(return_value=async_client)
    async_cm.__aexit__ = mocker.AsyncMock(return_value=None)
    mocker.patch("app.services.barcode_service.httpx.AsyncClient", return_value=async_cm)

    provider = OpenFoodFactsBarcodeProvider(timeout_seconds=1)
    result = await provider.lookup("5901234123457")

    assert result is None
