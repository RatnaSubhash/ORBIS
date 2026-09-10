"""
ORBIS Phase 10 Demonstration Script: Multi-App & Window Layout Restoration.
Demonstrates:
1. End-to-end declarative workspace restoration from Phase 9 ICE evidence.
2. Trusted canonical executable resolution without ambient PATH lookups.
3. Preflight safety validations: traversal, device names, dangerous URIs, and SSRF.
4. Multi-monitor virtual desktop geometry with valid negative coordinates.
5. Strict PID-bound window discovery and layout manipulation.
6. Intrinsic risk vs. approval policy separation.
7. Step failure isolation and transparent partial restoration reporting.
8. Agent Orchestrator and RestorationTool integration.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from core.agent.llm.mock import MockLLMProvider
from core.agent.orchestrator import AgentOrchestrator
from core.ice.engine import IntentContinuityEngine
from core.memory.db import DatabaseManager
from core.restoration.engine import RestorationEngine
from core.restoration.models import (
    ResourceTarget,
    ResourceTargetType,
    RestorationPlan,
    RestorationStep,
    RestorationStepStatus,
    VirtualDesktopGeometry,
    WindowBounds,
    WindowPlacementState,
)
from core.restoration.registry import AppDescriptor, ApplicationRegistry
from core.restoration.validator import PreflightValidator
from core.restoration.window_manager import MockWindowManager
from core.tools.registry import ToolRegistry
from core.tools.restoration_tool import RestorationTool, RestorationToolParameters
from core.types import ExecutionContext, RiskLevel, Session


async def run_phase10_demo() -> None:
    print("=" * 80)
    print("ORBIS PHASE 10 DEMONSTRATION: MULTI-APP & WINDOW LAYOUT RESTORATION")
    print("Controlled Declarative Workspace Reconstruction Grounded in ICE Evidence")
    print("=" * 80)

    # Initialize in-memory DB and temp directory
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    with tempfile.TemporaryDirectory() as tmp_dir:
        workspace_dir = Path(tmp_dir) / "workspace"
        workspace_dir.mkdir()
        auth_file = workspace_dir / "auth_service.py"
        auth_file.write_text("# Auth service implementation\n", encoding="utf-8")
        test_file = workspace_dir / "test_auth.py"
        test_file.write_text("# Auth test suite\n", encoding="utf-8")

        bin_dir = Path(tmp_dir) / "bin"
        bin_dir.mkdir()
        code_exe = bin_dir / "Code.exe"
        code_exe.write_text("", encoding="utf-8")
        wt_exe = bin_dir / "wt.exe"
        wt_exe.write_text("", encoding="utf-8")
        chrome_exe = bin_dir / "chrome.exe"
        chrome_exe.write_text("", encoding="utf-8")

        # Custom registry with canonical binaries in temp directory
        apps = {
            "vscode": AppDescriptor(
                identifier="vscode",
                display_name="Visual Studio Code",
                process_names=["Code.exe"],
                canonical_paths=[str(code_exe)],
                supported_targets=[ResourceTargetType.WORKSPACE_FILE, ResourceTargetType.DIRECTORY],
            ),
            "terminal": AppDescriptor(
                identifier="terminal",
                display_name="Windows Terminal",
                process_names=["wt.exe"],
                canonical_paths=[str(wt_exe)],
                supported_targets=[ResourceTargetType.DIRECTORY],
            ),
            "chrome": AppDescriptor(
                identifier="chrome",
                display_name="Google Chrome",
                process_names=["chrome.exe"],
                canonical_paths=[str(chrome_exe)],
                supported_targets=[ResourceTargetType.WEB_URL],
            ),
        }
        registry = ApplicationRegistry(apps=apps)

        # Dual-monitor display simulation: Left monitor (-1920..0), Primary monitor (0..1920)
        virtual_geom = VirtualDesktopGeometry(left=-1920, top=0, width=3840, height=1080)
        wm = MockWindowManager(virtual_geometry=virtual_geom)

        now = datetime.now(timezone.utc)

        # Record a rich multi-app session in SQLite
        db.record_session(
            Session(
                session_id="sess_auth_debug",
                start_time=now - timedelta(minutes=25),
                end_time=now - timedelta(minutes=5),
                duration_seconds=1200.0,
                event_count=8,
                applications=["Code.exe", "wt.exe"],
                resources=[str(auth_file), str(workspace_dir), "https://docs.python.org/3/library/security.html"],
                event_ids=[f"ev_{i}" for i in range(8)],
                session_metadata={"task": "OAuth2 flow token debugging"},
            )
        )

        # =========================================================================
        # SCENARIO 1: End-to-End Declarative Restoration with ICE & Approval
        # =========================================================================
        print("\n" + "-" * 80)
        print("SCENARIO 1: End-to-End Declarative Restoration Grounded in ICE Context")
        print("Intent Query: 'Restore my previous authentication debugging session'")
        print("-" * 80)

        # Simulated process launcher tracking discrete PID and windows
        next_pid = 2001
        def demo_launcher(exe, args):
            nonlocal next_pid
            pid = next_pid
            next_pid += 1
            app_name = Path(exe).name
            wm.register_simulated_process(pid, app_name)
            wm.register_simulated_window(pid, f"Window for {app_name}")
            return True, pid, None

        engine = RestorationEngine(
            db=db,
            registry=registry,
            validator=PreflightValidator(registry=registry, allowed_workspace_root=str(workspace_dir)),
            window_manager=wm,
            process_launcher=demo_launcher,
        )

        # 1. Generate declarative plan (zero subprocess side effects)
        plan, ice_res, plan_msg = await engine.create_restoration_plan("Restore my previous authentication debugging session")
        print(f"[Plan Created] Plan ID: {plan.plan_id}")
        print(f"[Provenance] Session ID: {plan.session_id}")
        print(f"[ICE Goal Inferred] '{plan.goal_description}'")
        print(f"[Preflight Validation Status] Valid: {plan.is_preflight_valid}")
        print(f"[Intrinsic Risk Level] {plan.overall_risk.value}")
        print(f"[Approval Required by Policy] {plan.requires_user_approval}")
        print(f"[Planned Steps: {len(plan.steps)}]")
        for s in plan.steps:
            target_str = s.resource.resolved_target if s.resource else "None"
            print(f"  * App: {s.app_identifier.upper():<10} | Exe: {Path(s.command_executable).name} | Target: {target_str}")

        # 2. Execute plan with simulated approval callback
        print("\n[Executing Plan with User Approval]")
        approval_granted = True
        result = await engine.execute_restoration_plan(plan, approval_callback=lambda p: approval_granted)
        print(f"[Restoration Result] Success: {result.success}, Partial: {result.is_partial_restoration}")
        print(f"[Summary] {result.summary}")
        for step_id, step_res in result.step_results.items():
            print(f"  * Step {step_id}: Launched PID={step_res.process_id}, Window HWND={step_res.target_hwnd}, Placed={step_res.window_placed}")

        # =========================================================================
        # SCENARIO 2: Preflight Boundary Protections & Security Enforcements
        # =========================================================================
        print("\n" + "-" * 80)
        print("SCENARIO 2: Preflight Boundary Protections & Attack Vector Neutralization")
        print("-" * 80)

        validator = PreflightValidator(registry=registry, allowed_workspace_root=str(workspace_dir))
        attack_cases = [
            (ResourceTargetType.WORKSPACE_FILE, r"..\..\Windows\System32\cmd.exe", "Directory Traversal"),
            (ResourceTargetType.WORKSPACE_FILE, "CON", "Windows Reserved Device Name"),
            (ResourceTargetType.WORKSPACE_FILE, r"\\10.0.0.1\share\stolen.db", "UNC Network Path"),
            (ResourceTargetType.WORKSPACE_FILE, r"C:\Windows\System32\notepad.exe", "Outside Allowed Workspace Root"),
            (ResourceTargetType.WEB_URL, "file:///C:/Windows/System32/calc.exe", "Prohibited file: URI Scheme"),
            (ResourceTargetType.WEB_URL, "javascript:alert(document.cookie)", "Prohibited javascript: URI Scheme"),
            (ResourceTargetType.WEB_URL, "chrome://settings", "Prohibited chrome: Internal Scheme"),
            (ResourceTargetType.WEB_URL, "http://169.254.169.254/latest/meta-data/", "SSRF Cloud Metadata Block"),
        ]

        for t_type, raw_val, desc in attack_cases:
            res_target = ResourceTarget(target_type=t_type, raw_target=raw_val)
            is_valid, _, err = validator.validate_resource_target(res_target)
            status_str = "REJECTED (SAFE)" if not is_valid else "ALLOWED (VULNERABLE)"
            print(f"  * {desc:<35} -> {status_str} | Reason: {err}")

        # =========================================================================
        # SCENARIO 3: Multi-Monitor Virtual Desktop & Negative Coordinates
        # =========================================================================
        print("\n" + "-" * 80)
        print("SCENARIO 3: Multi-Monitor Geometry & Negative Coordinates Sanity")
        print(f"Virtual Desktop Span: {virtual_geom.left}px to {virtual_geom.right}px (Width: {virtual_geom.width}px)")
        print("-" * 80)

        # Coordinate on secondary monitor to the left (negative X)
        sec_bounds = WindowBounds(x=-1400, y=100, width=1100, height=850)
        clamped_sec = validator.validate_and_clamp_window_bounds(sec_bounds, virtual_geom)
        print(f"  * Secondary Monitor (Left Display): Target (x={sec_bounds.x}, y={sec_bounds.y}) -> Preserved: (x={clamped_sec.x}, y={clamped_sec.y})")

        # Wild off-screen coordinates clamped into visible view
        wild_bounds = WindowBounds(x=-9000, y=-9000, width=1200, height=800)
        clamped_wild = validator.validate_and_clamp_window_bounds(wild_bounds, virtual_geom)
        print(f"  * Off-Screen Out of Bounds:         Target (x={wild_bounds.x}, y={wild_bounds.y}) -> Safely Clamped: (x={clamped_wild.x}, y={clamped_wild.y})")

        # =========================================================================
        # SCENARIO 4: Failure Isolation Across Steps
        # =========================================================================
        print("\n" + "-" * 80)
        print("SCENARIO 4: Step Failure Isolation & Transparent Partial Reporting")
        print("-" * 80)

        failing_step = RestorationStep(
            step_id="step_crashed",
            app_identifier="vscode",
            command_executable=str(code_exe),
            status=RestorationStepStatus.PENDING,
        )
        ok_step = RestorationStep(
            step_id="step_healthy",
            app_identifier="terminal",
            command_executable=str(wt_exe),
            status=RestorationStepStatus.PENDING,
        )
        isolated_plan = RestorationPlan(
            session_id="sess_iso",
            goal_description="Failure isolation demo",
            steps=[failing_step, ok_step],
            is_preflight_valid=True,
            requires_user_approval=False,
        )

        def selective_launcher(exe, args):
            if "Code.exe" in exe:
                return False, None, "Process crashed immediately with exit code -1073741819"
            wm.register_simulated_process(3001, "wt.exe")
            wm.register_simulated_window(3001, "Terminal Window")
            return True, 3001, None

        iso_engine = RestorationEngine(
            db=db,
            registry=registry,
            window_manager=wm,
            process_launcher=selective_launcher,
        )

        iso_res = await iso_engine.execute_restoration_plan(isolated_plan)
        print(f"[Outcome] Success: {iso_res.success} | Partial Restoration: {iso_res.is_partial_restoration}")
        print(f"[Audit Summary] {iso_res.summary}")
        print(f"[Completed Steps] {iso_res.completed_steps}")
        print(f"[Failed Steps] {iso_res.failed_steps}")

        # =========================================================================
        # SCENARIO 5: Agent Orchestrator & RestorationTool Integration
        # =========================================================================
        print("\n" + "-" * 80)
        print("SCENARIO 5: Agent Orchestrator & ToolRegistry Integration")
        print("-" * 80)

        restoration_tool = RestorationTool(engine=engine)
        tool_registry = ToolRegistry()
        tool_registry.register(restoration_tool)

        orchestrator = AgentOrchestrator(
            llm_provider=MockLLMProvider(),
            tool_registry=tool_registry,
            db_manager=db,
            restoration_engine=engine,
        )

        orch_res = await orchestrator.run_restoration("Restore my authentication debugging session", execute=False)
        print(f"[Orchestrator Run Result] Plan Generated: {orch_res.get('plan') is not None}")
        print(f"[Orchestrator Plan ID] {orch_res['plan'].plan_id}")

        print("\n" + "=" * 80)
        print("PHASE 10 DEMONSTRATION COMPLETE: ALL SCENARIOS VERIFIED")
        print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_phase10_demo())
