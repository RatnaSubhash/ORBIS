"""
Declarative Workspace Restoration Engine for ORBIS Phase 10.
Builds, validates, executes, and verifies multi-application workspace restoration
plans grounded in Phase 9 Intent Continuity Engine evidence.
"""

import asyncio
from datetime import datetime, timezone
import inspect
import logging
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from core.config import get_settings
from core.ice.engine import IntentContinuityEngine
from core.ice.models import ICEResult, IntentCandidate
from core.memory.db import DatabaseManager
from core.restoration.models import (
    ResourceTarget,
    ResourceTargetType,
    RestorationPlan,
    RestorationResult,
    RestorationStep,
    RestorationStepResult,
    RestorationStepStatus,
    RestorationVerification,
    WindowBounds,
    WindowPlacementState,
    WindowState,
    WorkspaceSnapshot,
)
from core.restoration.registry import ApplicationRegistry
from core.restoration.validator import PreflightValidator
from core.restoration.window_manager import BaseWindowManager, MockWindowManager, WindowsWindowManager
from core.types import RiskLevel, Session

logger = logging.getLogger("orbis.restoration.engine")


ProcessLauncher = Callable[[str, List[str]], Tuple[bool, Optional[int], Optional[str]]]


class RestorationEngine:
    """
    Orchestrates the conversion of Phase 9 ICE intent context into a declarative,
    preflight-validated RestorationPlan, executing it within strict safety and approval boundaries.
    """

    def __init__(
        self,
        db: DatabaseManager,
        ice_engine: Optional[IntentContinuityEngine] = None,
        registry: Optional[ApplicationRegistry] = None,
        validator: Optional[PreflightValidator] = None,
        window_manager: Optional[BaseWindowManager] = None,
        process_launcher: Optional[ProcessLauncher] = None,
    ) -> None:
        self.db = db
        self.ice_engine = ice_engine or IntentContinuityEngine(db=db)
        self.registry = registry or ApplicationRegistry()
        self.validator = validator or PreflightValidator(registry=self.registry)
        self.window_manager = window_manager or (
            WindowsWindowManager() if os.name == "nt" else MockWindowManager()
        )
        self.process_launcher = process_launcher or self._default_process_launcher

    @staticmethod
    def _default_process_launcher(
        executable: str, args: List[str]
    ) -> Tuple[bool, Optional[int], Optional[str]]:
        """
        Launches an approved application process strictly with discrete argument lists (shell=False).
        Returns: (success, process_id, error_message)
        """
        cmd = [executable] + args
        try:
            # shell=False is strictly enforced
            proc = subprocess.Popen(
                cmd,
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            return True, proc.pid, None
        except Exception as e:
            return False, None, str(e)

    async def create_restoration_plan(
        self, query: str
    ) -> Tuple[Optional[RestorationPlan], Optional[ICEResult], str]:
        """
        Uses Phase 9 ICE to infer relevant session context, constructs a WorkspaceSnapshot,
        and generates a preflight-validated declarative RestorationPlan.
        """
        # 1. Query Phase 9 ICE for intent and relevant session provenance
        ice_result: ICEResult = await self.ice_engine.infer_intent(query)
        if not ice_result.has_sufficient_evidence or not ice_result.primary_intent:
            return None, ice_result, "Insufficient evidence to identify relevant workspace session."

        primary = ice_result.primary_intent
        session_id = primary.provenance.session_ids[0] if primary.provenance.session_ids else None
        if not session_id:
            return None, ice_result, "No session identifier found in intent provenance."

        # 2. Extract session snapshot
        snapshot = self.build_snapshot_from_session(session_id, primary)
        if not snapshot:
            return None, ice_result, f"Failed to reconstruct workspace snapshot for session '{session_id}'."

        # 3. Build declarative RestorationPlan
        raw_plan = self.build_plan_from_snapshot(snapshot, primary.goal_description)

        # 4. Preflight Validate Plan
        virtual_geom = self.window_manager.get_virtual_desktop_geometry()
        validated_plan = self.validator.validate_plan(raw_plan, virtual_geom)

        return validated_plan, ice_result, "Plan created and preflight-validated successfully."

    def build_snapshot_from_session(
        self, session_id: str, intent: IntentCandidate
    ) -> Optional[WorkspaceSnapshot]:
        """Constructs a declarative WorkspaceSnapshot from stored SQLite session evidence."""
        session = self.db.get_session(session_id)
        if not session:
            return None

        # Build ResourceTargets from session resources
        resource_targets: List[ResourceTarget] = []
        for r in session.resources:
            r_clean = r.strip()
            if r_clean.startswith("http://") or r_clean.startswith("https://"):
                t_type = ResourceTargetType.WEB_URL
            elif Path(r_clean).is_dir() if Path(r_clean).exists() else ("\\" not in r_clean and "/" not in r_clean and "." not in r_clean):
                t_type = ResourceTargetType.DIRECTORY
            else:
                t_type = ResourceTargetType.WORKSPACE_FILE

            resource_targets.append(
                ResourceTarget(
                    target_type=t_type,
                    raw_target=r_clean,
                )
            )

        # Build WindowStates from session applications
        window_states: List[WindowState] = []
        for app_proc in session.applications:
            # Default layout based on index
            idx = len(window_states)
            bounds = WindowBounds(
                x=100 + (idx * 60),
                y=100 + (idx * 60),
                width=1200,
                height=800,
            )
            window_states.append(
                WindowState(
                    process_name=app_proc,
                    bounds=bounds,
                    placement=WindowPlacementState.NORMAL,
                )
            )

        return WorkspaceSnapshot(
            session_id=session.session_id,
            intent_id=intent.intent_id,
            applications=list(session.applications),
            resources=resource_targets,
            window_states=window_states,
        )

    def build_plan_from_snapshot(
        self, snapshot: WorkspaceSnapshot, goal_description: str
    ) -> RestorationPlan:
        """
        Maps a WorkspaceSnapshot into a sequence of declarative RestorationSteps.
        """
        steps: List[RestorationStep] = []
        assigned_resources = set()

        # 1. First pass: Pair applications with their natural resource targets
        for app_name in snapshot.applications:
            app_desc = self.registry.get_by_process_name(app_name)
            app_id = app_desc.identifier if app_desc else app_name.lower().replace(".exe", "")

            matched_resource: Optional[ResourceTarget] = None
            if app_desc:
                for res in snapshot.resources:
                    if res.raw_target not in assigned_resources and res.target_type in app_desc.supported_targets:
                        matched_resource = res
                        assigned_resources.add(res.raw_target)
                        break

            # Find matching window state
            w_state = next((w for w in snapshot.window_states if w.process_name.lower() == app_name.lower()), None)

            step = RestorationStep(
                app_identifier=app_id,
                command_executable="",  # To be resolved during preflight validation
                command_args=[],
                resource=matched_resource,
                target_window_state=w_state,
                risk_level=RiskLevel.LOW,
                requires_approval=False,
                status=RestorationStepStatus.PENDING,
            )
            steps.append(step)

        # 2. Second pass: If remaining unassigned resources exist (e.g. web URLs without browser app)
        for res in snapshot.resources:
            if res.raw_target not in assigned_resources:
                if res.target_type == ResourceTargetType.WEB_URL:
                    fallback_browser = self.registry.get("edge") or self.registry.get("chrome")
                    if fallback_browser:
                        step = RestorationStep(
                            app_identifier=fallback_browser.identifier,
                            command_executable="",
                            command_args=[],
                            resource=res,
                            risk_level=RiskLevel.MEDIUM,
                            requires_approval=False,
                            status=RestorationStepStatus.PENDING,
                        )
                        steps.append(step)
                        assigned_resources.add(res.raw_target)

        return RestorationPlan(
            session_id=snapshot.session_id,
            goal_description=goal_description,
            steps=steps,
        )

    async def execute_restoration_plan(
        self,
        plan: RestorationPlan,
        approval_callback: Optional[Callable[[RestorationPlan], Any]] = None,
    ) -> RestorationResult:
        """
        Executes an approved, preflight-validated RestorationPlan with failure isolation
        and independent verification.
        """
        if not plan.is_preflight_valid:
            return RestorationResult(
                plan_id=plan.plan_id,
                session_id=plan.session_id,
                success=False,
                is_partial_restoration=False,
                summary=f"Restoration aborted: Preflight validation failed ({'; '.join(plan.preflight_errors)}).",
            )

        # Enforce Policy-Based Approval Gate
        if plan.requires_user_approval or plan.overall_risk == RiskLevel.HIGH:
            approved = False
            if approval_callback is not None:
                try:
                    res = approval_callback(plan)
                    if inspect.isawaitable(res):
                        approved = bool(await res)
                    else:
                        approved = bool(res)
                except Exception as e:
                    logger.warning("Approval callback raised exception: %s", e)
                    approved = False

            if not approved:
                return RestorationResult(
                    plan_id=plan.plan_id,
                    session_id=plan.session_id,
                    success=False,
                    is_partial_restoration=False,
                    summary="Restoration halted: Multi-application restoration requires explicit user approval.",
                )

        completed_steps: List[str] = []
        failed_steps: List[str] = []
        skipped_steps: List[str] = []
        step_results: Dict[str, RestorationStepResult] = {}
        verifications: Dict[str, RestorationVerification] = {}

        settings = get_settings()
        poll_timeout = settings.restoration_window_detection_timeout_seconds

        for step in plan.steps:
            if step.status == RestorationStepStatus.SKIPPED_INVALID:
                skipped_steps.append(step.step_id)
                continue

            step.status = RestorationStepStatus.RUNNING
            t_start = time.perf_counter()

            # 1. Isolated Subprocess Launch (shell=False)
            launch_ok, pid, err_msg = self.process_launcher(
                step.command_executable, step.command_args
            )

            if not launch_ok or pid is None:
                step.status = RestorationStepStatus.FAILED
                step.status_message = err_msg or "Failed to spawn process."
                failed_steps.append(step.step_id)
                step_results[step.step_id] = RestorationStepResult(
                    step_id=step.step_id,
                    app_identifier=step.app_identifier,
                    launched=False,
                    error=step.status_message,
                    execution_time_ms=(time.perf_counter() - t_start) * 1000.0,
                )
                verifications[step.step_id] = RestorationVerification(
                    step_id=step.step_id,
                    verified=False,
                    process_running=False,
                    window_visible=False,
                    details=step.status_message,
                    discrepancies=["Process failed to spawn."],
                )
                continue  # Failure isolation: continue to next independent step

            # 2. Window Layout Manipulation (PID-Bound & Best-Effort)
            target_hwnd: Optional[int] = None
            window_placed = False

            # Bounded polling for top-level window belonging to exact PID
            t_poll_start = time.perf_counter()
            while (time.perf_counter() - t_poll_start) < poll_timeout:
                hwnds = self.window_manager.find_windows_by_pid(pid)
                if hwnds:
                    target_hwnd = hwnds[0]
                    break
                await asyncio.sleep(0.1)

            if target_hwnd and step.target_window_state:
                # Apply window bounds and placement strictly to verified HWND
                if step.target_window_state.bounds:
                    self.window_manager.set_window_bounds(target_hwnd, step.target_window_state.bounds)
                if step.target_window_state.placement:
                    self.window_manager.set_window_placement(target_hwnd, step.target_window_state.placement)
                window_placed = True

            # 3. Independent Verification
            is_running = self.window_manager.is_process_running(pid)
            window_visible = bool(target_hwnd is not None)

            verified = is_running
            discrepancies: List[str] = []
            if not is_running:
                discrepancies.append(f"Process PID {pid} is not running.")
            if not window_visible:
                discrepancies.append(f"No visible top-level window detected for PID {pid} within {poll_timeout}s.")

            ver_details = (
                f"Process PID {pid} verified running."
                if verified
                else "Process liveness check failed."
            )

            step.status = RestorationStepStatus.SUCCESS
            completed_steps.append(step.step_id)

            step_results[step.step_id] = RestorationStepResult(
                step_id=step.step_id,
                app_identifier=step.app_identifier,
                launched=True,
                process_id=pid,
                target_hwnd=target_hwnd,
                window_placed=window_placed,
                execution_time_ms=(time.perf_counter() - t_start) * 1000.0,
            )
            verifications[step.step_id] = RestorationVerification(
                step_id=step.step_id,
                verified=verified,
                process_running=is_running,
                window_visible=window_visible,
                details=ver_details,
                discrepancies=discrepancies,
            )

        # 4. Construct Final Comprehensive Result
        is_partial = len(failed_steps) > 0 or len(skipped_steps) > 0
        all_success = len(completed_steps) > 0 and len(failed_steps) == 0

        summary = (
            f"Restoration completed successfully ({len(completed_steps)}/{len(plan.steps)} steps)."
            if all_success and not is_partial
            else f"Partial restoration completed: {len(completed_steps)} succeeded, "
                 f"{len(failed_steps)} failed, {len(skipped_steps)} skipped."
        )

        return RestorationResult(
            plan_id=plan.plan_id,
            session_id=plan.session_id,
            success=all_success,
            is_partial_restoration=is_partial,
            completed_steps=completed_steps,
            failed_steps=failed_steps,
            skipped_steps=skipped_steps,
            step_results=step_results,
            verification_reports=verifications,
            summary=summary,
        )
