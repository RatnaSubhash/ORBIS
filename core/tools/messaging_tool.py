"""
Messaging Tool for ORBIS.
Supports high-side-effect communication (messaging) under strictly enforced
RiskLevel.HIGH safety policy. Every messaging operation requires explicit
human approval before external transmission can proceed.
"""

from abc import ABC, abstractmethod
import logging
from typing import Any, Dict, List, Literal, Optional, Type
from pydantic import BaseModel, Field

from core.tools.base import BaseTool
from core.tools.secret_redaction import redact_secrets
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport

logger = logging.getLogger("orbis.tools.messaging")


class MessagingParameters(BaseModel):
    """Parameters schema for safe, approval-gated messaging."""
    recipient: str = Field(
        ...,
        min_length=1,
        description="Target recipient contact identifier, name, or phone number.",
    )
    message: str = Field(
        ...,
        min_length=1,
        description="The content of the message to transmit.",
    )
    channel: Literal["whatsapp", "email", "sms"] = Field(
        default="whatsapp",
        description="Communication channel ('whatsapp', 'email', or 'sms').",
    )


class BaseMessageDispatcher(ABC):
    """Abstract interface for transmitting messages."""

    @abstractmethod
    def dispatch(self, channel: str, recipient: str, message: str) -> bool:
        pass


class MockMessageDispatcher(BaseMessageDispatcher):
    """In-memory dispatcher recording message sends for testing and dry-run execution."""

    def __init__(self) -> None:
        self.sent_messages: List[Dict[str, str]] = []

    def dispatch(self, channel: str, recipient: str, message: str) -> bool:
        self.sent_messages.append({
            "channel": channel,
            "recipient": recipient,
            "message": message,
        })
        return True


class MessagingTool(BaseTool):
    """
    High-side-effect messaging tool.
    Marked RiskLevel.HIGH: cannot execute without explicit human authorization.
    """

    name: str = "messaging"
    description: str = (
        "Sends external messages via WhatsApp, Email, or SMS. "
        "Strictly gated by high-risk human approval policy."
    )
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.HIGH
    parameters_schema: Type[BaseModel] = MessagingParameters

    def __init__(self, dispatcher: Optional[BaseMessageDispatcher] = None) -> None:
        self.dispatcher = dispatcher or MockMessageDispatcher()

    async def execute(
        self, params: BaseModel, context: ExecutionContext
    ) -> ToolResult:
        if not isinstance(params, MessagingParameters):
            params = self.validate_parameters(params.model_dump())

        # Secret redaction on outgoing message text
        clean_msg = redact_secrets(params.message)

        try:
            success = self.dispatcher.dispatch(
                channel=params.channel,
                recipient=params.recipient,
                message=clean_msg,
            )

            if success:
                return ToolResult(
                    success=True,
                    output={
                        "action": "send_message",
                        "channel": params.channel,
                        "recipient": params.recipient,
                        "message_preview": clean_msg[:80] + ("..." if len(clean_msg) > 80 else ""),
                        "message": f"Message sent to {params.recipient} via {params.channel.capitalize()}.",
                    },
                )
            else:
                return ToolResult(
                    success=False,
                    error=f"Message dispatch to {params.recipient} failed.",
                )

        except Exception as exc:
            logger.exception("MessagingTool dispatch failed: %s", exc)
            return ToolResult(
                success=False,
                error=f"Messaging operation failed: {exc}",
            )

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        if not result.success or not isinstance(result.output, dict):
            return VerificationReport(
                verified=False,
                details=f"Messaging dispatch failed: {result.error or 'no output dictionary'}",
                discrepancies=[result.error or "dispatch_failed"],
            )

        return VerificationReport(
            verified=True,
            details=f"Message dispatch to {result.output.get('recipient')} successfully verified.",
        )
