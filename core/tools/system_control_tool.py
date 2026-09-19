"""
System Control Tool for ORBIS.
Provides native Windows system audio controls (master volume, mute, unmute)
via Core Audio WASAPI (pycaw / IAudioEndpointVolume).
Enforces Pydantic parameter validation, independent hardware verification,
and safe mockable backends for automated testing.
"""

from abc import ABC, abstractmethod
from contextlib import contextmanager
import logging
import math
import sys
from typing import Any, Dict, Literal, Optional, Type
from pydantic import BaseModel, Field

from core.tools.base import BaseTool
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport

logger = logging.getLogger("orbis.tools.system_control")


class SystemControlParameters(BaseModel):
    """Parameters for SystemControlTool operations."""
    action: Literal["get_volume", "set_volume", "mute", "unmute"] = Field(
        ...,
        description="The system control action to perform: 'get_volume', 'set_volume', 'mute', or 'unmute'.",
    )
    level: Optional[int] = Field(
        default=None,
        ge=0,
        le=100,
        description="Target volume level as an integer percentage between 0 and 100 (required for set_volume).",
    )


class BaseAudioEndpoint(ABC):
    """Abstract interface for system audio volume and mute control."""

    @abstractmethod
    def get_volume(self) -> int:
        """Returns the current master volume as an integer percentage (0-100)."""
        pass

    @abstractmethod
    def set_volume(self, level: int) -> None:
        """Sets the master volume to an integer percentage (0-100)."""
        pass

    @abstractmethod
    def get_mute(self) -> bool:
        """Returns True if the system audio is currently muted, False otherwise."""
        pass

    @abstractmethod
    def set_mute(self, mute: bool) -> None:
        """Sets the system mute state."""
        pass


class WindowsAudioEndpoint(BaseAudioEndpoint):
    """
    Native Windows Core Audio endpoint controller using WASAPI IAudioEndpointVolume via pycaw.
    Directly modifies and inspects the master audio endpoint on Windows 11.
    Thread-safe: safely initializes and uninitializes the COM apartment on the active calling thread.
    """

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("WindowsAudioEndpoint is only supported on Windows platforms.")

    @contextmanager
    def _com_endpoint(self) -> Any:
        """
        Context manager ensuring COM apartment initialization on the active thread
        and safe, balanced CoUninitialize() cleanup on exit.
        """
        import comtypes

        comtypes.CoInitialize()
        try:
            from pycaw.pycaw import AudioUtilities

            speakers = AudioUtilities.GetSpeakers()
            if speakers is None:
                raise RuntimeError("No default audio output device (speakers) found on this system.")
            yield speakers.EndpointVolume
        except Exception as e:
            logger.error("Failed to initialize Windows Core Audio endpoint: %s", e)
            raise RuntimeError(f"Unable to access Windows Core Audio endpoint: {e}") from e
        finally:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass

    def get_volume(self) -> int:
        """Reads current master volume level scalar and returns integer 0-100."""
        with self._com_endpoint() as endpoint:
            scalar = endpoint.GetMasterVolumeLevelScalar()
            return int(round(scalar * 100.0))

    def set_volume(self, level: int) -> None:
        """Sets master volume level scalar (0.0 to 1.0) from integer 0-100."""
        with self._com_endpoint() as endpoint:
            clamped = max(0, min(100, level))
            scalar = clamped / 100.0
            endpoint.SetMasterVolumeLevelScalar(scalar, None)
            # If setting volume above 0, ensure endpoint is unmuted so change is audible
            if clamped > 0 and endpoint.GetMute():
                endpoint.SetMute(0, None)

    def get_mute(self) -> bool:
        """Returns True if muted, False otherwise."""
        with self._com_endpoint() as endpoint:
            return bool(endpoint.GetMute())

    def set_mute(self, mute: bool) -> None:
        """Sets mute state: 1 for mute, 0 for unmute."""
        with self._com_endpoint() as endpoint:
            endpoint.SetMute(1 if mute else 0, None)


class MockAudioEndpoint(BaseAudioEndpoint):
    """
    In-memory mock audio endpoint for unit tests and non-Windows environments.
    Guarantees deterministic testing without mutating developer or CI audio hardware.
    """

    def __init__(self, initial_level: int = 50, initial_mute: bool = False) -> None:
        self.level = initial_level
        self.muted = initial_mute
        self.set_volume_calls = []
        self.set_mute_calls = []

    def get_volume(self) -> int:
        return self.level

    def set_volume(self, level: int) -> None:
        clamped = max(0, min(100, level))
        self.level = clamped
        self.set_volume_calls.append(clamped)
        if clamped > 0 and self.muted:
            self.muted = False

    def get_mute(self) -> bool:
        return self.muted

    def set_mute(self, mute: bool) -> None:
        self.muted = bool(mute)
        self.set_mute_calls.append(self.muted)


class SystemControlTool(BaseTool):
    """
    Controls and inspects local system settings, starting with master audio volume and mute.
    Enforces independent post-execution hardware verification and tolerance-based matching.
    """

    name: str = "system_control"
    description: str = (
        "Controls and inspects local system settings, starting with master audio volume, mute, and unmute. "
        "Use this tool to adjust sound volume, mute or unmute the system, or check the current volume."
    )
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    parameters_schema: Type[BaseModel] = SystemControlParameters

    # Hardware volume step tolerance (accounting for discrete audio chip quantization)
    VOLUME_TOLERANCE_PERCENT: int = 2

    def __init__(self, audio_endpoint: Optional[BaseAudioEndpoint] = None) -> None:
        if audio_endpoint is not None:
            self.audio_endpoint = audio_endpoint
        elif sys.platform == "win32":
            try:
                self.audio_endpoint = WindowsAudioEndpoint()
            except Exception as e:
                logger.warning("WindowsAudioEndpoint initialization failed; using MockAudioEndpoint fallback: %s", e)
                self.audio_endpoint = MockAudioEndpoint()
        else:
            self.audio_endpoint = MockAudioEndpoint()

    async def execute(
        self, params: BaseModel, context: ExecutionContext
    ) -> ToolResult:
        """Executes the requested system control operation."""
        if not isinstance(params, SystemControlParameters):
            params = self.validate_parameters(params.model_dump())

        action = params.action

        try:
            if action == "get_volume":
                vol = self.audio_endpoint.get_volume()
                muted = self.audio_endpoint.get_mute()
                muted_str = " (muted)" if muted else ""
                msg = f"Current master volume is {vol}%{muted_str}."
                return ToolResult(
                    success=True,
                    output={
                        "action": "get_volume",
                        "level": vol,
                        "muted": muted,
                        "message": msg,
                    },
                )

            elif action == "set_volume":
                if params.level is None:
                    return ToolResult(
                        success=False,
                        error="A target volume level (0-100) must be specified for set_volume.",
                    )
                target = params.level
                self.audio_endpoint.set_volume(target)
                return ToolResult(
                    success=True,
                    output={
                        "action": "set_volume",
                        "level": target,
                        "message": f"Master volume set to {target}%.",
                    },
                )

            elif action == "mute":
                self.audio_endpoint.set_mute(True)
                return ToolResult(
                    success=True,
                    output={
                        "action": "mute",
                        "muted": True,
                        "message": "Computer audio has been muted.",
                    },
                )

            elif action == "unmute":
                self.audio_endpoint.set_mute(False)
                return ToolResult(
                    success=True,
                    output={
                        "action": "unmute",
                        "muted": False,
                        "message": "Computer audio has been unmuted.",
                    },
                )

            else:
                return ToolResult(
                    success=False,
                    error=f"Unrecognized system control action '{action}'.",
                )

        except Exception as exc:
            logger.exception("System control action '%s' failed: %s", action, exc)
            return ToolResult(
                success=False,
                error=f"System control operation '{action}' failed: {exc}",
            )

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        """
        Independently verifies real-world hardware state against expectations:
        - set_volume: reads actual volume from audio endpoint, verifies within tolerance.
        - mute: reads actual mute state, verifies muted is True.
        - unmute: reads actual mute state, verifies muted is False.
        - get_volume: verifies output contains valid integer volume level.
        """
        if not result.success or not isinstance(result.output, dict):
            return VerificationReport(
                verified=False,
                details=f"Tool execution failed: {result.error or 'no output dictionary'}",
                discrepancies=[result.error or "execution_failed"],
            )

        action = getattr(params, "action", None) or result.output.get("action")

        try:
            if action == "set_volume":
                expected = params.level
                actual = self.audio_endpoint.get_volume()
                diff = abs(actual - expected)
                if diff <= self.VOLUME_TOLERANCE_PERCENT:
                    return VerificationReport(
                        verified=True,
                        details=f"Volume verified at {actual}% (target: {expected}%, diff: {diff}% within tolerance).",
                    )
                else:
                    return VerificationReport(
                        verified=False,
                        details=(
                            f"Volume verification failed: expected {expected}%, but audio endpoint reported {actual}% "
                            f"(diff: {diff}% exceeds {self.VOLUME_TOLERANCE_PERCENT}% tolerance)."
                        ),
                        discrepancies=[f"expected={expected}", f"actual={actual}", f"diff={diff}"],
                    )

            elif action == "mute":
                actual_mute = self.audio_endpoint.get_mute()
                if actual_mute is True:
                    return VerificationReport(
                        verified=True,
                        details="Mute state verified: audio endpoint is muted.",
                    )
                else:
                    return VerificationReport(
                        verified=False,
                        details="Mute verification failed: audio endpoint remains unmuted.",
                        discrepancies=["expected_muted=True", "actual_muted=False"],
                    )

            elif action == "unmute":
                actual_mute = self.audio_endpoint.get_mute()
                if actual_mute is False:
                    return VerificationReport(
                        verified=True,
                        details="Unmute state verified: audio endpoint is unmuted.",
                    )
                else:
                    return VerificationReport(
                        verified=False,
                        details="Unmute verification failed: audio endpoint remains muted.",
                        discrepancies=["expected_muted=False", "actual_muted=True"],
                    )

            elif action == "get_volume":
                level = result.output.get("level")
                if isinstance(level, (int, float)) and 0 <= level <= 100:
                    return VerificationReport(
                        verified=True,
                        details=f"Get volume verified: current level is {level}%.",
                    )
                else:
                    return VerificationReport(
                        verified=False,
                        details=f"Get volume verification failed: invalid level value {level}.",
                        discrepancies=[f"invalid_level={level}"],
                    )

            return VerificationReport(
                verified=True,
                details=f"Action '{action}' verified successfully.",
            )

        except Exception as ver_ex:
            logger.warning("Independent verification exception for action '%s': %s", action, ver_ex)
            return VerificationReport(
                verified=False,
                details=f"Independent hardware verification raised exception: {ver_ex}",
                discrepancies=[str(ver_ex)],
            )
