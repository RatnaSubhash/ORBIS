"""
VoiceTool for ORBIS Phase 11.
Provides a BaseTool-compliant interface for the planner and agent to vocalize concise summaries
via Text-to-Speech or inspect voice subsystem status.

CRITICAL INVARIANT:
The planner/LLM must NEVER be able to arbitrarily activate the microphone, start listening,
or recursively initiate another voice interaction. Push-to-Talk is strictly user-initiated.
Autonomous microphone capture is explicitly blocked.
"""

from typing import Any, Dict, Literal, Optional, Type
from pydantic import BaseModel, Field, field_validator

from core.tools.base import BaseTool
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport
from core.voice.normalizer import VoiceNormalizer
from core.voice.tts.base import BaseTTSProvider
from core.voice.tts.mock_tts import MockTTSProvider


class VoiceToolParameters(BaseModel):
    """Parameters for VoiceTool operations."""
    action: str = Field(
        ...,
        description="Action to perform: 'speak' (vocalize summary aloud), 'synthesize' (dry-run text-to-speech), or 'get_status' (check voice subsystem readiness)."
    )
    text: Optional[str] = Field(
        default=None,
        description="Text content to vocalize or synthesize. Markdown and raw JSON will be automatically formatted for speech."
    )
    voice_name: Optional[str] = Field(
        default=None,
        description="Optional voice name/identifier to use for synthesis."
    )

    @field_validator("action")
    @classmethod
    def prevent_unauthorized_listening(cls, v: str) -> str:
        prohibited = {"listen", "record", "start_listening", "capture_audio", "surveil"}
        if v.lower() in prohibited:
            raise ValueError(
                "Autonomous microphone capture is prohibited. "
                "Microphone recording is strictly user-initiated via Push-to-Talk."
            )
        allowed = {"speak", "synthesize", "get_status"}
        if v.lower() not in allowed:
            raise ValueError(f"Action must be one of {allowed}, got '{v}'.")
        return v.lower()


class VoiceTool(BaseTool):
    """
    Tool allowing the ORBIS agent to speak notifications/summaries or query voice subsystem status.
    Strictly prohibits agent-initiated microphone recording or listening.
    """

    name: str = "voice"
    description: str = (
        "Allows ORBIS to vocalize concise summaries aloud to the user via local Text-to-Speech, "
        "or check voice interface readiness. Does NOT allow microphone recording or listening."
    )
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    parameters_schema: Type[BaseModel] = VoiceToolParameters

    def __init__(self, tts_provider: Optional[BaseTTSProvider] = None) -> None:
        self.tts_provider = tts_provider or MockTTSProvider()

    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        """Executes voice operations strictly limited to outbound speech and status inspection."""
        p: VoiceToolParameters = params  # type: ignore

        if p.action == "get_status":
            available = self.tts_provider.is_available()
            return ToolResult(
                tool_name=self.name,
                success=True,
                output={
                    "status": "ready" if available else "unavailable",
                    "tts_available": available,
                    "mode": "push_to_talk_only",
                    "autonomous_listening_prohibited": True,
                },
            )

        if p.action in ("speak", "synthesize"):
            if not p.text or not p.text.strip():
                return ToolResult(
                    tool_name=self.name,
                    success=False,
                    error="Parameter 'text' cannot be empty for speak or synthesize actions.",
                )

            # Clean output for safe speech vocalization
            safe_text = VoiceNormalizer.format_output_for_speech(p.text)

            if p.action == "synthesize":
                res = await self.tts_provider.synthesize(safe_text)
            else:
                res = await self.tts_provider.speak(safe_text)

            if not res.success:
                return ToolResult(
                    tool_name=self.name,
                    success=False,
                    error=res.error or "Speech synthesis failed.",
                    output={"spoken_text": safe_text},
                )

            return ToolResult(
                tool_name=self.name,
                success=True,
                output={
                    "spoken_text": safe_text,
                    "duration_seconds": res.duration_seconds,
                    "voice_name": res.voice_name,
                    "was_cancelled": res.was_cancelled,
                },
            )

        return ToolResult(
            tool_name=self.name,
            success=False,
            error=f"Unsupported action: {p.action}",
        )

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        """Verifies voice tool output integrity."""
        p: VoiceToolParameters = params  # type: ignore

        if not result.success:
            return VerificationReport(
                verified=False,
                details=f"VoiceTool execution failed: {result.error}",
                discrepancies=[f"Execution error: {result.error}"],
            )

        if p.action == "get_status":
            return VerificationReport(
                verified=True,
                details="Voice subsystem status inspected successfully.",
            )

        output = result.output or {}
        spoken_text = output.get("spoken_text", "")
        if not spoken_text:
            return VerificationReport(
                verified=False,
                details="Spoken text output was empty.",
                discrepancies=["Output missing spoken_text"],
            )

        return VerificationReport(
            verified=True,
            details=f"Speech successfully generated ({len(spoken_text)} characters).",
        )
