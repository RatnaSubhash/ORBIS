"""
Deterministic Mock LLM Provider for ORBIS.
Enables fast, isolated, reliable unit and integration testing without external dependencies.
"""

from typing import Dict, List, Optional

from core.agent.llm.base import BaseLLMProvider
from core.tools.base import BaseTool
from core.types import ToolCall


class MockLLMProvider(BaseLLMProvider):
    """Mock implementation of BaseLLMProvider for deterministic testing."""

    def __init__(
        self,
        default_tool_call: Optional[ToolCall] = None,
        tool_call_map: Optional[Dict[str, Optional[ToolCall]]] = None,
        default_text_response: str = "Mock response",
        should_fail: bool = False,
        failure_error_message: str = "Simulated LLM failure",
    ) -> None:
        self.default_tool_call = default_tool_call
        self.tool_call_map = tool_call_map or {}
        self.default_text_response = default_text_response
        self.should_fail = should_fail
        self.failure_error_message = failure_error_message
        self.call_history: List[Dict[str, str]] = []

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        format: Optional[str] = None,
    ) -> str:
        """Returns deterministic mock text or raises error if configured."""
        self.call_history.append({"action": "generate", "prompt": prompt, "format": format})
        if self.should_fail:
            raise RuntimeError(self.failure_error_message)
        return self.default_text_response

    async def select_tool(
        self, user_request: str, tools: List[BaseTool]
    ) -> Optional[ToolCall]:
        """Returns deterministic ToolCall based on request or default."""
        self.call_history.append({"action": "select_tool", "request": user_request})
        if self.should_fail:
            raise RuntimeError(self.failure_error_message)

        if user_request in self.tool_call_map:
            return self.tool_call_map[user_request]

        return self.default_tool_call
