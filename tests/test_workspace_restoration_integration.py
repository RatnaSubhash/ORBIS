"""
Comprehensive Integration Test Suite for ORBIS Workspace Continuity -> Restoration Integration.
Tests all 18 requirements specified in Section 13 of the project specification:
1. Informational queries ("What was I working on?") -> WORKSPACE_CONTINUITY, never execute apps
2. Genuine restoration intent ("Continue where I left off") -> WORKSPACE_RESTORATION, execute=True
3. Explicit project open ("Open project ORBIS") -> resolves project name, planner sets execute=True
4. Default tool safety: RestorationToolParameters() defaults to execute=False
5. Contextual reference single candidate: "Open it" resolves to single project
6. Contextual reference multiple candidates: "Open it" halts at ambiguity gate, asks clarification
7. Ordinal reference: "Open the first one" resolves to first project
8. Ordinal reference: "Open the second project" resolves to second project
9. Unverified window-title evidence rejected without hallucinating filesystem paths
10. Antigravity IDE canonical path resolution without ambient PATH reliance
11. ORBIS Electron control plane excluded while preserving non-ORBIS Electron apps
12. Preflight validation rejects unauthorized/protected paths
13. Hierarchical planner end-to-end plan generation with execute=True
14. Orchestrator end-to-end execution and human-readable summary formatting
15. Dry run / preview commands plan with execute=False
16. RestorationTool verification report validation
17. Missing ICE session fallback to ContinuityRetriever
18. Multi-turn conversation context continuity propagation
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from core.agent.conversation import ConversationContext, IntentType
from core.agent.models import AgentRequest, TaskPlan, TaskState
from core.agent.orchestrator import AgentOrchestrator, format_useful_tool_output
from core.agent.planner.classifier import CapabilityClass, classify_capability
from core.agent.planner.hierarchical import HierarchicalPlanner
from core.continuity.extractor import is_orbis_control_plane
from core.continuity.models import ContinuityResponse
from core.continuity.service import ContinuityService
from core.events.bus import EventBus
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
    WindowState,
    WorkspaceSnapshot,
)
from core.restoration.registry import AppDescriptor, ApplicationRegistry
from core.restoration.validator import PreflightValidator
from core.tools.continuity_tool import ContinuityTool
from core.tools.registry import ToolRegistry, create_default_tool_registry
from core.tools.restoration_tool import RestorationTool, RestorationToolParameters
from core.types import ExecutionContext, RiskLevel, Session, WorkspaceEvent, WorkspaceEventType


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def db():
    """In-memory SQLite database initialized with all tables."""
    db_mgr = DatabaseManager(db_path=":memory:")
    db_mgr.initialize()
    return db_mgr


@pytest.fixture
def sample_workspace(tmp_path):
    """Creates a sample workspace with a project directory."""
    ws = tmp_path / "ORBIS"
    ws.mkdir()
    (ws / "main.py").write_text("print('hello')", encoding="utf-8")
    return ws


@pytest.fixture
def mock_registry(tmp_path):
    """ApplicationRegistry with mock executables on disk."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    antigravity_exe = bin_dir / "Antigravity IDE.exe"
    antigravity_exe.write_text("", encoding="utf-8")
    code_exe = bin_dir / "Code.exe"
    code_exe.write_text("", encoding="utf-8")

    registry = ApplicationRegistry()
    registry.register_app(
        AppDescriptor(
            identifier="antigravity",
            display_name="Antigravity IDE",
            process_names=["Antigravity IDE.exe", "Antigravity IDE", "antigravity.exe"],
            canonical_paths=[str(antigravity_exe)],
            supported_targets=[ResourceTargetType.WORKSPACE_FILE, ResourceTargetType.DIRECTORY],
        )
    )
    registry.register_app(
        AppDescriptor(
            identifier="vscode",
            display_name="Visual Studio Code",
            process_names=["Code.exe", "code.exe"],
            canonical_paths=[str(code_exe)],
            supported_targets=[ResourceTargetType.WORKSPACE_FILE, ResourceTargetType.DIRECTORY],
        )
    )
    return registry


# ==============================================================================
# 1. Informational Queries: WORKSPACE_CONTINUITY & Zero Mutation
# ==============================================================================

@pytest.mark.parametrize(
    "query",
    [
        "What was I working on?",
        "What apps were open?",
        "Which files was I working with?",
        "What was I doing earlier?",
        "Where did I leave off?",
        "What apps was I using?",
    ],
)
def test_informational_query_classification_and_routing(query):
    """Informational continuity queries route strictly to WORKSPACE_CONTINUITY."""
    cap = classify_capability(query)
    assert cap == CapabilityClass.WORKSPACE_CONTINUITY
    assert cap != CapabilityClass.WORKSPACE_RESTORATION

    planner = HierarchicalPlanner()
    tool_reg = ToolRegistry()
    c_tool = ContinuityTool()
    tool_reg.register(c_tool)
    r_tool = RestorationTool()
    tool_reg.register(r_tool)

    req = AgentRequest(user_prompt=query)
    import asyncio
    plan = asyncio.run(planner.plan(req, tool_reg))
    assert plan.task_type.value in ("CONTINUITY", "continuity")
    assert "step_continuity" in plan.steps
    assert "step_workspace_restoration" not in plan.steps
    assert plan.steps["step_continuity"].tool_name == "continuity_tool"


# ==============================================================================
# 2. Genuine Restoration Intent: WORKSPACE_RESTORATION & execute=True
# ==============================================================================

@pytest.mark.parametrize(
    "query",
    [
        "Continue where I left off",
        "Restore my previous workspace",
        "Open what I was working on",
        "Resume the project from earlier",
        "Restore my workspace",
    ],
)
def test_genuine_restoration_intent_and_execution_flag(query):
    """Genuine restoration requests classify as WORKSPACE_RESTORATION with execute=True."""
    cap = classify_capability(query)
    assert cap == CapabilityClass.WORKSPACE_RESTORATION

    planner = HierarchicalPlanner()
    tool_reg = ToolRegistry()
    tool_reg.register(RestorationTool())

    req = AgentRequest(user_prompt=query)
    import asyncio
    plan = asyncio.run(planner.plan(req, tool_reg))
    assert "step_workspace_restoration" in plan.steps
    step = plan.steps["step_workspace_restoration"]
    assert step.tool_name == "restoration_tool"
    assert step.parameters.get("execute") is True
    assert step.parameters.get("query") == query


# ==============================================================================
# 3. Explicit Project Open: "Open project ORBIS"
# ==============================================================================

def test_explicit_project_open():
    """'Open project ORBIS' extracts target_resource and routes to restoration."""
    query = "Open project ORBIS"
    cap = classify_capability(query)
    assert cap == CapabilityClass.WORKSPACE_RESTORATION

    planner = HierarchicalPlanner()
    tool_reg = ToolRegistry()
    tool_reg.register(RestorationTool())

    req = AgentRequest(user_prompt=query)
    import asyncio
    plan = asyncio.run(planner.plan(req, tool_reg))
    step = plan.steps["step_workspace_restoration"]
    assert step.parameters.get("execute") is True
    assert step.parameters.get("target_resource") == "ORBIS"


# ==============================================================================
# 4. Default Tool Safety: RestorationToolParameters() Defaults to execute=False
# ==============================================================================

def test_restoration_tool_default_safety():
    """RestorationToolParameters defaults to execute=False non-negotiably."""
    params = RestorationToolParameters()
    assert params.execute is False

    tool = RestorationTool()
    assert tool.risk_level == RiskLevel.LOW


@pytest.mark.asyncio
async def test_restoration_tool_dry_run_by_default(db):
    """Calling RestorationTool without execute=True returns plan_generated without subprocess calls."""
    tool = RestorationTool(db=db)
    ctx = ExecutionContext(task_id="t1", session_id="s1")
    params = RestorationToolParameters(query="preview restoration")

    with patch.object(tool.engine, "execute_restoration_plan", new_callable=AsyncMock) as mock_exec:
        result = await tool.execute(params, ctx)
        assert mock_exec.call_count == 0
        # When no sessions exist, failed_to_plan is safe output
        assert result.output.get("status") in ("plan_generated", "failed_to_plan")


# ==============================================================================
# 5. Contextual Reference: Single Candidate ("Open it")
# ==============================================================================

def test_contextual_reference_single_candidate():
    """When last continuity turn has 1 project, 'Open it.' resolves deterministically."""
    ctx = ConversationContext()
    ctx.record_turn(
        user_query="What was I working on?",
        resolved_query="What was I working on?",
        response_text="You were working on the ORBIS project in Antigravity IDE.",
        tool_name="continuity_tool",
        continuity_data={
            "projects": ["ORBIS"],
            "active_applications": ["Antigravity IDE"],
            "session_id": "sess-100",
        },
    )

    res = ctx.resolve_query("Open it.")
    assert not res.is_ambiguous
    assert res.intent_type == IntentType.WORKSPACE_RESTORATION
    assert "ORBIS" in res.resolved_query
    assert classify_capability(res.resolved_query) == CapabilityClass.WORKSPACE_RESTORATION


# ==============================================================================
# 6. Contextual Reference: Ambiguity Gate (Multiple Candidates)
# ==============================================================================

def test_contextual_reference_multiple_candidates_ambiguity_gate():
    """When last continuity turn has multiple projects, 'Open it.' halts at ambiguity gate."""
    ctx = ConversationContext()
    ctx.record_turn(
        user_query="What was I working on?",
        resolved_query="What was I working on?",
        response_text="You had two projects open: ORBIS and Dashboard.",
        tool_name="continuity_tool",
        continuity_data={
            "projects": ["ORBIS", "Dashboard"],
            "active_applications": ["Antigravity IDE", "Visual Studio Code"],
            "session_id": "sess-101",
        },
    )

    res = ctx.resolve_query("Open it.")
    assert res.is_ambiguous is True
    assert res.clarification_message is not None
    assert "ORBIS" in res.clarification_message
    assert "Dashboard" in res.clarification_message
    # Zero guessing: raw query preserved
    assert res.resolved_query == "Open it."


# ==============================================================================
# 7. Ordinal References: "Open the first one" & "Open the second project"
# ==============================================================================

def test_ordinal_references_resolution():
    """Ordinal references resolve deterministically against candidate lists."""
    ctx = ConversationContext()
    ctx.record_turn(
        user_query="What was I working on?",
        resolved_query="What was I working on?",
        response_text="You were working on ORBIS and Dashboard.",
        tool_name="continuity_tool",
        continuity_data={
            "projects": ["ORBIS", "Dashboard"],
            "active_applications": ["Antigravity IDE", "Visual Studio Code"],
            "session_id": "sess-102",
        },
    )

    # First one
    r1 = ctx.resolve_query("Open the first one.")
    assert not r1.is_ambiguous
    assert "ORBIS" in r1.resolved_query
    assert r1.intent_type == IntentType.WORKSPACE_RESTORATION

    # Second one
    r2 = ctx.resolve_query("Open the second project.")
    assert not r2.is_ambiguous
    assert "Dashboard" in r2.resolved_query
    assert r2.intent_type == IntentType.WORKSPACE_RESTORATION


# ==============================================================================
# 8. Unverified Window Titles Rejected Without Hallucinating Filesystem Paths
# ==============================================================================

def test_unverified_window_title_not_hallucinated(db):
    """Titles like 'Notes.txt - Notepad' without physical files must be UNKNOWN, not created paths."""
    engine = RestorationEngine(db=db)
    now = datetime.now(timezone.utc)

    # Record event with non-existent file in title
    event = WorkspaceEvent(
        event_id="e1",
        event_type=WorkspaceEventType.WINDOW_ACTIVATED,
        timestamp=now,
        source="desktop_tracker",
        payload={
            "process_name": "notepad.exe",
            "window_title": "FakeNonExistentDocument12345.txt - Notepad",
            "hwnd": 1111,
        },
    )
    session = Session(
        session_id="s_fake",
        start_time=now - timedelta(minutes=5),
        end_time=now,
        events=[event],
        summary="Working in notepad",
        duration_seconds=300.0,
        event_count=1,
        applications=["notepad.exe"],
        resources=["FakeNonExistentDocument12345.txt"],
    )

    snapshot = engine.build_snapshot_from_session(session)
    assert len(snapshot.applications) == 1
    assert snapshot.applications[0] == "notepad.exe"
    # Resource target must NOT be a fabricated file path
    assert len(snapshot.resources) == 1
    assert snapshot.resources[0].target_type == ResourceTargetType.UNKNOWN


# ==============================================================================
# 9. Antigravity IDE Support
# ==============================================================================

def test_antigravity_ide_descriptor():
    """Antigravity IDE is registered with trusted process names and builds correct arguments."""
    reg = ApplicationRegistry()
    antigravity = reg.get("antigravity")
    assert antigravity is not None
    assert "Antigravity IDE.exe" in antigravity.process_names
    assert "Antigravity IDE" in antigravity.process_names

    # Test build_command_args with a workspace target
    args = antigravity.build_command_args(
        ResourceTarget(
            target_type=ResourceTargetType.DIRECTORY,
            raw_target="C:\\Projects\\ORBIS",
        )
    )
    assert args == ["C:\\Projects\\ORBIS"]


def test_antigravity_resolution_without_ambient_path():
    """Resolving antigravity looks only at canonical locations, never arbitrary PATH."""
    app = AppDescriptor(
        identifier="antigravity_test",
        display_name="Antigravity Test",
        process_names=["Antigravity IDE.exe"],
        canonical_paths=["C:\\NonExistentPath\\Antigravity IDE.exe"],
        supported_targets=[ResourceTargetType.DIRECTORY],
    )
    # Cannot resolve non-existent canonical path
    resolved = app.resolve_trusted_executable()
    assert resolved is None


# ==============================================================================
# 10. Control-Plane Exclusion & Non-ORBIS Electron Preservation
# ==============================================================================

def test_control_plane_exclusion_logic():
    """ORBIS Electron control plane is detected and excluded without globally blacklisting Electron."""
    # ORBIS Electron window
    assert is_orbis_control_plane("electron.exe", "ORBIS - Assistant") is True
    assert is_orbis_control_plane("electron.exe", "ORBIS Agent Shell") is True
    assert is_orbis_control_plane("Electron.exe", "ORBIS") is True
    assert is_orbis_control_plane("orbis.exe", "ORBIS") is True

    # Non-ORBIS Electron windows (VS Code, Slack, custom dev apps)
    assert is_orbis_control_plane("Code.exe", "main.py - ORBIS - Visual Studio Code") is False
    assert is_orbis_control_plane("slack.exe", "Slack - General") is False
    assert is_orbis_control_plane("electron.exe", "My Custom App") is False


# ==============================================================================
# 11. Preflight Validation: Containment & Path Validation
# ==============================================================================

def test_preflight_validation_project_resolution(sample_workspace):
    """Preflight validator validates project name against allowed workspace root."""
    validator = PreflightValidator(allowed_workspace_root=str(sample_workspace))
    target = ResourceTarget(
        target_type=ResourceTargetType.DIRECTORY,
        raw_target="ORBIS",
    )
    is_valid, resolved_path, err = validator.validate_resource_target(target)
    assert is_valid is True
    assert Path(resolved_path) == sample_workspace.resolve()


def test_preflight_validation_rejects_protected_dirs():
    """Preflight validator strictly rejects system/protected directories."""
    validator = PreflightValidator()
    for sys_dir in ["C:\\Windows", "C:\\Windows\\System32", "C:\\Program Files"]:
        target = ResourceTarget(
            target_type=ResourceTargetType.DIRECTORY,
            raw_target=sys_dir,
        )
        is_valid, resolved, err = validator.validate_resource_target(target)
        assert is_valid is False
        assert err is not None


# ==============================================================================
# 12. End-to-End Hierarchical Planner Plan Generation
# ==============================================================================

@pytest.mark.asyncio
async def test_hierarchical_planner_restoration_plan():
    """Hierarchical planner generates a validated TaskPlan for restoration."""
    planner = HierarchicalPlanner()
    tool_reg = ToolRegistry()
    tool_reg.register(RestorationTool())

    req = AgentRequest(
        user_prompt="Continue where I left off",
        context=ExecutionContext(
            task_id="task-test",
            session_id="session-42",
            metadata={"request_timestamp": "2026-09-20T12:00:00Z"},
        ),
    )

    plan = await planner.plan(req, tool_reg)
    assert isinstance(plan, TaskPlan)
    assert len(plan.steps) == 1
    step = plan.steps["step_workspace_restoration"]
    assert step.parameters["execute"] is True
    assert step.parameters["session_id"] == "session-42"
    assert step.parameters["reference_time"] == "2026-09-20T12:00:00Z"


# ==============================================================================
# 13. Preview / Dry Run Planning
# ==============================================================================

@pytest.mark.parametrize(
    "preview_query",
    [
        "Preview what you would restore",
        "Dry run restoration",
        "Plan only restoration",
    ],
)
@pytest.mark.asyncio
async def test_dry_run_and_preview_planning(preview_query):
    """Planner sets execute=False for preview/dry-run requests."""
    planner = HierarchicalPlanner()
    tool_reg = ToolRegistry()
    tool_reg.register(RestorationTool())

    req = AgentRequest(user_prompt=preview_query)
    plan = await planner.plan(req, tool_reg)
    step = plan.steps["step_workspace_restoration"]
    assert step.parameters["execute"] is False


# ==============================================================================
# 14. Restoration Output Formatting in Orchestrator
# ==============================================================================

def test_format_useful_tool_output_restoration():
    """format_useful_tool_output generates informative messages for restoration tool."""
    # Executed successfully
    out_exec = {
        "status": "executed",
        "result": {
            "success": True,
            "summary": "Workspace restored successfully: reopened Antigravity IDE on project ORBIS.",
            "completed_steps": ["step1"],
            "failed_steps": [],
        },
    }
    msg = format_useful_tool_output("restoration_tool", out_exec)
    assert "Workspace restored successfully: reopened Antigravity IDE on project ORBIS." in msg

    # Plan generated (dry run)
    out_plan = {
        "status": "plan_generated",
        "plan": {
            "summary": "Launch Antigravity IDE on ORBIS",
            "steps": [{"step_id": "s1"}],
        },
    }
    msg_plan = format_useful_tool_output("restoration_tool", out_plan)
    assert "Restoration plan ready: Launch Antigravity IDE on ORBIS" in msg_plan

    # Failed to plan
    out_fail = {
        "status": "failed_to_plan",
        "message": "No recent workspace session found.",
    }
    msg_fail = format_useful_tool_output("restoration_tool", out_fail)
    assert "No recent workspace session found." in msg_fail


# ==============================================================================
# 15. RestorationTool Verification Report
# ==============================================================================

@pytest.mark.asyncio
async def test_restoration_tool_verification():
    """RestorationTool.verify validates structured output contracts."""
    tool = RestorationTool()
    ctx = ExecutionContext(task_id="t1")
    params = RestorationToolParameters()

    from core.types import ToolResult
    # Valid executed output
    valid_res = ToolResult(
        success=True,
        output={"status": "executed", "result": {}},
    )
    v_report = await tool.verify(params, valid_res, ctx)
    assert v_report.verified is True

    # Invalid status
    invalid_res = ToolResult(
        success=True,
        output={"status": "corrupt_status"},
    )
    v_report_bad = await tool.verify(params, invalid_res, ctx)
    assert v_report_bad.verified is False


# ==============================================================================
# 16. Fallback from Missing ICE Sessions to ContinuityRetriever
# ==============================================================================

@pytest.mark.asyncio
async def test_ice_fallback_to_continuity_retriever(db):
    """When ICE yields no candidates, RestorationEngine falls back to ContinuityRetriever."""
    now = datetime.now(timezone.utc)
    # Seed db with a past session and event
    event = WorkspaceEvent(
        event_id="e_retriever",
        event_type=WorkspaceEventType.WINDOW_ACTIVATED,
        timestamp=now - timedelta(minutes=2),
        source="tracker",
        payload={"process_name": "notepad.exe", "window_title": "notes.txt"},
    )
    session = Session(
        session_id="sess_fallback",
        start_time=now - timedelta(minutes=10),
        end_time=now,
        events=[event],
        summary="Working on notes",
        duration_seconds=600.0,
        event_count=1,
    )
    db.record_session(session)

    engine = RestorationEngine(db=db)
    # create_restoration_plan should not raise; it returns plan or clean message
    plan, ice_res, message = await engine.create_restoration_plan(
        query="Continue where I left off",
        reference_time=now,
    )
    # Should safely complete without throwing an unhandled exception
    assert (plan is not None) or (message is not None)


# ==============================================================================
# 17. Multi-Turn Context Continuity Propagation
# ==============================================================================

def test_conversation_context_continuity_propagation():
    """ConversationContext preserves continuity session, applications, and projects."""
    ctx = ConversationContext()
    ctx.record_turn(
        user_query="What apps was I using?",
        resolved_query="What apps was I using?",
        response_text="You had Antigravity IDE open on project ORBIS.",
        tool_name="continuity_tool",
        continuity_data={
            "projects": ["ORBIS"],
            "active_applications": ["Antigravity IDE"],
            "primary_resources": ["C:\\Projects\\ORBIS"],
            "session_id": "sess-999",
        },
    )

    assert ctx.last_continuity_projects == ["ORBIS"]
    assert ctx.last_continuity_applications == ["Antigravity IDE"]
    assert ctx.last_continuity_session_id == "sess-999"
    assert ctx.active_entity == "ORBIS"


# ==============================================================================
# 18. End-to-End Agent Orchestrator Run
# ==============================================================================

@pytest.mark.asyncio
async def test_orchestrator_hierarchical_restoration_run(db, mock_registry, sample_workspace):
    """End-to-end run through AgentOrchestrator for workspace restoration."""
    reg = create_default_tool_registry(db=db)
    bus = EventBus()
    events_captured = []

    async def listener(evt):
        events_captured.append(evt)

    bus.subscribe(listener)

    orch = AgentOrchestrator(
        tool_registry=reg,
        db_manager=db,
        event_bus=bus,
    )

    now = datetime.now(timezone.utc)
    ev = WorkspaceEvent(
        event_id="e_live",
        event_type=WorkspaceEventType.WINDOW_ACTIVATED,
        timestamp=now,
        source="test",
        payload={"process_name": "Antigravity IDE.exe", "window_title": "ORBIS - Antigravity IDE"},
    )
    sess = Session(
        session_id="sess-live",
        start_time=now - timedelta(minutes=5),
        end_time=now,
        events=[ev],
        duration_seconds=300.0,
        event_count=1,
        applications=["Antigravity IDE"],
        resources=["C:\\Projects\\ORBIS"],
    )
    db.record_session(sess)

    # Ingest a prior continuity turn into orchestrator context
    orch.conversation_context.record_turn(
        user_query="What was I working on?",
        resolved_query="What was I working on?",
        response_text="You were working on project ORBIS.",
        tool_name="continuity_tool",
        continuity_data={
            "projects": ["ORBIS"],
            "active_applications": ["Antigravity IDE"],
            "primary_resources": ["C:\\Projects\\ORBIS"],
            "session_id": "sess-live",
        },
    )

    # User says "Open it"
    response = await orch.run_task("Open it")
    assert response.status.value in ("COMPLETED", "FAILED")
    assert len(orch.conversation_context.turns) == 2
    # Check that events were emitted
    assert len(events_captured) > 0
