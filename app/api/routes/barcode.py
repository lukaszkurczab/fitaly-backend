from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.schemas.barcode import BarcodeLookupFoundResponse
from app.services.barcode_service import (
    BarcodeInvalidError,
    BarcodeLookupService,
    BarcodeNotFoundError,
    BarcodeProviderError,
    BarcodeProviderTimeoutError,
    get_barcode_lookup_service,
)

router = APIRouter()


def _raise_barcode_http_error(status_code: int, code: str, message: str) -> NoReturn:
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


@router.get("/users/me/barcode/lookup", response_model=BarcodeLookupFoundResponse)
async def lookup_barcode_me(
    barcode: str = Query(...),
    _current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
    barcode_lookup_service: BarcodeLookupService = Depends(get_barcode_lookup_service),
) -> BarcodeLookupFoundResponse:
    try:
        return await barcode_lookup_service.lookup(barcode)
    except BarcodeInvalidError as exc:
        _raise_barcode_http_error(
            status.HTTP_400_BAD_REQUEST,
            "BARCODE_INVALID",
            str(exc),
        )
    except BarcodeNotFoundError as exc:
        _raise_barcode_http_error(
            status.HTTP_404_NOT_FOUND,
            "BARCODE_NOT_FOUND",
            str(exc),
        )
    except BarcodeProviderTimeoutError as exc:
        _raise_barcode_http_error(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "BARCODE_PROVIDER_TIMEOUT",
            str(exc),
        )
    except BarcodeProviderError as exc:
        _raise_barcode_http_error(
            status.HTTP_502_BAD_GATEWAY,
            "BARCODE_PROVIDER_FAILURE",
            str(exc),
        )
