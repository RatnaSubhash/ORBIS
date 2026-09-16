"""
ORBIS Phase 12.8: FastAPI Gateway & Real-Time IPC Module.
Exposes the AgentOrchestrator and EventBus through REST and WebSocket transports
for future desktop clients.
"""

from core.gateway.app import create_app
from core.gateway.models import (
    ApprovalRequest,
    ApprovalResponse,
    ErrorResponse,
    PendingApprovalDetails,
    TaskEventsResponse,
    TaskStatusResponse,
    TaskSubmitRequest,
    TaskSubmitResponse,
)
from core.gateway.task_manager import ManagedTask, TaskManager
from core.gateway.websocket import WebSocketConnectionConfig, handle_task_websocket

__all__ = [
    "create_app",
    "TaskManager",
    "ManagedTask",
    "TaskSubmitRequest",
    "TaskSubmitResponse",
    "TaskStatusResponse",
    "PendingApprovalDetails",
    "ApprovalRequest",
    "ApprovalResponse",
    "TaskEventsResponse",
    "ErrorResponse",
    "handle_task_websocket",
    "WebSocketConnectionConfig",
]
