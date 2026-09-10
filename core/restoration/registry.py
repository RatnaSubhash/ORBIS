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
            candidate = Path(expanded).resolve()
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate

        return None

    def build_command_args(self, target: Optional[ResourceTarget]) -> List[str]:
        """
        Builds discrete argument list for launching the application with a target.
        Strictly discrete arguments (shell=False).
        """
        args: List[str] = []

        if not target or not target.resolved_target or target.target_type == ResourceTargetType.NONE:
            return args

        if self.identifier == "vscode":
            # code "<path>"
            args.extend([target.resolved_target])
        elif self.identifier == "terminal":
            # wt -d "<dir>"
            if target.target_type == ResourceTargetType.DIRECTORY:
                args.extend(["-d", target.resolved_target])
        elif self.identifier in ("chrome", "edge"):
            # browser --new-window "<url>"
            args.extend(["--new-window", target.resolved_target])
        elif self.identifier == "notepad":
            # notepad "<file>"
            args.extend([target.resolved_target])
        else:
            args.extend([target.resolved_target])

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
                identifier="terminal",
                display_name="Windows Terminal",
                process_names=["WindowsTerminal.exe", "wt.exe"],
                canonical_paths=[
                    r"%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe",
                    r"%ProgramFiles%\WindowsApps\Microsoft.WindowsTerminal_*\wt.exe",
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
                ],
                supported_targets=[ResourceTargetType.WORKSPACE_FILE],
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
