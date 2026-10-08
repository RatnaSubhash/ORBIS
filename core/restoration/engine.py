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

import psutil

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
        if os.name == "nt":
            try:
                import ctypes
                # Allow newly launched window to claim foreground activation
                ctypes.windll.user32.AllowSetForegroundWindow(-1)
            except Exception:
                pass

        cmd = [executable] + args
        try:
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            # shell=False is strictly enforced
            proc = subprocess.Popen(
                cmd,
                shell=False,
                creationflags=creationflags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            return True, proc.pid, None
        except Exception as e:
            return False, None, str(e)

    async def create_restoration_plan(
        self,
        query: str,
        session_id: Optional[str] = None,
        target_resource: Optional[str] = None,
        reference_time: Optional[datetime] = None,
    ) -> Tuple[Optional[RestorationPlan], Optional[ICEResult], str]:
        """
        Uses Phase 9 ICE (or explicit session/target reference) to identify the relevant session,
        constructs a WorkspaceSnapshot, and generates a preflight-validated declarative RestorationPlan.
        """
        resolved_session_id = session_id
        ice_result: Optional[ICEResult] = None
        goal_description = "Restore previous workspace"

        if not resolved_session_id:
            # 1. Query Phase 9 ICE for intent and relevant session provenance
            ice_result = await self.ice_engine.infer_intent(query)
            if ice_result.has_sufficient_evidence and ice_result.primary_intent:
                primary = ice_result.primary_intent
                goal_description = primary.goal_description
                if primary.provenance and primary.provenance.session_ids:
                    resolved_session_id = primary.provenance.session_ids[0]

        # 2. Fallback to ContinuityRetriever if ICE has insufficient evidence
        if not resolved_session_id:
            from core.continuity.models import ContinuityQuery, ContinuityQueryType
            cont_q = ContinuityQuery(
                query_text=query,
                query_type=ContinuityQueryType.GENERAL_CONTINUITY,
                cutoff_time=reference_time,
                max_results=5,
            )
            evidence = self.ice_engine.retriever.retrieve(cont_q)
            if evidence:
                resolved_session_id = evidence[0].session_id
                goal_description = f"Restore workspace for session '{resolved_session_id}'"

        if not resolved_session_id:
            return None, ice_result, "Insufficient evidence to identify relevant workspace session."

        # 3. Extract session snapshot
        snapshot = self.build_snapshot_from_session(
            resolved_session_id,
            ice_result.primary_intent if ice_result else None,
            target_resource=target_resource,
        )
        if not snapshot:
            return None, ice_result, f"Failed to reconstruct workspace snapshot for session '{resolved_session_id}'."

        # 4. Build declarative RestorationPlan
        raw_plan = self.build_plan_from_snapshot(snapshot, goal_description, target_resource=target_resource)

        # 5. Preflight Validate Plan
        virtual_geom = self.window_manager.get_virtual_desktop_geometry()
        validated_plan = self.validator.validate_plan(raw_plan, virtual_geom)

        return validated_plan, ice_result, "Plan created and preflight-validated successfully."

    def build_snapshot_from_session(
        self,
        session_id: Union[str, Session],
        intent: Optional[IntentCandidate] = None,
        target_resource: Optional[str] = None,
    ) -> Optional[WorkspaceSnapshot]:
        """Constructs a declarative WorkspaceSnapshot from stored SQLite session evidence."""
        if isinstance(session_id, Session):
            session = session_id
            session_id = session.session_id
        else:
            session = self.db.get_session(session_id)
        if not session:
            return None

        from core.continuity.extractor import is_orbis_control_plane

        # Build ResourceTargets from session resources using evidence hierarchy
        resource_targets: List[ResourceTarget] = []
        seen_targets = set()

        for r in session.resources:
            r_clean = r.strip()
            if not r_clean or r_clean in seen_targets:
                continue

            if r_clean.startswith("http://") or r_clean.startswith("https://"):
                t_type = ResourceTargetType.WEB_URL
            else:
                # 1. Physical filesystem existence check
                p = Path(r_clean)
                check_p = p if p.is_absolute() else (self.validator.allowed_root / p).resolve()
                if check_p.exists():
                    if check_p.is_dir():
                        t_type = ResourceTargetType.DIRECTORY
                    elif check_p.is_file():
                        t_type = ResourceTargetType.WORKSPACE_FILE
                    else:
                        t_type = ResourceTargetType.UNKNOWN
                else:
                    # 2. Trustworthy stored resource metadata/type
                    meta_type = None
                    if session.session_metadata:
                        res_types = session.session_metadata.get("resource_types", {})
                        if r_clean in res_types:
                            meta_type = res_types[r_clean]
                        elif str(p) in res_types:
                            meta_type = res_types[str(p)]
                        else:
                            res_meta = session.session_metadata.get("resource_metadata", {})
                            if r_clean in res_meta and isinstance(res_meta[r_clean], dict):
                                meta_type = res_meta[r_clean].get("target_type") or res_meta[r_clean].get("type")

                    if meta_type:
                        m_str = str(meta_type).upper()
                        if "DIR" in m_str:
                            t_type = ResourceTargetType.DIRECTORY
                        elif "FILE" in m_str:
                            t_type = ResourceTargetType.WORKSPACE_FILE
                        elif "URL" in m_str:
                            t_type = ResourceTargetType.WEB_URL
                        else:
                            t_type = ResourceTargetType.UNKNOWN
                    else:
                        # 3. Unresolved / ambiguous extensionless nonexistent resource
                        # Invariant: Do NOT infer file/directory merely from extension or trailing slash
                        t_type = ResourceTargetType.UNKNOWN

            seen_targets.add(r_clean)
            resource_targets.append(
                ResourceTarget(
                    target_type=t_type,
                    raw_target=r_clean,
                )
            )

        # Check for verified project directory targets
        session_projects = []
        if session.session_metadata and isinstance(session.session_metadata.get("projects"), list):
            session_projects.extend(session.session_metadata["projects"])
        if target_resource and target_resource not in session_projects:
            session_projects.append(target_resource)

        for proj in session_projects:
            proj_clean = proj.strip()
            if not proj_clean:
                continue
            resolved_proj = None
            if self.validator.allowed_root.name.lower() == proj_clean.lower() and self.validator.allowed_root.is_dir():
                resolved_proj = str(self.validator.allowed_root)
            elif (self.validator.allowed_root / proj_clean).is_dir():
                resolved_proj = str((self.validator.allowed_root / proj_clean).resolve())
            elif Path(proj_clean).is_dir():
                resolved_proj = str(Path(proj_clean).resolve())

            if resolved_proj and resolved_proj not in seen_targets:
                seen_targets.add(resolved_proj)
                resource_targets.append(
                    ResourceTarget(
                        target_type=ResourceTargetType.DIRECTORY,
                        raw_target=resolved_proj,
                        resolved_target=resolved_proj,
                        is_valid=True,
                    )
                )

        # Exclude ORBIS control plane applications while preserving non-ORBIS Electron apps
        filtered_apps: List[str] = []
        window_states: List[WindowState] = []

        session_events = [e for e in self.db.get_workspace_events(limit=100) if e.event_id in session.event_ids] if session.event_ids else []

        OS_INTERNAL_PROCESSES = {
            "searchhost.exe",
            "searchhost",
            "searchapp.exe",
            "shellexperiencehost.exe",
            "startmenuexperiencehost.exe",
            "shellhost.exe",
            "shellhost",
            "taskhostw.exe",
            "lockapp.exe",
            "applicationframehost.exe",
            "dwm.exe",
            "sihost.exe",
            "fontdrvhost.exe",
            "ctfmon.exe",
            "smartscreen.exe",
        }

        for app_proc in session.applications:
            # Filter OS internal system host windows
            proc_clean = app_proc.lower()
            if proc_clean in OS_INTERNAL_PROCESSES or proc_clean.replace(".exe", "") in OS_INTERNAL_PROCESSES:
                continue

            # Check if this process was purely an ORBIS control plane window in this session
            is_control = False
            matching_events = [e for e in session_events if e.process_name and e.process_name.lower() == app_proc.lower()]
            if matching_events:
                if all(is_orbis_control_plane(e.process_name, e.window_title, e.metadata.get("executable_path") if isinstance(e.metadata, dict) else None) for e in matching_events):
                    is_control = True
            elif proc_clean in ("electron.exe", "electron", "orbis.exe", "orbis"):
                is_control = True

            if is_control:
                continue

            filtered_apps.append(app_proc)
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
            intent_id=intent.intent_id if intent else None,
            applications=filtered_apps,
            resources=resource_targets,
            window_states=window_states,
        )

    def build_plan_from_snapshot(
        self,
        snapshot: WorkspaceSnapshot,
        goal_description: str,
        target_resource: Optional[str] = None,
    ) -> RestorationPlan:
        """
        Maps a WorkspaceSnapshot into a sequence of declarative RestorationSteps.
        """
        steps: List[RestorationStep] = []
        assigned_resources = set()

        # 1. First pass: Pair applications with their natural resource targets
        for idx, app_name in enumerate(snapshot.applications):
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

            sess_prefix = snapshot.session_id[:8] if snapshot.session_id else "sess"
            step = RestorationStep(
                step_id=f"rstep_{sess_prefix}_{idx}_{app_id}",
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
        for res_idx, res in enumerate(snapshot.resources):
            if res.raw_target not in assigned_resources:
                if res.target_type == ResourceTargetType.WEB_URL:
                    fallback_browser = self.registry.get("edge") or self.registry.get("chrome")
                    if fallback_browser:
                        sess_prefix = snapshot.session_id[:8] if snapshot.session_id else "sess"
                        step = RestorationStep(
                            step_id=f"rstep_{sess_prefix}_res{res_idx}_{fallback_browser.identifier}",
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

        # If a specific target_resource was requested, filter steps to those addressing the target
        if target_resource:
            clean_tr = target_resource.lower().strip()
            filtered = []
            for s in steps:
                if s.resource:
                    raw = (s.resource.raw_target or "").lower()
                    resolved = (s.resource.resolved_target or "").lower()
                    if (
                        clean_tr == raw
                        or clean_tr == resolved
                        or clean_tr in raw
                        or clean_tr in resolved
                        or (raw and Path(raw).name.lower() == clean_tr)
                    ):
                        filtered.append(s)
            if filtered:
                steps = filtered

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

            if target_hwnd:
                # Apply window bounds and placement strictly to verified HWND
                if step.target_window_state and step.target_window_state.bounds:
                    self.window_manager.set_window_bounds(target_hwnd, step.target_window_state.bounds)
                if step.target_window_state and step.target_window_state.placement:
                    self.window_manager.set_window_placement(target_hwnd, step.target_window_state.placement)
                if os.name == "nt":
                    try:
                        import ctypes
                        ctypes.windll.user32.ShowWindow(target_hwnd, 1)
                        ctypes.windll.user32.SetForegroundWindow(target_hwnd)
                    except Exception:
                        pass
                window_placed = True

            # 3. Independent Verification
            is_running = self.window_manager.is_process_running(pid)
            app_desc = self.registry.get(step.app_identifier)
            if not is_running and app_desc:
                # Single-instance delegation fallback (e.g. Antigravity IDE, VS Code, Chrome)
                # When an instance is already running, the spawned launcher PID hands off and exits.
                for proc_name in app_desc.process_names:
                    for p in psutil.process_iter(['name']):
                        try:
                            if p.info['name'] and p.info['name'].lower() == proc_name.lower():
                                is_running = True
                                break
                        except Exception:
                            pass
                    if is_running:
                        break

            if target_hwnd is None and app_desc:
                for proc_name in app_desc.process_names:
                    for p in psutil.process_iter(['pid', 'name']):
                        try:
                            if p.info['name'] and p.info['name'].lower() == proc_name.lower():
                                cand_hwnds = self.window_manager.find_windows_by_pid(p.info['pid'])
                                if cand_hwnds:
                                    target_hwnd = cand_hwnds[0]
                                    break
                        except Exception:
                            pass
                    if target_hwnd:
                        if os.name == "nt":
                            try:
                                import ctypes
                                ctypes.windll.user32.ShowWindow(target_hwnd, 1)
                                ctypes.windll.user32.SetForegroundWindow(target_hwnd)
                            except Exception:
                                pass
                        break

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

            if verified:
                step.status = RestorationStepStatus.SUCCESS
                completed_steps.append(step.step_id)
            else:
                step.status = RestorationStepStatus.FAILED
                step.status_message = f"Process PID {pid} was spawned but could not be verified running."
                failed_steps.append(step.step_id)

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

    def get_restorable_sessions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Retrieves recent restorable sessions with application descriptors,
        resource target counts, and preflight readiness.
        """
        sessions = self.db.get_sessions(limit=limit * 2)
        # Order by newest first
        sessions.sort(key=lambda s: s.start_time, reverse=True)

        results: List[Dict[str, Any]] = []
        for s in sessions:
            snapshot = self.build_snapshot_from_session(s)
            if not snapshot:
                continue

            # Only include sessions that have at least one valid or filterable application or resource
            if not snapshot.applications and not snapshot.resources:
                continue

            app_summaries = []
            for app_name in snapshot.applications:
                app_desc = self.registry.get_by_process_name(app_name)
                app_summaries.append({
                    "process_name": app_name,
                    "identifier": app_desc.identifier if app_desc else app_name.lower().replace(".exe", ""),
                    "display_name": app_desc.display_name if app_desc else app_name,
                    "supported": bool(app_desc is not None),
                })

            res_summaries = []
            for res in snapshot.resources:
                res_summaries.append({
                    "target_type": res.target_type.value,
                    "raw_target": res.raw_target,
                    "resolved_target": res.resolved_target,
                    "is_valid": res.is_valid,
                })

            project_name = None
            if s.session_metadata and isinstance(s.session_metadata.get("projects"), list) and s.session_metadata["projects"]:
                project_name = s.session_metadata["projects"][0]

            results.append({
                "session_id": s.session_id,
                "start_time": s.start_time.isoformat(),
                "end_time": s.end_time.isoformat(),
                "duration_seconds": s.duration_seconds,
                "event_count": s.event_count,
                "project_name": project_name,
                "applications": app_summaries,
                "resources": res_summaries,
                "confidence": s.confidence,
                "step_count": len(snapshot.applications),
            })
            if len(results) >= limit:
                break

        return results
