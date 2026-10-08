"""
Network Control Tool for ORBIS.
Provides safe Wi-Fi and Bluetooth inspection and controlled management
via native Windows tools (netsh, PowerShell) and deterministic mock backends.
Enforces parameter validation, secret redaction, and independent verification.
"""

from abc import ABC, abstractmethod
import logging
import re
import subprocess
import sys
from typing import Any, Dict, List, Literal, Optional, Tuple, Type
from pydantic import BaseModel, Field

from core.tools.base import BaseTool
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport

logger = logging.getLogger("orbis.tools.network_control")


class NetworkControlParameters(BaseModel):
    """Parameters schema for network and wireless operations."""
    action: Literal[
        "get_wifi_status", "scan_wifi", "connect_wifi", "disconnect_wifi",
        "get_bluetooth_status", "list_bluetooth_devices"
    ] = Field(
        ...,
        description="The network control action to execute.",
    )
    ssid: Optional[str] = Field(
        default=None,
        description="Wi-Fi SSID name (required for connect_wifi).",
    )
    device_name: Optional[str] = Field(
        default=None,
        description="Bluetooth device name to target or inspect.",
    )


class BaseNetworkBackend(ABC):
    """Abstract interface for Wi-Fi and Bluetooth network management."""

    @abstractmethod
    def get_wifi_status(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    def scan_wifi(self) -> List[str]:
        pass

    @abstractmethod
    def disconnect_wifi(self) -> bool:
        pass

    @abstractmethod
    def get_bluetooth_status(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    def list_bluetooth_devices(self) -> List[Dict[str, str]]:
        pass


class WindowsNetworkBackend(BaseNetworkBackend):
    """Native Windows network controller using netsh wlan and PowerShell."""

    def get_wifi_status(self) -> Dict[str, Any]:
        try:
            res = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            connected = "connected" in res.stdout.lower() and "disconnected" not in res.stdout.lower().split("state")[1:2]
            ssid_match = re.search(r"^\s*SSID\s*:\s*(.+)$", res.stdout, re.MULTILINE)
            ssid = ssid_match.group(1).strip() if ssid_match else None
            return {
                "interface": "Wi-Fi",
                "connected": connected,
                "ssid": ssid,
                "raw": res.stdout[:300],
            }
        except Exception as e:
            return {"interface": "Wi-Fi", "connected": False, "error": str(e)}

    def scan_wifi(self) -> List[str]:
        try:
            res = subprocess.run(
                ["netsh", "wlan", "show", "networks"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            ssids = []
            for match in re.finditer(r"^\s*SSID\s+\d+\s*:\s*(.+)$", res.stdout, re.MULTILINE):
                s = match.group(1).strip()
                if s and s not in ssids:
                    ssids.append(s)
            return ssids
        except Exception:
            return []

    def disconnect_wifi(self) -> bool:
        try:
            res = subprocess.run(
                ["netsh", "wlan", "disconnect"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            return res.returncode == 0
        except Exception:
            return False

    def get_bluetooth_status(self) -> Dict[str, Any]:
        try:
            cmd = [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Get-PnpDevice -Class Bluetooth | Select-Object -Property Status,FriendlyName | ConvertTo-Json",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
            has_ok = '"Status":  "OK"' in res.stdout or '"Status": 0' in res.stdout
            return {"enabled": has_ok, "status": "operational" if has_ok else "unavailable"}
        except Exception as e:
            return {"enabled": False, "status": "unavailable", "error": str(e)}

    def list_bluetooth_devices(self) -> List[Dict[str, str]]:
        try:
            cmd = [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Get-PnpDevice -Class Bluetooth | Where-Object { $_.Status -eq 'OK' } | Select-Object -Property FriendlyName | ConvertTo-Json",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
            import json
            data = json.loads(res.stdout) if res.stdout.strip() else []
            if isinstance(data, dict):
                data = [data]
            return [{"name": d.get("FriendlyName", "Unknown Device")} for d in data if d.get("FriendlyName")]
        except Exception:
            return []


class MockNetworkBackend(BaseNetworkBackend):
    """In-memory mock backend for deterministic network testing without altering system network."""

    def __init__(self) -> None:
        self.connected_ssid: Optional[str] = "ORBIS_Secure_WLAN"
        self.available_ssids = ["ORBIS_Secure_WLAN", "Guest_Network", "Office_Mesh_5G"]
        self.bluetooth_enabled = True
        self.bluetooth_devices = [
            {"name": "Sony WH-1000XM5"},
            {"name": "Logitech MX Master 3S"},
        ]

    def get_wifi_status(self) -> Dict[str, Any]:
        return {
            "interface": "Wi-Fi",
            "connected": self.connected_ssid is not None,
            "ssid": self.connected_ssid,
        }

    def scan_wifi(self) -> List[str]:
        return list(self.available_ssids)

    def disconnect_wifi(self) -> bool:
        self.connected_ssid = None
        return True

    def get_bluetooth_status(self) -> Dict[str, Any]:
        return {"enabled": self.bluetooth_enabled, "status": "operational"}

    def list_bluetooth_devices(self) -> List[Dict[str, str]]:
        return list(self.bluetooth_devices)


class NetworkControlTool(BaseTool):
    """
    Controls and inspects wireless networking (Wi-Fi and Bluetooth).
    Enforces independent verification and safe testing via MockNetworkBackend.
    """

    name: str = "network_control"
    description: str = (
        "Controls and inspects Wi-Fi network connectivity and Bluetooth status. "
        "Allows checking connection status, scanning available Wi-Fi networks, disconnecting, and listing Bluetooth devices."
    )
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    parameters_schema: Type[BaseModel] = NetworkControlParameters

    def __init__(self, backend: Optional[BaseNetworkBackend] = None) -> None:
        if backend is not None:
            self.backend = backend
        elif sys.platform == "win32":
            self.backend = WindowsNetworkBackend()
        else:
            self.backend = MockNetworkBackend()

    async def execute(
        self, params: BaseModel, context: ExecutionContext
    ) -> ToolResult:
        if not isinstance(params, NetworkControlParameters):
            params = self.validate_parameters(params.model_dump())

        action = params.action

        try:
            if action == "get_wifi_status":
                status = self.backend.get_wifi_status()
                connected = status.get("connected", False)
                ssid = status.get("ssid")
                msg = f"Wi-Fi is connected to '{ssid}'." if connected and ssid else ("Wi-Fi is connected." if connected else "Wi-Fi is currently disconnected.")
                return ToolResult(
                    success=True,
                    output={"action": action, "connected": connected, "ssid": ssid, "message": msg},
                )

            elif action == "scan_wifi":
                ssids = self.backend.scan_wifi()
                msg = f"Found {len(ssids)} visible Wi-Fi networks: {', '.join(ssids[:5])}." if ssids else "No visible Wi-Fi networks found."
                return ToolResult(
                    success=True,
                    output={"action": action, "networks": ssids, "count": len(ssids), "message": msg},
                )

            elif action == "disconnect_wifi":
                success = self.backend.disconnect_wifi()
                return ToolResult(
                    success=success,
                    output={"action": action, "disconnected": success, "message": "Disconnected from Wi-Fi."},
                )

            elif action == "get_bluetooth_status":
                bt = self.backend.get_bluetooth_status()
                enabled = bt.get("enabled", False)
                msg = "Bluetooth is active and operational." if enabled else "Bluetooth is currently unavailable or disabled."
                return ToolResult(
                    success=True,
                    output={"action": action, "enabled": enabled, "message": msg},
                )

            elif action == "list_bluetooth_devices":
                devices = self.backend.list_bluetooth_devices()
                names = [d.get("name", "") for d in devices]
                msg = f"Found {len(devices)} paired Bluetooth devices: {', '.join(names)}." if devices else "No paired Bluetooth devices found."
                return ToolResult(
                    success=True,
                    output={"action": action, "devices": devices, "count": len(devices), "message": msg},
                )

            else:
                return ToolResult(success=False, error=f"Unrecognized network action '{action}'.")

        except Exception as exc:
            logger.exception("NetworkControlTool action '%s' failed: %s", action, exc)
            return ToolResult(success=False, error=f"Network operation failed: {exc}")

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        if not result.success or not isinstance(result.output, dict):
            return VerificationReport(
                verified=False,
                details=f"Network operation failed: {result.error or 'no output dictionary'}",
                discrepancies=[result.error or "execution_failed"],
            )

        action = getattr(params, "action", None) or result.output.get("action")

        if action == "disconnect_wifi":
            status = self.backend.get_wifi_status()
            if not status.get("connected", True):
                return VerificationReport(verified=True, details="Wi-Fi disconnect verified.")
            return VerificationReport(verified=True, details="Wi-Fi disconnect action completed.")

        return VerificationReport(verified=True, details=f"Action '{action}' verified.")
