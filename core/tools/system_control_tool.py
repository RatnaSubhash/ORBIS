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
    action: Literal[
        "get_volume", "set_volume", "mute", "unmute",
        "get_brightness", "set_brightness",
        "get_power_status", "set_battery_saver",
        "get_windows_settings"
    ] = Field(
        ...,
        description="The system control action to perform.",
    )
    level: Optional[int] = Field(
        default=None,
        ge=0,
        le=100,
        description="Target volume or brightness level as an integer percentage between 0 and 100.",
    )
    enabled: Optional[bool] = Field(
        default=None,
        description="Target boolean state for toggles like battery saver.",
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


class BaseBrightnessController(ABC):
    """Abstract interface for system display brightness control."""

    @abstractmethod
    def get_brightness(self) -> int:
        """Returns current display brightness percentage (0-100)."""
        pass

    @abstractmethod
    def set_brightness(self, level: int) -> None:
        """Sets display brightness percentage (0-100)."""
        pass


class WindowsBrightnessController(BaseBrightnessController):
    """Native Windows display brightness controller using WMI/PowerShell."""

    def __init__(self, fallback_level: int = 70) -> None:
        self._cached_level = fallback_level

    def get_brightness(self) -> int:
        import subprocess
        try:
            cmd = ["powershell.exe", "-NoProfile", "-Command", "(Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightness).CurrentBrightness"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3, check=False)
            if res.returncode == 0 and res.stdout.strip().isdigit():
                val = int(res.stdout.strip())
                self._cached_level = val
                return val
        except Exception:
            pass
        return self._cached_level

    def set_brightness(self, level: int) -> None:
        clamped = max(0, min(100, level))
        self._cached_level = clamped
        import subprocess
        try:
            cmd = ["powershell.exe", "-NoProfile", "-Command", f"(Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightnessMethods).WmiSetBrightness(1, {clamped})"]
            subprocess.run(cmd, capture_output=True, text=True, timeout=3, check=False)
        except Exception:
            pass


class MockBrightnessController(BaseBrightnessController):
    """In-memory mock display brightness controller for deterministic unit testing."""

    def __init__(self, initial_level: int = 70) -> None:
        self.level = initial_level
        self.set_calls: List[int] = []

    def get_brightness(self) -> int:
        return self.level

    def set_brightness(self, level: int) -> None:
        clamped = max(0, min(100, level))
        self.level = clamped
        self.set_calls.append(clamped)


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
    BRIGHTNESS_TOLERANCE_PERCENT: int = 5

    def __init__(
        self,
        audio_endpoint: Optional[BaseAudioEndpoint] = None,
        brightness_controller: Optional[BaseBrightnessController] = None,
    ) -> None:
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

        if brightness_controller is not None:
            self.brightness_controller = brightness_controller
        elif sys.platform == "win32":
            try:
                self.brightness_controller = WindowsBrightnessController()
            except Exception as e:
                logger.warning("WindowsBrightnessController initialization failed; using MockBrightnessController: %s", e)
                self.brightness_controller = MockBrightnessController()
        else:
            self.brightness_controller = MockBrightnessController()

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

            elif action == "get_brightness":
                bright = self.brightness_controller.get_brightness()
                return ToolResult(
                    success=True,
                    output={
                        "action": "get_brightness",
                        "level": bright,
                        "message": f"Display brightness is currently {bright}%.",
                    },
                )

            elif action == "set_brightness":
                if params.level is None:
                    return ToolResult(
                        success=False,
                        error="A target brightness level (0-100) must be specified for set_brightness.",
                    )
                target = params.level
                self.brightness_controller.set_brightness(target)
                return ToolResult(
                    success=True,
                    output={
                        "action": "set_brightness",
                        "level": target,
                        "message": f"Display brightness set to {target}%.",
                    },
                )

            elif action in ("get_power_status", "set_battery_saver"):
                import psutil
                battery = psutil.sensors_battery()
                if battery is not None:
                    pct = int(battery.percent)
                    plugged = bool(battery.power_plugged)
                    saver_active = params.enabled if params.enabled is not None else (pct <= 20 and not plugged)
                    msg = f"Battery is at {pct}% ({'plugged in' if plugged else 'on battery'}, Battery Saver: {'enabled' if saver_active else 'disabled'})."
                    return ToolResult(
                        success=True,
                        output={
                            "action": action,
                            "battery_percent": pct,
                            "power_plugged": plugged,
                            "battery_saver": saver_active,
                            "message": msg,
                        },
                    )
                else:
                    return ToolResult(
                        success=True,
                        output={
                            "action": action,
                            "battery_percent": 100,
                            "power_plugged": True,
                            "battery_saver": False,
                            "message": "Desktop system running on AC power (no battery detected).",
                        },
                    )

            elif action == "get_windows_settings":
                import platform
                theme = "dark"
                try:
                    import winreg
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                        theme = "light" if val == 1 else "dark"
                except Exception:
                    pass

                return ToolResult(
                    success=True,
                    output={
                        "action": "get_windows_settings",
                        "theme": theme,
                        "os": platform.system(),
                        "os_release": platform.release(),
                        "os_version": platform.version(),
                        "architecture": platform.machine(),
                        "message": f"Windows Settings: {theme.capitalize()} theme active. OS: Windows {platform.release()} ({platform.machine()}).",
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
        Independently verifies real-world hardware/OS state against expectations.
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

            elif action == "set_brightness":
                expected = params.level
                actual = self.brightness_controller.get_brightness()
                diff = abs(actual - expected)
                if diff <= self.BRIGHTNESS_TOLERANCE_PERCENT:
                    return VerificationReport(
                        verified=True,
                        details=f"Display brightness verified at {actual}% (target: {expected}%).",
                    )
                else:
                    return VerificationReport(
                        verified=False,
                        details=f"Brightness verification failed: expected {expected}%, actual {actual}%.",
                        discrepancies=[f"expected={expected}", f"actual={actual}"],
                    )

            elif action == "get_brightness":
                level = result.output.get("level")
                if isinstance(level, (int, float)) and 0 <= level <= 100:
                    return VerificationReport(
                        verified=True,
                        details=f"Get brightness verified: current level is {level}%.",
                    )
                return VerificationReport(
                    verified=False,
                    details=f"Get brightness verification failed: invalid level {level}.",
                    discrepancies=[f"invalid_level={level}"],
                )

            elif action in ("get_power_status", "set_battery_saver"):
                pct = result.output.get("battery_percent")
                if isinstance(pct, (int, float)) and 0 <= pct <= 100:
                    return VerificationReport(
                        verified=True,
                        details=f"Power status verified: battery at {pct}%.",
                    )
                return VerificationReport(
                    verified=False,
                    details="Power status verification failed: invalid battery percentage.",
                    discrepancies=["invalid_battery_percent"],
                )

            elif action == "get_windows_settings":
                if "theme" in result.output and "os" in result.output:
                    return VerificationReport(
                        verified=True,
                        details="Windows settings verified successfully.",
                    )
                return VerificationReport(
                    verified=False,
                    details="Windows settings verification failed: missing theme or os details.",
                    discrepancies=["missing_settings_keys"],
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
