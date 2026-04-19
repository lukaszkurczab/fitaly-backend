from abc import ABC, abstractmethod
from typing import Any

class DomainTool(ABC):
    name: str

    @abstractmethod
    async def execute(self, *, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        ...
