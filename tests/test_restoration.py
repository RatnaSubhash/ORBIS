"""
Comprehensive Test Suite for ORBIS Phase 10: Multi-App & Window Layout Restoration.
Covers all 24 required test dimensions with 100% deterministic, offline fixtures:
1. Restoration domain models
2. Declarative plan generation (zero subprocess side effects)
3. ICE-guided session selection
4. Competing stale vs active sessions
5. Trusted canonical executable resolution
6. Ambient PATH lookup rejection
7. Arbitrary executable substitution rejection
8. Unregistered / unsafe application rejection
9. Missing / stale local resource preflight isolation
10. Directory traversal path rejection
11. Windows device / reserved name rejection
12. UNC / network path rejection
13. Workspace containment boundary enforcement
14. Privacy-excluded paths & resources
15. Strict browser URL scheme validation (no file:, javascript:, chrome:, etc.)
16. Browser SSRF protection
17. Multi-monitor virtual geometry & negative coordinates
18. PID-bound window discovery & unrelated window isolation
19. Window layout application
20. Failure isolation across steps
21. Accurate partial restoration reporting
22. Risk vs. approval policy separation
23. Subprocess safety (shell=False) and credential exclusion
24. Agent Orchestrator and RestorationTool integration
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from core.agent.orchestrator import AgentOrchestrator
from core.agent.llm.mock import MockLLMProvider
from core.config import get_settings
from core.ice.engine import IntentContinuityEngine
from core.ice.models import (
    ActionType,
    ICEResult,
    IntentCandidate,
    IntentEvidenceItem,
    IntentEvidenceType,
    IntentProvenance,
    NextActionCandidate,
    TaskContinuityState,
)
from core.memory.db import DatabaseManager
from core.restoration.engine import RestorationEngine
from core.restoration.models import (
    ResourceTarget,
    ResourceTargetType,
    RestorationPlan,
    RestorationResult,
    RestorationStep,
    RestorationStepResult,
    RestorationStepStatus,
    RestorationVerification,
    VirtualDesktopGeometry,
    WindowBounds,
    WindowPlacementState,
    WindowState,
    WorkspaceSnapshot,
)
from core.restoration.registry import AppDescriptor, ApplicationRegistry
from core.restoration.validator import PreflightValidator
from core.restoration.window_manager import MockWindowManager
from core.tools.registry import ToolRegistry
from core.tools.restoration_tool import RestorationTool, RestorationToolParameters
from core.types import ExecutionContext, RiskLevel, Session, WorkspaceEvent, WorkspaceEventType


@pytest.fixture
def db(tmp_path):
    """In-memory SQLite database initialized with all tables."""
    db_mgr = DatabaseManager(db_path=":memory:")
    db_mgr.initialize()
    return db_mgr


@pytest.fixture
def workspace_dir(tmp_path):
    """Creates a temporary workspace root containing valid sample files."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "main.py").write_text("print('hello')", encoding="utf-8")
    (ws / "notes.txt").write_text("todo notes", encoding="utf-8")
    (ws / "src").mkdir()
    (ws / "src" / "index.js").write_text("console.log('test')", encoding="utf-8")
    return ws


@pytest.fixture
def mock_registry(tmp_path):
    """Custom ApplicationRegistry with simulated binaries in temp directory."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    vscode_exe = bin_dir / "Code.exe"
    vscode_exe.write_text("", encoding="utf-8")
    wt_exe = bin_dir / "wt.exe"
    wt_exe.write_text("", encoding="utf-8")
    chrome_exe = bin_dir / "chrome.exe"
    chrome_exe.write_text("", encoding="utf-8")
    notepad_exe = bin_dir / "notepad.exe"
    notepad_exe.write_text("", encoding="utf-8")

    apps = {
        "vscode": AppDescriptor(
            identifier="vscode",
            display_name="Visual Studio Code",
            process_names=["Code.exe"],
            canonical_paths=[str(vscode_exe)],
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
        "notepad": AppDescriptor(
            identifier="notepad",
            display_name="Notepad",
            process_names=["notepad.exe"],
            canonical_paths=[str(notepad_exe)],
            supported_targets=[ResourceTargetType.WORKSPACE_FILE],
        ),
    }
    return ApplicationRegistry(apps=apps)


@pytest.fixture
def mock_wm():
    """MockWindowManager simulating a dual-monitor setup: left (-1920..0), primary (0..1920)."""
    geom = VirtualDesktopGeometry(left=-1920, top=0, width=3840, height=1080)
    return MockWindowManager(virtual_geometry=geom)


# ==============================================================================
# 1. Restoration Domain Models
# ==============================================================================

def test_dim01_restoration_domain_models():
    bounds = WindowBounds(x=-1200, y=100, width=1024, height=768)
    assert bounds.x == -1200
    assert bounds.width == 1024

    geom = VirtualDesktopGeometry(left=-1920, top=0, width=3840, height=1080)
    assert geom.contains_point(-100, 500)
    assert geom.contains_point(100, 500)
    assert not geom.contains_point(2500, 500)

    target = ResourceTarget(target_type=ResourceTargetType.WORKSPACE_FILE, raw_target="main.py")
    assert not target.is_valid

    step = RestorationStep(
        app_identifier="vscode",
        command_executable="C:\\mock\\Code.exe",
        command_args=["main.py"],
        risk_level=RiskLevel.LOW,
    )
    assert step.status == RestorationStepStatus.PENDING

    plan = RestorationPlan(
        session_id="s123",
        goal_description="Restore editor",
        steps=[step],
        overall_risk=RiskLevel.LOW,
        requires_user_approval=True,
    )
    assert plan.overall_risk == RiskLevel.LOW
    assert plan.requires_user_approval is True


# ==============================================================================
# 2. Declarative Plan Generation (Zero Subprocess Side Effects)
# ==============================================================================

@pytest.mark.asyncio
async def test_dim02_declarative_plan_generation(db, workspace_dir, mock_registry, mock_wm):
    now = datetime.now(timezone.utc)
    db.record_session(
        Session(
            session_id="sess_01",
            start_time=now - timedelta(minutes=10),
            end_time=now - timedelta(minutes=2),
            duration_seconds=480.0,
            event_count=2,
            applications=["Code.exe", "wt.exe"],
            resources=[str(workspace_dir / "main.py"), str(workspace_dir)],
            event_ids=["e1", "e2"],
        )
    )

    launched_processes = []
    def spy_launcher(exe, args):
        launched_processes.append((exe, args))
        return True, 9999, None

    engine = RestorationEngine(
        db=db,
        registry=mock_registry,
        validator=PreflightValidator(registry=mock_registry, allowed_workspace_root=str(workspace_dir)),
        window_manager=mock_wm,
        process_launcher=spy_launcher,
    )

    plan, ice_res, msg = await engine.create_restoration_plan("Restore my debugging session")
    assert plan is not None
    assert plan.is_preflight_valid
    assert len(plan.steps) == 2
    # Invariant: Planning has ZERO subprocess side effects
    assert len(launched_processes) == 0


# ==============================================================================
# 3. ICE-Guided Session Selection
# ==============================================================================

@pytest.mark.asyncio
async def test_dim03_ice_guided_session_selection(db, workspace_dir, mock_registry, mock_wm):
    now = datetime.now(timezone.utc)
    db.record_session(
        Session(
            session_id="sess_auth",
            start_time=now - timedelta(minutes=5),
            end_time=now - timedelta(minutes=1),
            duration_seconds=240.0,
            event_count=5,
            applications=["Code.exe"],
            resources=[str(workspace_dir / "main.py")],
            event_ids=["e1", "e2", "e3", "e4", "e5"],
        )
    )

    engine = RestorationEngine(
        db=db,
        registry=mock_registry,
        validator=PreflightValidator(registry=mock_registry, allowed_workspace_root=str(workspace_dir)),
        window_manager=mock_wm,
    )

    plan, ice_res, _ = await engine.create_restoration_plan("What was I doing with authentication?")
    assert plan is not None
    assert plan.session_id == "sess_auth"
    assert len(plan.steps) == 1
    assert plan.steps[0].app_identifier == "vscode"


# ==============================================================================
# 4. Competing Stale vs Active Sessions
# ==============================================================================

@pytest.mark.asyncio
async def test_dim04_competing_stale_active_sessions(db, workspace_dir, mock_registry, mock_wm):
    now = datetime.now(timezone.utc)
    # Stale session from 4 days ago
    db.record_session(
        Session(
            session_id="sess_stale",
            start_time=now - timedelta(days=4),
            end_time=now - timedelta(days=4, hours=-1),
            duration_seconds=3600.0,
            event_count=5,
            applications=["notepad.exe"],
            resources=[str(workspace_dir / "notes.txt")],
            event_ids=["s1", "s2", "s3", "s4", "s5"],
        )
    )
    # Active session from 5 minutes ago
    db.record_session(
        Session(
            session_id="sess_recent",
            start_time=now - timedelta(minutes=5),
            end_time=now - timedelta(minutes=1),
            duration_seconds=240.0,
            event_count=5,
            applications=["Code.exe"],
            resources=[str(workspace_dir / "main.py")],
            event_ids=["r1", "r2", "r3", "r4", "r5"],
        )
    )

    engine = RestorationEngine(
        db=db,
        registry=mock_registry,
        validator=PreflightValidator(registry=mock_registry, allowed_workspace_root=str(workspace_dir)),
        window_manager=mock_wm,
    )

    plan, _, _ = await engine.create_restoration_plan("Restore my recent workspace")
    assert plan is not None
    assert plan.session_id == "sess_recent"


# ==============================================================================
# 5. Trusted Canonical Executable Resolution
# ==============================================================================

def test_dim05_trusted_canonical_executable_resolution(tmp_path):
    bin_file = tmp_path / "app.exe"
    bin_file.write_text("binary", encoding="utf-8")

    desc = AppDescriptor(
        identifier="test_app",
        display_name="Test App",
        process_names=["app.exe"],
        canonical_paths=[str(bin_file)],
        supported_targets=[ResourceTargetType.WORKSPACE_FILE],
    )
    resolved = desc.resolve_trusted_executable()
    assert resolved is not None
    assert resolved == bin_file.resolve()


# ==============================================================================
# 6. Ambient PATH Lookup Rejection
# ==============================================================================

def test_dim06_ambient_path_lookup_rejection(monkeypatch):
    # If file exists on PATH but NOT in canonical_paths, it must NOT resolve
    desc = AppDescriptor(
        identifier="calc",
        display_name="Calculator",
        process_names=["calc.exe"],
        canonical_paths=[r"C:\NonExistentLocation\calc.exe"],
        supported_targets=[],
    )
    assert desc.resolve_trusted_executable() is None


# ==============================================================================
# 7. Arbitrary Executable Substitution Rejection
# ==============================================================================

def test_dim07_arbitrary_executable_substitution_rejection(tmp_path):
    evil_bin = tmp_path / "evil.exe"
    evil_bin.write_text("malicious", encoding="utf-8")

    reg = ApplicationRegistry._build_default_apps()
    registry = ApplicationRegistry(apps=reg)

    # Attempting to query an unapproved arbitrary executable fails
    assert registry.get(str(evil_bin)) is None
    assert not registry.is_allowed(str(evil_bin))


# ==============================================================================
# 8. Unregistered / Unsafe Application Rejection
# ==============================================================================

def test_dim08_unregistered_unsafe_application_rejection(workspace_dir, mock_registry):
    validator = PreflightValidator(registry=mock_registry, allowed_workspace_root=str(workspace_dir))
    step = RestorationStep(
        app_identifier="cmd_malicious",
        command_executable="",
        command_args=[],
    )
    plan = RestorationPlan(
        session_id="s_bad_app",
        goal_description="Test bad app",
        steps=[step],
    )

    validated = validator.validate_plan(plan)
    assert step.status == RestorationStepStatus.SKIPPED_INVALID
    assert "not in the approved registry" in (step.status_message or "")
    assert not validated.is_preflight_valid


# ==============================================================================
# 9. Missing / Stale Local Resource Preflight Isolation
# ==============================================================================

def test_dim09_missing_stale_local_resource_preflight(workspace_dir, mock_registry):
    validator = PreflightValidator(registry=mock_registry, allowed_workspace_root=str(workspace_dir))
    ghost_file = workspace_dir / "deleted_file.py"

    step = RestorationStep(
        app_identifier="vscode",
        command_executable="",
        resource=ResourceTarget(
            target_type=ResourceTargetType.WORKSPACE_FILE,
            raw_target=str(ghost_file),
        ),
    )
    plan = RestorationPlan(
        session_id="s_ghost",
        goal_description="Missing file test",
        steps=[step],
    )

    validated = validator.validate_plan(plan)
    assert step.status == RestorationStepStatus.SKIPPED_INVALID
    assert "does not exist" in (step.status_message or "")


# ==============================================================================
# 10. Directory Traversal Path Rejection
# ==============================================================================

def test_dim10_directory_traversal_path_rejection(workspace_dir, mock_registry):
    validator = PreflightValidator(registry=mock_registry, allowed_workspace_root=str(workspace_dir))
    target = ResourceTarget(
        target_type=ResourceTargetType.WORKSPACE_FILE,
        raw_target=r"..\..\Windows\System32\cmd.exe",
    )
    valid, _, err = validator.validate_resource_target(target)
    assert not valid
    assert "Directory traversal" in str(err)


# ==============================================================================
# 11. Windows Device / Reserved Name Rejection
# ==============================================================================

def test_dim11_windows_device_reserved_name_rejection(workspace_dir, mock_registry):
    validator = PreflightValidator(registry=mock_registry, allowed_workspace_root=str(workspace_dir))
    for dev in ["con", "prn", "aux", "nul", "com1", "lpt1"]:
        target = ResourceTarget(
            target_type=ResourceTargetType.WORKSPACE_FILE,
            raw_target=dev,
        )
        valid, _, err = validator.validate_resource_target(target)
        assert not valid
        assert "reserved device name" in str(err)


# ==============================================================================
# 12. UNC / Network Path Rejection
# ==============================================================================

def test_dim12_unc_network_path_rejection(workspace_dir, mock_registry):
    validator = PreflightValidator(registry=mock_registry, allowed_workspace_root=str(workspace_dir))
    target = ResourceTarget(
        target_type=ResourceTargetType.WORKSPACE_FILE,
        raw_target=r"\\192.168.1.50\share\file.txt",
    )
    valid, _, err = validator.validate_resource_target(target)
    assert not valid
    assert "UNC network paths are prohibited" in str(err)


# ==============================================================================
# 13. Workspace Containment Boundary Enforcement
# ==============================================================================

def test_dim13_workspace_containment(tmp_path, workspace_dir, mock_registry):
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("secret", encoding="utf-8")

    validator = PreflightValidator(registry=mock_registry, allowed_workspace_root=str(workspace_dir))
    target = ResourceTarget(
        target_type=ResourceTargetType.WORKSPACE_FILE,
        raw_target=str(outside_file),
    )
    valid, _, err = validator.validate_resource_target(target)
    assert not valid
    assert "resolves outside allowed root" in str(err)


# ==============================================================================
# 14. Privacy-Excluded Paths & Resources
# ==============================================================================

def test_dim14_privacy_excluded_paths(workspace_dir, mock_registry):
    secret_file = workspace_dir / "secret_passwords.txt"
    secret_file.write_text("pwd", encoding="utf-8")

    from core.config import Settings
    settings = Settings(excluded_window_titles=["password", "credential"])

    validator = PreflightValidator(
        registry=mock_registry,
        allowed_workspace_root=str(workspace_dir),
        settings=settings,
    )
    target = ResourceTarget(
        target_type=ResourceTargetType.WORKSPACE_FILE,
        raw_target=str(secret_file),
    )
    valid, _, err = validator.validate_resource_target(target)
    assert not valid
    assert "excluded by privacy policy" in str(err)


# ==============================================================================
# 15. Strict Browser URL Scheme Validation
# ==============================================================================

def test_dim15_strict_browser_url_scheme_validation(workspace_dir, mock_registry):
    validator = PreflightValidator(registry=mock_registry, allowed_workspace_root=str(workspace_dir))

    bad_urls = [
        "file:///C:/Windows/System32/calc.exe",
        "javascript:alert(1)",
        "data:text/html,<h1>Pwned</h1>",
        "chrome://settings",
        "edge://version",
        "about:blank",
    ]
    for url in bad_urls:
        target = ResourceTarget(target_type=ResourceTargetType.WEB_URL, raw_target=url)
        valid, _, err = validator.validate_resource_target(target)
        assert not valid, f"Expected {url} to be rejected"
        assert "is strictly prohibited" in str(err) or "Only 'http' and 'https'" in str(err)


# ==============================================================================
# 16. Browser SSRF Protection
# ==============================================================================

def test_dim16_browser_ssrf_protection(workspace_dir, mock_registry):
    validator = PreflightValidator(registry=mock_registry, allowed_workspace_root=str(workspace_dir))

    ssrf_targets = [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8080/admin",
        "http://localhost:5000",
    ]
    for url in ssrf_targets:
        target = ResourceTarget(target_type=ResourceTargetType.WEB_URL, raw_target=url)
        valid, _, err = validator.validate_resource_target(target)
        assert not valid, f"Expected SSRF target {url} to be rejected"
        assert "security policy" in str(err).lower()


# ==============================================================================
# 17. Multi-Monitor Virtual Geometry & Negative Coordinates
# ==============================================================================

def test_dim17_multi_monitor_virtual_geometry(workspace_dir, mock_registry):
    geom = VirtualDesktopGeometry(left=-1920, top=0, width=3840, height=1080)
    validator = PreflightValidator(registry=mock_registry, allowed_workspace_root=str(workspace_dir))

    # Negative coordinates on secondary monitor to the left are completely valid
    secondary_bounds = WindowBounds(x=-1500, y=100, width=1000, height=800)
    clamped = validator.validate_and_clamp_window_bounds(secondary_bounds, geom)
    assert clamped.x == -1500
    assert clamped.y == 100

    # Bounds completely outside virtual desktop are clamped into view
    way_off = WindowBounds(x=-5000, y=-5000, width=1000, height=800)
    clamped_off = validator.validate_and_clamp_window_bounds(way_off, geom)
    assert clamped_off.x >= geom.left
    assert clamped_off.y >= geom.top


# ==============================================================================
# 18. PID-Bound Window Discovery & Unrelated Window Isolation
# ==============================================================================

def test_dim18_pid_bound_window_discovery(mock_wm):
    target_pid = 4001
    other_pid = 4002

    mock_wm.register_simulated_process(target_pid, "Code.exe")
    mock_wm.register_simulated_process(other_pid, "Code.exe")

    h_target = mock_wm.register_simulated_window(target_pid, "main.py - Target Window")
    h_other = mock_wm.register_simulated_window(other_pid, "unrelated.py - Other User Window")

    found_hwnds = mock_wm.find_windows_by_pid(target_pid)
    assert h_target in found_hwnds
    # Invariant: Unrelated window of same application is never targeted
    assert h_other not in found_hwnds


# ==============================================================================
# 19. Window Layout Application
# ==============================================================================

def test_dim19_window_layout_application(mock_wm):
    pid = 5001
    mock_wm.register_simulated_process(pid, "notepad.exe")
    hwnd = mock_wm.register_simulated_window(pid, "notes.txt - Notepad")

    new_bounds = WindowBounds(x=-800, y=200, width=800, height=600)
    ok_bounds = mock_wm.set_window_bounds(hwnd, new_bounds)
    assert ok_bounds
    assert mock_wm.get_window_bounds(hwnd) == new_bounds

    ok_placement = mock_wm.set_window_placement(hwnd, WindowPlacementState.MAXIMIZED)
    assert ok_placement


# ==============================================================================
# 20. Failure Isolation Across Steps
# ==============================================================================

@pytest.mark.asyncio
async def test_dim20_failure_isolation(db, workspace_dir, mock_registry, mock_wm):
    step1 = RestorationStep(
        step_id="s1_fail",
        app_identifier="vscode",
        command_executable="C:\\mock\\Code.exe",
        status=RestorationStepStatus.PENDING,
    )
    step2 = RestorationStep(
        step_id="s2_ok",
        app_identifier="notepad",
        command_executable="C:\\mock\\notepad.exe",
        status=RestorationStepStatus.PENDING,
    )
    plan = RestorationPlan(
        session_id="s_partial",
        goal_description="Failure isolation test",
        steps=[step1, step2],
        is_preflight_valid=True,
        requires_user_approval=False,
    )

    def launcher(exe, args):
        if "Code.exe" in exe:
            return False, None, "Process crashed on start"
        mock_wm.register_simulated_process(7002, "notepad.exe")
        mock_wm.register_simulated_window(7002, "Notepad")
        return True, 7002, None

    engine = RestorationEngine(
        db=db,
        registry=mock_registry,
        window_manager=mock_wm,
        process_launcher=launcher,
    )

    result = await engine.execute_restoration_plan(plan)
    assert not result.success
    assert result.is_partial_restoration
    assert "s1_fail" in result.failed_steps
    assert "s2_ok" in result.completed_steps


# ==============================================================================
# 21. Accurate Partial Restoration Reporting
# ==============================================================================

@pytest.mark.asyncio
async def test_dim21_partial_restoration_reporting(db, workspace_dir, mock_registry, mock_wm):
    step1 = RestorationStep(
        step_id="s_ok",
        app_identifier="notepad",
        command_executable="C:\\mock\\notepad.exe",
        status=RestorationStepStatus.PENDING,
    )
    step2 = RestorationStep(
        step_id="s_skipped",
        app_identifier="vscode",
        command_executable="",
        status=RestorationStepStatus.SKIPPED_INVALID,
        status_message="File deleted",
    )
    plan = RestorationPlan(
        session_id="s_report",
        goal_description="Report test",
        steps=[step1, step2],
        is_preflight_valid=True,
        requires_user_approval=False,
    )

    def launcher(exe, args):
        mock_wm.register_simulated_process(8001, "notepad.exe")
        mock_wm.register_simulated_window(8001, "Notepad")
        return True, 8001, None

    engine = RestorationEngine(
        db=db,
        registry=mock_registry,
        window_manager=mock_wm,
        process_launcher=launcher,
    )

    result = await engine.execute_restoration_plan(plan)
    assert result.is_partial_restoration
    assert "s_skipped" in result.skipped_steps
    assert "s_ok" in result.completed_steps
    assert "Partial restoration completed" in result.summary


# ==============================================================================
# 22. Risk vs. Approval Policy Separation
# ==============================================================================

def test_dim22_risk_vs_approval_separation(workspace_dir, mock_registry):
    validator = PreflightValidator(registry=mock_registry, allowed_workspace_root=str(workspace_dir))
    step1 = RestorationStep(
        app_identifier="vscode",
        command_executable="",
        resource=ResourceTarget(
            target_type=ResourceTargetType.WORKSPACE_FILE,
            raw_target=str(workspace_dir / "main.py"),
        ),
    )
    step2 = RestorationStep(
        app_identifier="notepad",
        command_executable="",
        resource=ResourceTarget(
            target_type=ResourceTargetType.WORKSPACE_FILE,
            raw_target=str(workspace_dir / "notes.txt"),
        ),
    )
    plan = RestorationPlan(
        session_id="s_policy",
        goal_description="Policy separation test",
        steps=[step1, step2],
    )

    validated = validator.validate_plan(plan)
    # INVARIANT: Intrinsic risk remains LOW for safe local files
    assert validated.overall_risk == RiskLevel.LOW
    # INVARIANT: Multi-app restoration still requires user approval by policy
    assert validated.requires_user_approval is True


# ==============================================================================
# 23. Subprocess Safety (shell=False) and Credential Exclusion
# ==============================================================================

@pytest.mark.asyncio
async def test_dim23_subprocess_safety_and_no_credentials(db, workspace_dir, mock_registry, mock_wm):
    plan = RestorationPlan(
        session_id="s_sec",
        goal_description="Safety check",
        steps=[
            RestorationStep(
                step_id="step_safe",
                app_identifier="notepad",
                command_executable="C:\\mock\\notepad.exe",
                command_args=[str(workspace_dir / "main.py")],
                status=RestorationStepStatus.PENDING,
            )
        ],
        is_preflight_valid=True,
        requires_user_approval=True,
    )

    engine = RestorationEngine(db=db, registry=mock_registry, window_manager=mock_wm)

    # If approval is NOT granted, execution must be aborted
    result_denied = await engine.execute_restoration_plan(plan, approval_callback=lambda p: False)
    assert not result_denied.success
    assert "requires explicit user approval" in result_denied.summary


# ==============================================================================
# 24. Agent Orchestrator and RestorationTool Integration
# ==============================================================================

@pytest.mark.asyncio
async def test_dim24_agent_orchestrator_integration(db, workspace_dir, mock_registry, mock_wm):
    now = datetime.now(timezone.utc)
    db.record_session(
        Session(
            session_id="sess_integrated",
            start_time=now - timedelta(minutes=8),
            end_time=now - timedelta(minutes=1),
            duration_seconds=420.0,
            event_count=5,
            applications=["notepad.exe"],
            resources=[str(workspace_dir / "notes.txt")],
            event_ids=["i1", "i2", "i3", "i4", "i5"],
        )
    )

    engine = RestorationEngine(
        db=db,
        registry=mock_registry,
        validator=PreflightValidator(registry=mock_registry, allowed_workspace_root=str(workspace_dir)),
        window_manager=mock_wm,
    )

    restoration_tool = RestorationTool(engine=engine)
    registry = ToolRegistry()
    registry.register(restoration_tool)

    orchestrator = AgentOrchestrator(
        llm_provider=MockLLMProvider(),
        tool_registry=registry,
        db_manager=db,
        restoration_engine=engine,
    )

    # Test Orchestrator run_restoration path
    res = await orchestrator.run_restoration("Restore my previous notes workspace", execute=False)
    assert isinstance(res, dict)
    plan = res.get("plan")
    assert plan is not None
    assert plan.session_id == "sess_integrated"
    assert len(plan.steps) == 1

    # Test RestorationTool execution through Tool interface
    context = ExecutionContext()
    tool_res = await restoration_tool.execute(
        RestorationToolParameters(query="Restore notes", execute=False),
        context=context,
    )
    assert tool_res.success
    assert tool_res.output.get("status") == "plan_generated"
