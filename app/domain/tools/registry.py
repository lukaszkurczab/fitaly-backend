from app.domain.tools.base import DomainTool


class ToolRegistry:
    def __init__(self, tools: list[DomainTool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def get(self, name: str) -> DomainTool:
        if name not in self._tools:
            raise ValueError(f"Unknown tool: {name}")
        return self._tools[name]
