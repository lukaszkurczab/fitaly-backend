"""Central registry for public API versioning.

Public ownership:
- `v1` is the current canonical public API surface while mobile uses it;
- `v2` is the next foundation/extension surface for new or breaking work;
- pre-launch we do not rely on hidden fallbacks or compatibility layers as a launch strategy;
- unused endpoints/routes should be removed before launch after confirming mobile does not use them.
"""

from app.core.config import settings

CURRENT_API_VERSION = "v1"
CURRENT_API_PREFIX = settings.API_V1_PREFIX

NEXT_API_VERSION = "v2"
NEXT_API_PREFIX = settings.API_V2_PREFIX
