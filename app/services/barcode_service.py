from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import logging
import re
from typing import Protocol, cast

import httpx

from app.core.coercion import coerce_float, coerce_optional_str
from app.schemas.barcode import BarcodeIngredient, BarcodeLookupFoundResponse

logger = logging.getLogger(__name__)

_BARCODE_RE = re.compile(r"^(?:\d{8}|\d{12}|\d{13})$")


class BarcodeInvalidError(ValueError):
    pass


class BarcodeNotFoundError(LookupError):
    pass


class BarcodeProviderTimeoutError(TimeoutError):
    pass


class BarcodeProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class BarcodeProduct:
    name: str
    ingredient: BarcodeIngredient


class BarcodeProvider(Protocol):
    async def lookup(self, barcode: str) -> BarcodeProduct | None: ...


def _as_object_map(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    raw_map = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for raw_key, raw_item in raw_map.items():
        if isinstance(raw_key, str):
            result[raw_key] = raw_item
    return result


def _normalize_barcode(barcode: str) -> str:
    candidate = barcode.strip()
    if not _BARCODE_RE.match(candidate):
        raise BarcodeInvalidError("Barcode must be 8, 12, or 13 digits")
    return candidate


def _to_number(value: object) -> float:
    return coerce_float(value)


def _is_beverage(product: dict[str, object]) -> bool:
    quantity = coerce_optional_str(product.get("quantity"))
    serving_size = coerce_optional_str(product.get("serving_size"))
    nutrition_data_per = coerce_optional_str(product.get("nutrition_data_per"))
    categories_raw = product.get("categories_tags")
    categories: list[object] = (
        cast(list[object], categories_raw) if isinstance(categories_raw, list) else []
    )

    if nutrition_data_per and "100ml" in nutrition_data_per.lower():
        return True

    if quantity and "ml" in quantity.lower():
        return True

    if serving_size and "ml" in serving_size.lower():
        return True

    for category in categories:
        if isinstance(category, str) and re.search(
            r"beverage|drink|napoje|napój", category, re.IGNORECASE
        ):
            return True

    return False


def _normalize_off_product(barcode: str, product: dict[str, object]) -> BarcodeProduct:
    nutriments_raw = product.get("nutriments")
    nutriments: dict[str, object] = (
        cast(dict[str, object], nutriments_raw)
        if isinstance(nutriments_raw, dict)
        else cast(dict[str, object], {})
    )
    decoded_name = unescape(coerce_optional_str(product.get("product_name")) or "").strip()
    name = decoded_name or f"Product {barcode}"
    unit = "ml" if _is_beverage(product) else "g"

    ingredient = BarcodeIngredient(
        id=barcode,
        name=name,
        amount=100,
        unit=unit,
        kcal=_to_number(
            nutriments.get("energy-kcal_100g")
            or nutriments.get("energy-kcal")
            or nutriments.get("energy_100g")
            or nutriments.get("energy")
        ),
        protein=_to_number(nutriments.get("proteins_100g") or nutriments.get("proteins")),
        fat=_to_number(nutriments.get("fat_100g") or nutriments.get("fat")),
        carbs=_to_number(
            nutriments.get("carbohydrates_100g") or nutriments.get("carbohydrates")
        ),
    )
    return BarcodeProduct(name=name, ingredient=ingredient)


class OpenFoodFactsBarcodeProvider:
    def __init__(self, *, timeout_seconds: float = 8.0) -> None:
        self._timeout_seconds = timeout_seconds

    async def lookup(self, barcode: str) -> BarcodeProduct | None:
        safe_barcode = _normalize_barcode(barcode)
        url = (
            "https://world.openfoodfacts.org/api/v2/product/"
            f"{safe_barcode}.json"
        )

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(url, headers={"Accept": "application/json"})
        except httpx.TimeoutException as exc:
            logger.warning("barcode_provider_timeout", extra={"barcode": safe_barcode})
            raise BarcodeProviderTimeoutError("Barcode provider timed out") from exc
        except httpx.HTTPError as exc:
            logger.warning("barcode_provider_unavailable", extra={"barcode": safe_barcode})
            raise BarcodeProviderError("Barcode provider unavailable") from exc

        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            logger.warning(
                "barcode_provider_http_error",
                extra={"barcode": safe_barcode, "status_code": response.status_code},
            )
            raise BarcodeProviderError("Barcode provider unavailable")

        try:
            payload_raw = response.json()
        except ValueError as exc:
            logger.warning("barcode_provider_invalid_response", extra={"barcode": safe_barcode})
            raise BarcodeProviderError("Barcode provider returned invalid JSON") from exc

        payload = _as_object_map(payload_raw)
        if payload is None:
            logger.warning("barcode_provider_invalid_response", extra={"barcode": safe_barcode})
            raise BarcodeProviderError("Barcode provider returned invalid response")

        status_value = payload.get("status")
        if status_value == 0:
            return None

        product_raw = payload.get("product")
        product = _as_object_map(product_raw) if product_raw is not None else None
        if product is None:
            logger.warning("barcode_provider_invalid_response", extra={"barcode": safe_barcode})
            raise BarcodeProviderError("Barcode provider returned invalid response")

        return _normalize_off_product(safe_barcode, product)


class BarcodeLookupService:
    def __init__(self, provider: BarcodeProvider | None = None) -> None:
        self._provider = provider or OpenFoodFactsBarcodeProvider()

    async def lookup(self, barcode: str) -> BarcodeLookupFoundResponse:
        safe_barcode = _normalize_barcode(barcode)
        product = await self._provider.lookup(safe_barcode)
        if product is None:
            raise BarcodeNotFoundError("Barcode product not found")

        return BarcodeLookupFoundResponse(
            kind="found",
            name=product.name,
            ingredient=product.ingredient,
        )


_barcode_lookup_service = BarcodeLookupService()


def get_barcode_lookup_service() -> BarcodeLookupService:
    return _barcode_lookup_service


async def lookup_barcode_product(barcode: str) -> BarcodeLookupFoundResponse:
    return await _barcode_lookup_service.lookup(barcode)
