from fastapi import APIRouter

from app.api.v1.router import router as v1_router
from app.api.v2.router import router as v2_router
from app.core.api_version import CURRENT_API_PREFIX, NEXT_API_PREFIX

api_router = APIRouter()

# Public API ownership rule:
# - CURRENT_API_PREFIX is the current canonical public API surface while mobile uses it.
# - NEXT_API_PREFIX is the next foundation/extension surface for new or breaking work.
# - Pre-launch we do not ship hidden fallbacks or a compatibility layer as launch strategy.
api_router.include_router(v1_router, prefix=CURRENT_API_PREFIX)

# Remove unused routes before launch once mobile confirms they are not used.
# Add new/changed endpoints in app/api/v2/endpoints/* and include them in v2/router.py.
api_router.include_router(v2_router, prefix=NEXT_API_PREFIX)
