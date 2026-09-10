"""
Tool Registry for ORBIS.
Manages tool registration, lookup, duplicate name prevention, and catalog discovery.
"""

from typing import Dict, List, Optional

from core.tools.base import BaseTool


class ToolRegistry:
    """Registry maintaining available tools for the Agent Brain."""

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """
        Registers a tool.
        Raises ValueError if a tool with the same name is already registered.
        """
        if not tool.name or not tool.name.strip():
            raise ValueError("Tool name must be a non-empty string.")

        name = tool.name.strip()
        if name in self._tools:
            raise ValueError(
                f"Tool with name '{name}' is already registered. "
                f"Existing: {self._tools[name].__class__.__name__}, New: {tool.__class__.__name__}"
            )
        self._tools[name] = tool

    def unregister(self, name: str) -> None:
        """Removes a tool from the registry if present."""
        self._tools.pop(name.strip(), None)

    def get(self, name: str) -> Optional[BaseTool]:
        """Retrieves a tool by name, returning None if not found."""
        return self._tools.get(name.strip())

    def get_or_raise(self, name: str) -> BaseTool:
        """
        Retrieves a tool by name or raises KeyError if not found.
        """
        tool = self.get(name)
        if tool is None:
            raise KeyError(f"Tool '{name}' is not registered.")
        return tool

    def list_tools(self) -> List[BaseTool]:
        """Returns a list of all registered tools."""
        return list(self._tools.values())

    def get_tool_schemas(self) -> List[Dict[str, str]]:
        """
        Returns high-level metadata and schemas for all tools.
        Suitable for informing the LLM of available capabilities.
        """
        schemas = []
        for tool in self._tools.values():
            schemas.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "version": tool.version,
                    "risk_level": tool.risk_level.value,
                    "parameters_schema": tool.get_schema_json(),
                }
            )
        return schemas
