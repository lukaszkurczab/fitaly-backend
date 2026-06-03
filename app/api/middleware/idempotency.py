"""Bearer-namespaced idempotency cache for AI endpoints.

Caches responses keyed by (bearer token digest, X-Idempotency-Key) for 90
seconds. A second request with the same namespace and key returns the cached
response without re-running the AI pipeline or deducting credits again.
"""

import hashlib
import json
import threading
from typing import Any, AsyncIterator, cast

from cachetools import TTLCache
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

_CACHE_LOCK = threading.Lock()
# 50 000 unique (user, key) pairs, 90-second TTL
_idempotency_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=50_000, ttl=90)

# Only cache these path prefixes
_IDEMPOTENT_PATHS = {
    "/api/v2/ai/chat/runs",
    "/api/v1/ai/photo/analyze",
    "/api/v1/ai/text-meal/analyze",
}


def _auth_namespace(request: Request) -> str:
    authorization = (request.headers.get("Authorization") or "").strip()
    scheme, separator, token = authorization.partition(" ")
    if separator and scheme.lower() == "bearer" and token.strip():
        digest = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
        return f"bearer:{digest}"
    return "anonymous"


def _cache_key(request: Request, idem_key: str) -> str:
    return f"{_auth_namespace(request)}:{idem_key.strip()}"


class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method != "POST" or request.url.path not in _IDEMPOTENT_PATHS:
            return await call_next(request)

        idem_key = request.headers.get("X-Idempotency-Key")
        if not idem_key:
            return await call_next(request)

        cache_key = _cache_key(request, idem_key)

        with _CACHE_LOCK:
            cached = _idempotency_cache.get(cache_key)

        if cached is not None:
            return JSONResponse(
                content=cached,
                status_code=200,
                headers={"X-Idempotency-Replayed": "true"},
            )

        response = await call_next(request)

        # Only cache successful AI responses
        if response.status_code == 200:
            body_bytes = b""
            try:
                body_content = getattr(response, "body", None)
                if isinstance(body_content, (bytes, bytearray)):
                    body_bytes = bytes(body_content)
                else:
                    body_iterator = getattr(response, "body_iterator", None)
                    if body_iterator is None:
                        return response
                    async for chunk in cast(AsyncIterator[bytes], body_iterator):
                        body_bytes += chunk
                body = json.loads(body_bytes)
                with _CACHE_LOCK:
                    _idempotency_cache[cache_key] = body
                return JSONResponse(content=body, status_code=200, headers=dict(response.headers))
            except Exception:
                # If caching fails, return original response unchanged
                return Response(
                    content=body_bytes,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type,
                    background=response.background,
                )

        return response
