"""
Executor Package for ORBIS Phase 6.
Provides sequential execution, step verification, dynamic context passing, and approval gates.
"""

from core.agent.executor.context import TaskExecutionContext
from core.agent.executor.executor import ApprovalCallback, TaskExecutor

__all__ = [
    "TaskExecutor",
    "TaskExecutionContext",
    "ApprovalCallback",
]
