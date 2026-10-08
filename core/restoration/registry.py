"""
Trusted Application Registry for ORBIS Phase 10 Workspace Restoration.
Provides safe, canonical executable path resolution for an explicit allowlist
of supported desktop applications without relying on ambient PATH lookup.
"""

import os
from pathlib import Path
from typing import Callable, Dict, List, Optional
from pydantic import BaseModel, Field

from core.restoration.models import ResourceTarget, ResourceTargetType


class AppDescriptor(BaseModel):
    """
    Descriptor for an approved, registered desktop application.
    Enforces that binaries are resolved only from verified canonical paths.
    """
    identifier: str
    display_name: str
    process_names: List[str]
    canonical_paths: List[str]
    supported_targets: List[ResourceTargetType]
    is_safe_default: bool = True
    override_executable: Optional[str] = None

    def resolve_trusted_executable(self) -> Optional[Path]:
        """
        Resolves the application binary strictly against canonical installation paths.
        Does NOT fall back to arbitrary ambient PATH lookups.
        """
        if self.override_executable:
            p = Path(self.override_executable).resolve()
            if p.is_file() and os.access(p, os.X_OK):
                return p

        for raw_path in self.canonical_paths:
            expanded = os.path.expandvars(raw_path)
            if "*" in expanded:
                import glob
                matches = glob.glob(expanded)
                for m in sorted(matches, reverse=True):
                    candidate = Path(m).resolve()
                    if candidate.is_file() and os.access(candidate, os.X_OK):
                        return candidate
            else:
                candidate = Path(expanded).resolve()
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return candidate

        # On Windows, query authoritative Registry App Paths without ambient PATH lookup
        if os.name == "nt":
            try:
                import winreg
                for p_name in self.process_names:
                    if not p_name.lower().endswith(".exe"):
                        continue
                    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                        sub = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{p_name}"
                        try:
                            with winreg.OpenKey(root, sub) as key:
                                val, _ = winreg.QueryValueEx(key, "")
                                if val:
                                    cand = Path(val).resolve()
                                    if cand.is_file() and os.access(cand, os.X_OK):
                                        return cand
                        except Exception:
                            pass
            except Exception:
                pass

        return None

    def build_command_args(self, target: Optional[ResourceTarget]) -> List[str]:
        """
        Builds discrete argument list for launching the application with a target.
        Strictly discrete arguments (shell=False).
        Guarantees that even pure layout applications without explicit resource targets
        open visible windows (e.g. Chrome opens --new-window, Explorer opens workspace root).
        """
        from core.config import get_settings
        settings = get_settings()
        default_root = (
            settings.allowed_workspace_root
            if (settings.allowed_workspace_root and os.path.isdir(settings.allowed_workspace_root))
            else os.getcwd()
        )

        args: List[str] = []
        target_val = (target.resolved_target or target.raw_target) if target else None

        if self.identifier in ("vscode", "antigravity"):
            # code "<path>" or antigravity "<path>"
            if target_val:
                args.extend([target_val])
            else:
                args.extend([default_root])
        elif self.identifier == "terminal":
            # wt -d "<dir>"
            dir_target = target_val if (target and target.target_type == ResourceTargetType.DIRECTORY) else default_root
            args.extend(["-d", dir_target])
        elif self.identifier in ("chrome", "edge"):
            # browser --new-window "<url>"
            url_target = target_val if (target and target.target_type == ResourceTargetType.WEB_URL) else "about:blank"
            args.extend(["--new-window", url_target])
        elif self.identifier == "notepad":
            # notepad "<file>"
            if target_val:
                args.extend([target_val])
        elif self.identifier == "explorer":
            # explorer "<dir>"
            dir_target = target_val if target_val else default_root
            args.extend([dir_target])
        elif self.identifier == "snippingtool":
            pass
        else:
            if target_val:
                args.extend([target_val])

        return args


class ApplicationRegistry:
    """
    Authoritative registry of pre-approved applications for workspace restoration.
    Rejects unregistered binaries and arbitrary user-supplied executable paths.
    """

    def __init__(self, apps: Optional[Dict[str, AppDescriptor]] = None) -> None:
        self._apps: Dict[str, AppDescriptor] = apps or self._build_default_apps()

    @classmethod
    def _build_default_apps(cls) -> Dict[str, AppDescriptor]:
        """Constructs the standard allowlist of supported applications."""
        defaults = [
            AppDescriptor(
                identifier="vscode",
                display_name="Visual Studio Code",
                process_names=["Code.exe", "code.exe"],
                canonical_paths=[
                    r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
                    r"%ProgramFiles%\Microsoft VS Code\Code.exe",
                    r"%ProgramFiles(x86)%\Microsoft VS Code\Code.exe",
                ],
                supported_targets=[ResourceTargetType.WORKSPACE_FILE, ResourceTargetType.DIRECTORY],
            ),
            AppDescriptor(
                identifier="antigravity",
                display_name="Antigravity IDE",
                process_names=["Antigravity IDE.exe", "Antigravity IDE", "Antigravity.exe", "antigravity.exe", "antigravity"],
                canonical_paths=[
                    r"%LOCALAPPDATA%\Programs\Antigravity IDE\Antigravity IDE.exe",
                    r"%LOCALAPPDATA%\Programs\antigravity\Antigravity.exe",
                    r"%ProgramFiles%\Antigravity IDE\Antigravity IDE.exe",
                    r"%ProgramFiles%\Antigravity\Antigravity.exe",
                ],
                supported_targets=[ResourceTargetType.WORKSPACE_FILE, ResourceTargetType.DIRECTORY],
            ),
            AppDescriptor(
                identifier="terminal",
                display_name="Windows Terminal",
                process_names=["WindowsTerminal.exe", "wt.exe", "powershell.exe", "cmd.exe"],
                canonical_paths=[
                    r"%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe",
                    r"%ProgramFiles%\WindowsApps\Microsoft.WindowsTerminal_*\wt.exe",
                    r"%SystemRoot%\System32\cmd.exe",
                    r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe",
                ],
                supported_targets=[ResourceTargetType.DIRECTORY],
            ),
            AppDescriptor(
                identifier="chrome",
                display_name="Google Chrome",
                process_names=["chrome.exe"],
                canonical_paths=[
                    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
                    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
                    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
                ],
                supported_targets=[ResourceTargetType.WEB_URL],
            ),
            AppDescriptor(
                identifier="edge",
                display_name="Microsoft Edge",
                process_names=["msedge.exe"],
                canonical_paths=[
                    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
                    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
                ],
                supported_targets=[ResourceTargetType.WEB_URL, ResourceTargetType.WORKSPACE_FILE],
            ),
            AppDescriptor(
                identifier="notepad",
                display_name="Notepad",
                process_names=["notepad.exe"],
                canonical_paths=[
                    r"%SystemRoot%\System32\notepad.exe",
                    r"%SystemRoot%\notepad.exe",
                    r"%LOCALAPPDATA%\Microsoft\WindowsApps\notepad.exe",
                ],
                supported_targets=[ResourceTargetType.WORKSPACE_FILE],
            ),
            AppDescriptor(
                identifier="explorer",
                display_name="File Explorer",
                process_names=["explorer.exe", "Explorer.EXE"],
                canonical_paths=[
                    r"%SystemRoot%\explorer.exe",
                    r"C:\Windows\explorer.exe",
                ],
                supported_targets=[ResourceTargetType.DIRECTORY, ResourceTargetType.WORKSPACE_FILE],
            ),
            AppDescriptor(
                identifier="spotify",
                display_name="Spotify",
                process_names=["Spotify.exe", "spotify.exe"],
                canonical_paths=[
                    r"%APPDATA%\Spotify\Spotify.exe",
                    r"%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe",
                    r"%ProgramFiles%\WindowsApps\SpotifyAB.SpotifyMusic_*\Spotify.exe",
                    r"%ProgramFiles%\Spotify\Spotify.exe",
                ],
                supported_targets=[ResourceTargetType.NONE, ResourceTargetType.WEB_URL],
            ),
            AppDescriptor(
                identifier="calculator",
                display_name="Calculator",
                process_names=["CalculatorApp.exe", "calc.exe", "Calculator.exe"],
                canonical_paths=[
                    r"%SystemRoot%\System32\calc.exe",
                    r"C:\Windows\System32\calc.exe",
                    r"%LOCALAPPDATA%\Microsoft\WindowsApps\calc.exe",
                ],
                supported_targets=[ResourceTargetType.NONE],
            ),
            AppDescriptor(
                identifier="snippingtool",
                display_name="Snipping Tool",
                process_names=["SnippingTool.exe", "snippingtool.exe", "SnippingTool"],
                canonical_paths=[
                    r"%LOCALAPPDATA%\Microsoft\WindowsApps\SnippingTool.exe",
                    r"%ProgramFiles%\WindowsApps\Microsoft.ScreenSketch_*\SnippingTool.exe",
                    r"%SystemRoot%\System32\SnippingTool.exe",
                ],
                supported_targets=[ResourceTargetType.NONE, ResourceTargetType.WORKSPACE_FILE],
            ),
        ]
        return {app.identifier: app for app in defaults}

    def get(self, identifier: str) -> Optional[AppDescriptor]:
        """Retrieves an AppDescriptor by its unique identifier."""
        return self._apps.get(identifier.lower().strip())

    def get_by_process_name(self, process_name: str) -> Optional[AppDescriptor]:
        """Resolves an observed process executable name to its approved AppDescriptor."""
        clean = process_name.lower().strip()
        for app in self._apps.values():
            if any(p.lower() == clean for p in app.process_names):
                return app
        return None

    def register_app(self, descriptor: AppDescriptor) -> None:
        """Registers or overrides an application descriptor (useful for testing)."""
        self._apps[descriptor.identifier.lower().strip()] = descriptor

    def list_apps(self) -> List[AppDescriptor]:
        """Returns all registered application descriptors."""
        return list(self._apps.values())

    def is_allowed(self, identifier: str) -> bool:
        """Checks whether the application identifier is in the approved registry."""
        return identifier.lower().strip() in self._apps
