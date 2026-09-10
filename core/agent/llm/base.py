"""
Abstract Base LLM Provider for ORBIS.
Decouples the Agent Brain from specific model providers (Ollama, cloud adapters, or mocks).
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from core.tools.base import BaseTool
from core.types import ToolCall


class BaseLLMProvider(ABC):
    """Abstract interface for LLM backends."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        format: Optional[str] = None,
    ) -> str:
        """Generates a text completion for a given prompt."""
        pass

    @abstractmethod
    async def select_tool(
        self, user_request: str, tools: List[BaseTool]
    ) -> Optional[ToolCall]:
        """
        Given a user request and available tools, returns the selected ToolCall
        or None if no tool should be invoked.
        """
        pass
