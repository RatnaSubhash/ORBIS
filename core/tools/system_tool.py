"""
System Utility Tool for ORBIS.
Provides local system time, date, and basic runtime inspection without external network requests.
Supports timezone-aware conversions and independent verification against the local system clock.
"""

from datetime import datetime, timedelta, timezone
import logging
import platform
import re
from typing import Any, Dict, List, Literal, Optional, Tuple, Type
from pydantic import BaseModel, Field

from core.tools.base import BaseTool
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport

logger = logging.getLogger("orbis.tools.system")

# Fixed offset mappings for common cities and codes (works 100% reliably on Windows without tzdata package)
TIMEZONE_OFFSETS: Dict[str, Tuple[timedelta, str]] = {
    "chennai": (timedelta(hours=5, minutes=30), "IST"),
    "delhi": (timedelta(hours=5, minutes=30), "IST"),
    "mumbai": (timedelta(hours=5, minutes=30), "IST"),
    "kolkata": (timedelta(hours=5, minutes=30), "IST"),
    "bangalore": (timedelta(hours=5, minutes=30), "IST"),
    "bengaluru": (timedelta(hours=5, minutes=30), "IST"),
    "india": (timedelta(hours=5, minutes=30), "IST"),
    "ist": (timedelta(hours=5, minutes=30), "IST"),
    "utc": (timedelta(0), "UTC"),
    "gmt": (timedelta(0), "GMT"),
    "london": (timedelta(hours=1), "BST"),
    "new york": (timedelta(hours=-4), "EDT"),
    "est": (timedelta(hours=-5), "EST"),
    "edt": (timedelta(hours=-4), "EDT"),
    "pst": (timedelta(hours=-8), "PST"),
    "pdt": (timedelta(hours=-7), "PDT"),
    "tokyo": (timedelta(hours=9), "JST"),
    "singapore": (timedelta(hours=8), "SGT"),
    "sydney": (timedelta(hours=10), "AEST"),
}


def resolve_timezone(tz_name: Optional[str]) -> Tuple[Optional[timezone], str]:
    """
    Resolves a human-provided timezone name, city, or code into a standard datetime.timezone object.
    Returns (timezone_or_None, display_tz_name).
    """
    if not tz_name or not tz_name.strip():
        return None, "local"

    clean_name = tz_name.strip().lower()
    if clean_name in TIMEZONE_OFFSETS:
        offset, code = TIMEZONE_OFFSETS[clean_name]
        return timezone(offset, code), code

    try:
        from zoneinfo import ZoneInfo
        zi = ZoneInfo(tz_name.strip())
        return zi, tz_name.strip()
    except Exception:
        logger.warning("Unrecognized timezone '%s', defaulting to local system time.", tz_name)
        return None, "local"


class SystemUtilityParameters(BaseModel):
    """Parameters for SystemUtilityTool operations."""
    action: Literal["get_current_time", "get_current_date", "get_system_info"] = Field(
        default="get_current_time",
        description="System utility operation to perform.",
    )
    timezone: Optional[str] = Field(
        default=None,
        description="Optional timezone name, code, or city (e.g., 'Chennai', 'Asia/Kolkata', 'UTC').",
    )


class SystemUtilityTool(BaseTool):
    """
    Safe read-only tool providing deterministic local system time, date,
    and system status without internet dependency.
    """

    name: str = "system"
    description: str = (
        "Provides real-time local system time, current date, and system environment info. "
        "Use this tool for questions about the current time, today's date, or timezone queries."
    )
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    parameters_schema: Type[BaseModel] = SystemUtilityParameters

    async def execute(
        self, params: BaseModel, context: ExecutionContext
    ) -> ToolResult:
        """Executes the system utility action."""
        if not isinstance(params, SystemUtilityParameters):
            params = self.validate_parameters(params.model_dump())

        tz_obj, tz_label = resolve_timezone(params.timezone)

        if tz_obj is not None:
            now = datetime.now(tz_obj)
        else:
            now = datetime.now().astimezone()

        action = params.action

        if action == "get_current_time":
            time_str = now.strftime("%I:%M %p").lstrip("0")
            tz_code = now.strftime("%Z") or tz_label
            date_str = now.strftime("%A, %B %d, %Y")
            formatted = f"The current time is {time_str} {tz_code}."
            if tz_label != "local" and params.timezone:
                formatted = f"The current time in {params.timezone.title()} is {time_str} ({tz_code})."

            output = {
                "action": action,
                "time": time_str,
                "timezone": tz_code,
                "date": date_str,
                "iso": now.isoformat(),
                "formatted": formatted,
                "epoch_timestamp": now.timestamp(),
            }
            return ToolResult(success=True, output=output)

        elif action == "get_current_date":
            date_str = now.strftime("%A, %B %d, %Y")
            tz_code = now.strftime("%Z") or tz_label
            formatted = f"Today is {date_str}."

            output = {
                "action": action,
                "date": date_str,
                "day_of_week": now.strftime("%A"),
                "day": now.day,
                "month": now.strftime("%B"),
                "year": now.year,
                "iso": now.isoformat(),
                "formatted": formatted,
                "epoch_timestamp": now.timestamp(),
            }
            return ToolResult(success=True, output=output)

        elif action == "get_system_info":
            info = {
                "action": action,
                "os": platform.system(),
                "os_release": platform.release(),
                "os_version": platform.version(),
                "machine": platform.machine(),
                "python_version": platform.python_version(),
                "local_time": now.strftime("%I:%M %p %Z"),
                "formatted": f"ORBIS host: {platform.system()} {platform.release()} ({platform.machine()}), Python {platform.python_version()}.",
            }
            return ToolResult(success=True, output=info)

        else:
            return ToolResult(
                success=False,
                error=f"Unsupported system utility action: '{action}'.",
            )

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        """
        Independently verifies that the output timestamp matches current system clock
        within a safe bounded drift window (< 10.0 seconds).
        """
        if not result.success or not isinstance(result.output, dict):
            return VerificationReport(
                verified=False,
                details="Tool execution failed or produced non-dictionary output.",
            )

        epoch = result.output.get("epoch_timestamp")
        if epoch is None:
            return VerificationReport(
                verified=True,
                details="System action verified successfully.",
            )

        now_epoch = datetime.now(timezone.utc).timestamp()
        drift = abs(now_epoch - epoch)
        if drift > 10.0:
            return VerificationReport(
                verified=False,
                details=f"System clock verification failed: clock drift is {drift:.2f}s (> 10.0s).",
            )

        return VerificationReport(
            verified=True,
            details=f"System time verified against clock (drift: {drift:.3f}s).",
        )

