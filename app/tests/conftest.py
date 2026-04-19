from pathlib import Path

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        path = Path(str(item.fspath)).as_posix()
        if "/app/tests/" in path:
            item.add_marker(pytest.mark.ai_v2)
