from abc import ABC, abstractmethod
from typing import Any, Dict

class DomainTool(ABC):
    name: str

    @abstractmethod
    async def execute(self, *, user_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
        ...