"""Middleware that logs basic request metrics and attaches a request ID to logs and responses."""

import time
import uuid

from fastapi import Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.services.error_logger import log_info, log_warning


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response: Response | None = None
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception as exc:
            # Propagate the exception to the global handler but ensure request_id is available.
            raise exc
        finally:
            duration = (time.monotonic() - start) * 1000
            status_code = getattr(response, "status_code", 500)
            log_fn = log_warning if status_code >= 400 else log_info
            log_fn(
                f"{request.method} {request.url.path} → {status_code}",
                request_id=request_id,
                duration_ms=duration,
                path=request.url.path,
                method=request.method,
                status_code=status_code,
            )
        if response is None:
            response = Response(status_code=500)
        response.headers["X-Request-ID"] = request_id
        return response
