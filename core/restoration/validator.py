"""
Preflight Safety and Boundary Validator for ORBIS Phase 10 Workspace Restoration.
Enforces canonical path containment, strict http/https URL scheme and SSRF validation,
trusted executable verification, and virtual desktop window geometry sanity.
"""

from pathlib import Path
import re
from typing import Any, List, Optional, Tuple
from urllib.parse import urlparse

from core.config import get_settings
from core.restoration.models import (
    ResourceTarget,
    ResourceTargetType,
    RestorationPlan,
    RestorationStep,
    RestorationStepStatus,
    VirtualDesktopGeometry,
    WindowBounds,
)
from core.restoration.registry import ApplicationRegistry
from core.tools.web_research_tool import validate_and_sanitize_url
from core.types import RiskLevel


WINDOWS_DEVICE_NAMES = {
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
}

DISALLOWED_URI_SCHEMES = {
    "file", "javascript", "data", "vbscript", "chrome", "edge", "about", "shell", "ms-settings",
}


class PreflightValidator:
    """
    Validates all aspects of a RestorationPlan before any process execution.
    Isolates missing/invalid resources so that partial restoration can safely proceed.
    """

    def __init__(
        self,
        registry: Optional[ApplicationRegistry] = None,
        allowed_workspace_root: Optional[str] = None,
        settings: Optional[Any] = None,
    ) -> None:
        self.registry = registry or ApplicationRegistry()
        self.settings = settings or get_settings()
        root_str = allowed_workspace_root or self.settings.allowed_workspace_root
        self.allowed_root = Path(root_str).resolve()

    def validate_plan(
        self, plan: RestorationPlan, virtual_geometry: Optional[VirtualDesktopGeometry] = None
    ) -> RestorationPlan:
        """
        Validates all steps in the restoration plan, populating resolved targets,
        updating step statuses, and computing intrinsic risk and approval policy.
        """
        geometry = virtual_geometry or VirtualDesktopGeometry()
        errors: List[str] = []
        has_executable_steps = False
        has_external_urls = False

        for step in plan.steps:
            # 1. Validate Application
            app_desc = self.registry.get(step.app_identifier)
            if not app_desc:
                step.status = RestorationStepStatus.SKIPPED_INVALID
                step.status_message = f"Application '{step.app_identifier}' is not in the approved registry."
                errors.append(step.status_message)
                continue

            trusted_exe = app_desc.resolve_trusted_executable()
            if not trusted_exe:
                step.status = RestorationStepStatus.SKIPPED_INVALID
                step.status_message = f"Executable for '{step.app_identifier}' not found in canonical locations on disk."
                errors.append(step.status_message)
                continue

            step.command_executable = str(trusted_exe)

            # 2. Validate Target Resource (if present)
            if step.resource and step.resource.target_type != ResourceTargetType.NONE:
                res_valid, resolved_target, err_msg = self.validate_resource_target(step.resource)
                step.resource.is_valid = res_valid
                step.resource.resolved_target = resolved_target
                step.resource.validation_error = err_msg

                if not res_valid:
                    step.status = RestorationStepStatus.SKIPPED_INVALID
                    step.status_message = err_msg or "Resource failed validation."
                    errors.append(f"Step '{step.step_id}': {step.status_message}")
                    continue

                if step.resource.target_type == ResourceTargetType.WEB_URL:
                    has_external_urls = True

            # 3. Build Discrete Command Arguments
            step.command_args = app_desc.build_command_args(step.resource)

            # 4. Validate and Clamp Window Bounds (if present)
            if step.target_window_state and step.target_window_state.bounds:
                clamped_bounds = self.validate_and_clamp_window_bounds(
                    step.target_window_state.bounds, geometry
                )
                step.target_window_state.bounds = clamped_bounds

            # Step is valid for execution
            step.status = RestorationStepStatus.PENDING
            step.status_message = None
            has_executable_steps = True

        # 5. Compute Intrinsic Risk Level
        # Pure local approved apps -> LOW. External URLs -> MEDIUM. Privileged -> HIGH.
        if has_external_urls:
            plan.overall_risk = RiskLevel.MEDIUM
        else:
            plan.overall_risk = RiskLevel.LOW

        # 6. Evaluate Policy-Based Approval Flag
        # Multi-app restoration requires user approval by policy, independent of intrinsic risk
        valid_steps_count = sum(1 for s in plan.steps if s.status == RestorationStepStatus.PENDING)
        plan.requires_user_approval = (
            valid_steps_count > 1 or self.settings.restoration_require_approval
        )

        plan.is_preflight_valid = has_executable_steps
        plan.preflight_errors = errors
        return plan

    def validate_resource_target(
        self, target: ResourceTarget
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Validates a single resource target according to its type.
        Returns: (is_valid, resolved_target, error_message)
        """
        raw = target.raw_target.strip()
        if not raw:
            return False, None, "Empty resource target."

        # Case A: Web URL
        if target.target_type == ResourceTargetType.WEB_URL:
            return self._validate_web_url(raw)

        # Case B: Local Workspace File or Directory
        if target.target_type in (ResourceTargetType.WORKSPACE_FILE, ResourceTargetType.DIRECTORY):
            return self._validate_local_path(raw, target.target_type)

        # Case C: Unknown / Unresolved Resource Type
        if target.target_type == ResourceTargetType.UNKNOWN:
            return False, None, f"Resource target '{raw}' is of unknown or ambiguous type and cannot be safely resolved."

        return True, raw, None

    def _validate_web_url(self, raw_url: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """Strictly validates web URLs: requires http/https and enforces SSRF blocks."""
        parsed = urlparse(raw_url)
        scheme = parsed.scheme.lower()

        if scheme in DISALLOWED_URI_SCHEMES:
            return False, None, f"URI scheme '{scheme}:' is strictly prohibited for browser restoration."

        if scheme not in ("http", "https"):
            return False, None, f"Only 'http' and 'https' schemes are permitted (got '{scheme}:')."

        try:
            sanitized = validate_and_sanitize_url(raw_url)
            return True, sanitized, None
        except Exception as e:
            return False, None, f"URL rejected by security policy: {e}"

    def _validate_local_path(
        self, raw_path: str, target_type: ResourceTargetType
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Enforces canonical workspace root containment, traversal checks, and device names."""
        # 1. Traversal syntax check
        if ".." in raw_path:
            return False, None, f"Directory traversal ('..') is strictly prohibited in path: '{raw_path}'."

        # 2. UNC syntax check
        if raw_path.startswith("//") or raw_path.startswith(r"\\"):
            return False, None, f"UNC network paths are prohibited: '{raw_path}'."

        # 3. Windows device names
        candidate = Path(raw_path)
        stem = candidate.stem.lower()
        if stem in WINDOWS_DEVICE_NAMES:
            return False, None, f"Windows reserved device name '{stem}' is prohibited."

        # 4. Canonical resolution against workspace root
        if not candidate.is_absolute():
            resolved = (self.allowed_root / candidate).resolve()
        else:
            resolved = candidate.resolve()

        # 5. Boundary containment check
        if resolved != self.allowed_root and self.allowed_root not in resolved.parents:
            return False, None, f"Path safety violation: '{raw_path}' resolves outside allowed root '{self.allowed_root}'."

        # 6. Physical existence check
        if not resolved.exists():
            return False, None, f"Target path '{resolved}' does not exist on disk."

        if target_type == ResourceTargetType.WORKSPACE_FILE and not resolved.is_file():
            return False, None, f"Expected file but found directory at '{resolved}'."

        if target_type == ResourceTargetType.DIRECTORY and not resolved.is_dir():
            return False, None, f"Expected directory but found file at '{resolved}'."

        # 7. Privacy exclusion check
        if self.settings.is_excluded(None, str(resolved)):
            return False, None, f"Target path '{resolved}' is excluded by privacy policy."

        return True, str(resolved), None

    def validate_and_clamp_window_bounds(
        self, bounds: WindowBounds, geometry: VirtualDesktopGeometry
    ) -> WindowBounds:
        """
        Validates window coordinates against virtual desktop geometry.
        Preserves valid negative coordinates in multi-monitor setups.
        Clamps only if window falls entirely outside the virtual display rectangle.
        """
        # Clamp dimensions to sane minimums and maximums
        w = max(200, min(bounds.width, geometry.width))
        h = max(200, min(bounds.height, geometry.height))

        x = bounds.x
        y = bounds.y

        # If window is entirely off to the left or right of virtual desktop
        if x + w <= geometry.left:
            x = geometry.left + 50
        elif x >= geometry.right:
            x = geometry.right - w - 50

        # If window is entirely above or below virtual desktop
        if y + h <= geometry.top:
            y = geometry.top + 50
        elif y >= geometry.bottom:
            y = geometry.bottom - h - 50

        return WindowBounds(x=x, y=y, width=w, height=h)
