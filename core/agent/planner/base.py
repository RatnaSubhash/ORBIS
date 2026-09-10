"""
Abstract Base Planner Contract for ORBIS Phase 6.
Defines the planning and controlled replanning interface.
"""

from abc import ABC, abstractmethod
from typing import Optional

from core.agent.models import AgentRequest, TaskPlan
from core.tools.registry import ToolRegistry


class BasePlanner(ABC):
    """Abstract interface for task decomposition and replanning engines."""

    @abstractmethod
    async def plan(self, request: AgentRequest, tool_registry: ToolRegistry) -> TaskPlan:
        """
        Decomposes an AgentRequest into a strongly typed, validated TaskPlan.
        """
        pass

    @abstractmethod
    async def replan(
        self,
        plan: TaskPlan,
        failed_step_id: str,
        error: str,
        tool_registry: ToolRegistry,
    ) -> Optional[TaskPlan]:
        """
        Generates a bounded replacement subplan when an execution step fails.
        Must return None if safe replanning is impossible or limits are exceeded.
        """
        pass
