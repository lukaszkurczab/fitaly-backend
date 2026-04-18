from _pytest.logging import LogCaptureFixture
from collections.abc import Callable
from typing import TypeAlias

AuthHeaders: TypeAlias = Callable[[str], dict[str, str]]

__all__ = ["AuthHeaders", "LogCaptureFixture"]
