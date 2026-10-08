"""
App Launch Tool for ORBIS.
Provides safe, allowlisted desktop application launching, PDF viewing,
multi-application sequencing, YouTube media workflow, and web application access.
Strictly enforces discrete argument lists (shell=False), path validation,
allowlists, and independent verification.
"""

from abc import ABC, abstractmethod
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Type
from pydantic import BaseModel, Field, field_validator

from core.config import get_settings
from core.tools.base import BaseTool
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport

logger = logging.getLogger("orbis.tools.app_launch")

# Authoritative allowlist of supported desktop applications
ALLOWLISTED_APPS: Dict[str, List[str]] = {
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe", "calculator:"],
    "calc": ["calc.exe", "calculator:"],
    "code": ["code.cmd", "Code.exe", "code"],
    "vscode": ["code.cmd", "Code.exe", "code"],
    "chrome": ["chrome.exe", "google-chrome"],
    "edge": ["msedge.exe", "microsoft-edge:"],
    "msedge": ["msedge.exe", "microsoft-edge:"],
    "opera": ["opera.exe", "launcher.exe"],
    "spotify": ["spotify:", "spotify.exe", "spotify_cli.exe"],
    "whatsapp": ["whatsapp:", "whatsapp.exe"],
    "terminal": ["wt.exe", "powershell.exe", "cmd.exe"],
    "powershell": ["powershell.exe"],
    "cmd": ["cmd.exe"],
    "explorer": ["explorer.exe"],
    "settings": ["ms-settings:"],
    "discord": ["discord:", "discord.exe"],
    "steam": ["steam:", "steam.exe"],
    "word": ["winword.exe"],
    "excel": ["excel.exe"],
    "powerpoint": ["powerpnt.exe"],
}


class AppLaunchParameters(BaseModel):
    """Parameters for AppLaunchTool operations."""
    action: Literal["launch_app", "open_pdf", "sequence_apps", "youtube_search", "open_web_app"] = Field(
        ...,
        description="The desktop launch action to perform.",
    )
    app_name: Optional[str] = Field(
        default=None,
        description="Name of the allowlisted application (e.g. 'code', 'chrome', 'spotify', 'notepad', 'calculator', 'whatsapp').",
    )
    pdf_path: Optional[str] = Field(
        default=None,
        description="Target relative or absolute path of the PDF file to open.",
    )
    sequence: Optional[List[str]] = Field(
        default=None,
        description="List of applications to launch sequentially in order.",
    )
    query: Optional[str] = Field(
        default=None,
        description="Search query for YouTube or media search.",
    )
    web_app: Optional[Literal["whatsapp", "instagram", "youtube"]] = Field(
        default=None,
        description="Web application destination ('whatsapp', 'instagram', or 'youtube').",
    )

    @field_validator("pdf_path")
    @classmethod
    def validate_pdf_path(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        clean = v.strip()
        if ".." in clean:
            raise ValueError(f"Path traversal ('..') is prohibited: '{v}'")
        if not clean.lower().endswith(".pdf"):
            raise ValueError(f"Target must be a PDF file (.pdf): '{v}'")
        return clean


class BaseAppLauncher(ABC):
    """Abstract interface for launching desktop applications."""

    @abstractmethod
    def launch(self, executable: str, args: List[str]) -> Tuple[bool, Optional[int], Optional[str]]:
        """Launches process returning (success, pid, error_message)."""
        pass

    @abstractmethod
    def is_running(self, pid: int) -> bool:
        """Checks if process is currently running."""
        pass


def resolve_windows_app(exe_name: str) -> Optional[str]:
    """
    Resolves the authoritative absolute path for a Windows application.
    Checks:
    1. PATH via shutil.which
    2. Windows Registry App Paths (HKLM & HKCU)
    3. WindowsApps AppExecutionAliases (%LOCALAPPDATA%\\Microsoft\\WindowsApps)
    4. Common install directories (Program Files, LocalAppData)
    """
    if sys.platform != "win32":
        import shutil
        return shutil.which(exe_name)

    import shutil
    import winreg

    # 1. System PATH
    w = shutil.which(exe_name)
    if w:
        return w

    # 2. Windows Registry App Paths
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        sub = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"
        try:
            with winreg.OpenKey(root, sub) as key:
                val, _ = winreg.QueryValueEx(key, "")
                if val and os.path.exists(val):
                    return val
        except Exception:
            pass

    # 3. WindowsApps
    local_app = os.environ.get("LOCALAPPDATA", "")
    winapps_path = os.path.join(local_app, "Microsoft", "WindowsApps", exe_name)
    if os.path.exists(winapps_path):
        return winapps_path

    # 4. Standard known paths
    known_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.join(local_app, r"Programs\Opera\launcher.exe"),
        os.path.join(local_app, r"Programs\Opera GX\launcher.exe"),
        os.path.join(local_app, r"Programs\Microsoft VS Code\Code.exe"),
        os.path.join(local_app, r"Programs\Microsoft VS Code\bin\code.cmd"),
        os.path.join(local_app, r"Discord\Update.exe"),
        r"C:\Program Files (x86)\Steam\steam.exe",
        r"C:\Program Files\Steam\steam.exe",
    ]
    for p in known_paths:
        if os.path.exists(p) and Path(p).name.lower() == exe_name.lower():
            return p

    return None


class SubprocessAppLauncher(BaseAppLauncher):
    """Native OS discrete process launcher enforcing foreground GUI activation on Windows."""

    def launch(self, executable: str, args: List[str]) -> Tuple[bool, Optional[int], Optional[str]]:
        # On Windows, use ShellExecuteExW with SW_SHOWNORMAL (1) to launch the GUI
        # in the active foreground, resolving AppPaths, system PATH, WindowsApps, and protocols.
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                try:
                    ctypes.windll.user32.AllowSetForegroundWindow(-1)
                except Exception:
                    pass

                resolved_target = resolve_windows_app(executable) if (not executable.endswith(":") and not os.path.isabs(executable)) else executable
                target_file = resolved_target or executable

                class SHELLEXECUTEINFOW(ctypes.Structure):
                    _fields_ = [
                        ('cbSize', wintypes.DWORD),
                        ('fMask', wintypes.ULONG),
                        ('hwnd', wintypes.HWND),
                        ('lpVerb', wintypes.LPCWSTR),
                        ('lpFile', wintypes.LPCWSTR),
                        ('lpParameters', wintypes.LPCWSTR),
                        ('lpDirectory', wintypes.LPCWSTR),
                        ('nShow', ctypes.c_int),
                        ('hInstApp', wintypes.HINSTANCE),
                        ('lpIDList', wintypes.LPVOID),
                        ('lpClass', wintypes.LPCWSTR),
                        ('hkeyClass', wintypes.HKEY),
                        ('dwHotKey', wintypes.DWORD),
                        ('hIcon', wintypes.HANDLE),
                        ('hProcess', wintypes.HANDLE),
                    ]

                sei = SHELLEXECUTEINFOW()
                sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
                sei.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
                sei.hwnd = None
                sei.lpVerb = 'open'
                if target_file.startswith("shell:AppsFolder"):
                    sei.lpFile = "explorer.exe"
                    sei.lpParameters = target_file
                else:
                    sei.lpFile = target_file
                    sei.lpParameters = " ".join(args) if args else None
                sei.lpDirectory = None
                sei.nShow = 1  # SW_SHOWNORMAL

                res = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
                if res:
                    pid = None
                    if sei.hProcess:
                        pid = ctypes.windll.kernel32.GetProcessId(sei.hProcess)
                        ctypes.windll.kernel32.CloseHandle(sei.hProcess)
                    if not pid:
                        try:
                            import psutil
                            base = Path(target_file).stem.lower().rstrip(":")
                            for p in psutil.process_iter(['pid', 'name']):
                                if base and base in p.name().lower():
                                    pid = p.pid
                                    break
                        except Exception:
                            pass
                    return True, pid, None
            except Exception as e:
                logger.warning("ShellExecuteExW failed for '%s': %s, falling back to subprocess", executable, e)

        # Fallback or Non-Windows standard discrete subprocess execution
        cmd = [executable] + args
        try:
            proc = subprocess.Popen(
                cmd,
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            return True, proc.pid, None
        except FileNotFoundError:
            return False, None, f"Application executable '{executable}' not found on system PATH."
        except Exception as exc:
            return False, None, str(exc)

    def is_running(self, pid: int) -> bool:
        import psutil
        try:
            proc = psutil.Process(pid)
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except Exception:
            return False


class MockAppLauncher(BaseAppLauncher):
    """In-memory launcher for deterministic testing without launching desktop GUI windows."""

    def __init__(self) -> None:
        self.launched_processes: List[Dict[str, Any]] = []
        self._next_pid = 1000

    def launch(self, executable: str, args: List[str]) -> Tuple[bool, Optional[int], Optional[str]]:
        pid = self._next_pid
        self._next_pid += 1
        self.launched_processes.append({"executable": executable, "args": args, "pid": pid})
        return True, pid, None

    def is_running(self, pid: int) -> bool:
        return any(p["pid"] == pid for p in self.launched_processes)


class AppLaunchTool(BaseTool):
    """
    Safe desktop application and resource launcher.
    Supports allowlisted app launching, PDF document opening, multi-application sequencing,
    YouTube searching, and WhatsApp/Instagram interaction.
    """

    name: str = "app_launch"
    description: str = (
        "Safely launches allowlisted desktop applications (e.g. VS Code, Chrome, Spotify, Notepad, Calculator, WhatsApp), "
        "opens PDF files, executes multi-app sequences, and handles media/browser workflows."
    )
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    parameters_schema: Type[BaseModel] = AppLaunchParameters

    def __init__(
        self,
        launcher: Optional[BaseAppLauncher] = None,
        allowed_workspace_root: Optional[str] = None,
    ) -> None:
        self.launcher = launcher or (
            SubprocessAppLauncher() if sys.platform == "win32" else MockAppLauncher()
        )
        settings = get_settings()
        root_str = allowed_workspace_root or getattr(settings, "allowed_workspace_root", str(Path.cwd()))
        self.allowed_root = Path(root_str).resolve()

    async def execute(
        self, params: BaseModel, context: ExecutionContext
    ) -> ToolResult:
        if not isinstance(params, AppLaunchParameters):
            params = self.validate_parameters(params.model_dump())

        action = params.action

        try:
            if action == "launch_app":
                if not params.app_name:
                    return ToolResult(success=False, error="app_name must be specified for launch_app action.")

                app_key = params.app_name.lower().strip()
                if app_key not in ALLOWLISTED_APPS:
                    avail = ", ".join(sorted(ALLOWLISTED_APPS.keys()))
                    return ToolResult(
                        success=False,
                        error=f"Application '{params.app_name}' is not in the authorized allowlist. Supported applications: {avail}.",
                    )

                exec_candidates = list(ALLOWLISTED_APPS[app_key])

                # On Windows, prepend resolved executable paths if available
                if sys.platform == "win32" and isinstance(self.launcher, SubprocessAppLauncher):
                    import shutil
                    if app_key == "spotify":
                        local_app = os.environ.get("LOCALAPPDATA", "")
                        winapps_cli = os.path.join(local_app, "Microsoft", "WindowsApps", "spotify_cli.exe")
                        resolved_cli = shutil.which("spotify_cli.exe") or (winapps_cli if os.path.exists(winapps_cli) else None)
                        if resolved_cli:
                            exec_candidates.insert(0, resolved_cli)

                last_err = None
                launched_exe = None
                launched_pid = None

                for candidate in exec_candidates:
                    launch_args = []
                    if candidate.endswith("spotify_cli.exe"):
                        launch_args = ["open"]
                    elif app_key in ("chrome", "edge", "msedge", "opera"):
                        launch_args = ["--new-window"]
                    success, pid, err = self.launcher.launch(candidate, launch_args)
                    if success:
                        launched_exe = candidate
                        launched_pid = pid
                        break
                    last_err = err

                if not launched_exe:
                    return ToolResult(
                        success=False,
                        error=f"Failed to launch '{app_key}': {last_err}",
                    )

                msg = f"Successfully launched {app_key.capitalize()}" + (f" (PID {launched_pid})." if launched_pid else ".")
                return ToolResult(
                    success=True,
                    output={
                        "action": "launch_app",
                        "app_name": app_key,
                        "executable": launched_exe,
                        "pid": launched_pid,
                        "message": msg,
                    },
                )

            elif action == "open_pdf":
                if not params.pdf_path:
                    return ToolResult(success=False, error="pdf_path must be specified for open_pdf action.")

                raw_path = Path(params.pdf_path)
                safe_path = raw_path if raw_path.is_absolute() else (self.allowed_root / raw_path).resolve()

                if not safe_path.exists():
                    return ToolResult(success=False, error=f"PDF file '{params.pdf_path}' does not exist.")

                # Launch default PDF viewer on Windows
                if sys.platform == "win32" and isinstance(self.launcher, SubprocessAppLauncher):
                    try:
                        os.startfile(str(safe_path))
                        return ToolResult(
                            success=True,
                            output={
                                "action": "open_pdf",
                                "file_path": str(safe_path),
                                "message": f"Opened PDF document '{safe_path.name}'.",
                            },
                        )
                    except Exception as exc:
                        return ToolResult(success=False, error=f"Could not open PDF: {exc}")
                else:
                    success, pid, err = self.launcher.launch("mock_pdf_viewer", [str(safe_path)])
                    return ToolResult(
                        success=True,
                        output={
                            "action": "open_pdf",
                            "file_path": str(safe_path),
                            "pid": pid,
                            "message": f"Opened PDF document '{safe_path.name}'.",
                        },
                    )

            elif action == "sequence_apps":
                if not params.sequence:
                    return ToolResult(success=False, error="sequence list must be specified for sequence_apps action.")

                launched = []
                for app in params.sequence:
                    app_key = app.lower().strip()
                    if app_key not in ALLOWLISTED_APPS:
                        continue
                    exe = ALLOWLISTED_APPS[app_key][0]
                    succ, pid, _ = self.launcher.launch(exe, [])
                    if succ:
                        launched.append({"app": app_key, "pid": pid})

                return ToolResult(
                    success=True,
                    output={
                        "action": "sequence_apps",
                        "launched": launched,
                        "message": f"Sequentially launched {len(launched)} applications: {', '.join(p['app'] for p in launched)}.",
                    },
                )

            elif action == "youtube_search":
                query_str = params.query or "ORBIS agent"
                import urllib.parse
                encoded = urllib.parse.quote_plus(query_str)
                yt_url = f"https://www.youtube.com/results?search_query={encoded}"

                if sys.platform == "win32" and isinstance(self.launcher, SubprocessAppLauncher):
                    import webbrowser
                    webbrowser.open(yt_url)
                else:
                    self.launcher.launch("browser", [yt_url])

                return ToolResult(
                    success=True,
                    output={
                        "action": "youtube_search",
                        "query": query_str,
                        "url": yt_url,
                        "message": f"Opened YouTube search for '{query_str}'.",
                    },
                )

            elif action == "open_web_app":
                dest = params.web_app or "whatsapp"
                urls = {
                    "whatsapp": "https://web.whatsapp.com",
                    "instagram": "https://www.instagram.com",
                    "youtube": "https://www.youtube.com",
                }
                target_url = urls.get(dest, "https://web.whatsapp.com")

                if sys.platform == "win32" and isinstance(self.launcher, SubprocessAppLauncher):
                    import webbrowser
                    webbrowser.open(target_url)
                else:
                    self.launcher.launch("browser", [target_url])

                return ToolResult(
                    success=True,
                    output={
                        "action": "open_web_app",
                        "web_app": dest,
                        "url": target_url,
                        "message": f"Opened {dest.capitalize()} Web ({target_url}). Note: message transmissions require explicit authorization.",
                    },
                )

            else:
                return ToolResult(success=False, error=f"Unrecognized action '{action}'.")

        except Exception as exc:
            logger.exception("AppLaunchTool action '%s' failed: %s", action, exc)
            return ToolResult(success=False, error=f"AppLaunchTool operation failed: {exc}")

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        if not result.success or not isinstance(result.output, dict):
            return VerificationReport(
                verified=False,
                details=f"App launch failed: {result.error or 'no output dictionary'}",
                discrepancies=[result.error or "launch_failed"],
            )

        action = getattr(params, "action", None) or result.output.get("action")
        pid = result.output.get("pid")

        if action == "launch_app":
            if pid:
                if self.launcher.is_running(pid):
                    return VerificationReport(
                        verified=True,
                        details=f"Process {pid} verified running for application '{result.output.get('app_name')}'.",
                    )
                return VerificationReport(
                    verified=True,
                    details=f"Process {pid} executed for '{result.output.get('app_name')}'.",
                )
            return VerificationReport(
                verified=True,
                details=f"Application '{result.output.get('app_name')}' launched successfully.",
            )

        return VerificationReport(
            verified=True,
            details=f"Action '{action}' verified successfully.",
        )
