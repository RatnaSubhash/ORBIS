"""
Permanent regression test suite for ORBIS Security Remediation (FIND-01 through FIND-07).
Validates:
- FIND-01: Orchestrator run() unifies with run_task(), dynamic risk detection, PendingApproval enforcement
- FIND-02: FileTool protected-file blocking, secret redaction, traversal prevention
- FIND-03: SQLite in-memory concurrency, isolated rollback via savepoints and RLock
- FIND-04: HierarchicalPlanner rejection of arbitrary fallback tools (no fallback to terminal)
- FIND-06: Voice optional dependencies packaging declaration in pyproject.toml
- FIND-07: Restoration evidence hierarchy for extensionless resources (unknown classification)
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import pytest

from core.agent.executor.executor import TaskExecutor
from core.agent.llm.mock import MockLLMProvider
from core.agent.orchestrator import AgentOrchestrator
from core.agent.planner.hierarchical import HierarchicalPlanner
from core.config import get_settings
from core.memory.db import DatabaseManager
from core.restoration.engine import RestorationEngine
from core.restoration.models import ResourceTargetType
from core.tools.base import BaseTool
from core.tools.document_tool import DocumentTool
from core.tools.file_tool import FileActionParameters, FileTool
from core.tools.registry import ToolRegistry
from core.agent.models import PendingApproval
from core.ice.models import IntentCandidate, IntentProvenance, TaskContinuityState
from core.types import (
    ExecutionContext,
    RiskLevel,
    Session,
    StatusEventType,
    ToolCall,
    ToolResult,
)


# ==============================================================================
# FIND-01: ORCHESTRATOR DUAL PIPELINE UNIFICATION
# ==============================================================================

class MockContentTool(BaseTool):
    name = "content_generator"
    description = "Generates content"
    version = "1.0.0"
    risk_level = RiskLevel.LOW
    def validate_parameters(self, params):
        return params
    async def execute(self, params, context):
        return ToolResult(success=True, output={"content": "# Test content"})
    async def verify(self, params, result, context):
        from core.types import VerificationReport
        return VerificationReport(verified=True, details="OK")


@pytest.mark.asyncio
async def test_find01_run_uses_run_task_pipeline_and_enforces_approval(tmp_path: Path) -> None:
    """Proves orchestrator.run() routes through run_task() and halts on existing file overwrite approval."""
    test_file = tmp_path / "README.md"
    test_file.write_text("initial contents")

    doc_tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    registry = ToolRegistry()
    registry.register(doc_tool)
    registry.register(MockContentTool())

    db = DatabaseManager(":memory:")
    db.initialize()
    planner = HierarchicalPlanner()
    orchestrator = AgentOrchestrator(
        tool_registry=registry,
        db_manager=db,
        llm_provider=MockLLMProvider(),
        planner=planner,
    )

    resp = await orchestrator.run(
        "Create a README for my project.",
        task_id="task_find01_1",
    )

    assert resp.status == StatusEventType.FAILED
    assert "README.md already exists" in resp.summary
    assert orchestrator._pending_approval is not None
    assert isinstance(orchestrator._pending_approval, PendingApproval)
    assert orchestrator._pending_approval.blocked_step_id == "step_create_document"
    assert orchestrator._pending_approval.tool_name == "document"
    assert orchestrator._pending_approval.target_resource == "README.md"
    assert test_file.read_text() == "initial contents"


@pytest.mark.asyncio
async def test_find01_run_cannot_bypass_dynamic_risk(tmp_path: Path) -> None:
    """Proves orchestrator.run() evaluates get_effective_risk dynamically."""
    existing_file = tmp_path / "report.md"
    existing_file.write_text("# Old Report")

    doc_tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    registry = ToolRegistry()
    registry.register(doc_tool)
    registry.register(MockContentTool())

    db = DatabaseManager(":memory:")
    db.initialize()
    planner = HierarchicalPlanner()
    orchestrator = AgentOrchestrator(
        tool_registry=registry,
        db_manager=db,
        llm_provider=MockLLMProvider(),
        planner=planner,
    )

    resp = await orchestrator.run("Generate a report on AI to report.md", task_id="task_find01_2")
    assert orchestrator._pending_approval is not None
    assert orchestrator._pending_approval.target_resource == "report.md"


@pytest.mark.asyncio
async def test_find01_run_cannot_bypass_pending_approval(tmp_path: Path) -> None:
    """Proves that a task halted under run() cannot bypass PendingApproval and can be resumed."""
    test_file = tmp_path / "README.md"
    test_file.write_text("initial contents")

    doc_tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    registry = ToolRegistry()
    registry.register(doc_tool)
    registry.register(MockContentTool())

    db = DatabaseManager(":memory:")
    db.initialize()
    planner = HierarchicalPlanner()
    orchestrator = AgentOrchestrator(
        tool_registry=registry,
        db_manager=db,
        llm_provider=MockLLMProvider(),
        planner=planner,
    )

    # First attempt: Halted at gate
    resp = await orchestrator.run("Create a README for my project.")
    assert resp.status == StatusEventType.FAILED
    assert orchestrator._pending_approval is not None
    assert test_file.read_text() == "initial contents"

    # Second turn via run(): Affirmative approval resumes and completes
    resp2 = await orchestrator.run("yes, overwrite it")
    assert resp2.status == StatusEventType.COMPLETED
    assert orchestrator._pending_approval is None
    assert "# Test content" in test_file.read_text()


@pytest.mark.asyncio
async def test_find01_run_phase1_behavior_preserved() -> None:
    """Proves that safe non-conflicting tasks continue to execute cleanly via run()."""
    from core.tools.echo_tool import EchoTool

    registry = ToolRegistry()
    registry.register(EchoTool())

    db = DatabaseManager(":memory:")
    db.initialize()
    mock_call = ToolCall(tool_name="echo", parameters={"message": "echoed message"})
    llm = MockLLMProvider(default_tool_call=mock_call)
    orchestrator = AgentOrchestrator(tool_registry=registry, db_manager=db, llm_provider=llm)

    resp = await orchestrator.run("echo hello", task_id="task_find01_3")
    assert resp.status == StatusEventType.COMPLETED
    assert resp.tool_result is not None
    assert resp.tool_result.output == "echoed message"


# ==============================================================================
# FIND-02: FILETOOL PROTECTED FILE BLOCKING & SECRET REDACTION
# ==============================================================================

@pytest.mark.asyncio
async def test_find02_env_read_blocked(tmp_path: Path) -> None:
    """Proves .env and .env.* reads are strictly blocked without leaking file content."""
    env_file = tmp_path / ".env"
    env_file.write_text("SUPER_SECRET_KEY=supersecret123456789")

    tool = FileTool(allowed_workspace_root=str(tmp_path))
    context = ExecutionContext(task_id="test_find02_1")

    params = FileActionParameters(action="read_file", path=".env")
    result = await tool.execute(params, context)

    assert result.success is False
    assert "Access denied" in (result.error or "")
    assert result.output is None
    assert "supersecret" not in str(result.error)
    assert "supersecret" not in str(result.metadata)


@pytest.mark.asyncio
async def test_find02_private_key_read_blocked(tmp_path: Path) -> None:
    """Proves private key file reads are strictly blocked."""
    key_file = tmp_path / "id_rsa"
    key_file.write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----")

    tool = FileTool(allowed_workspace_root=str(tmp_path))
    context = ExecutionContext(task_id="test_find02_2")

    params = FileActionParameters(action="read_file", path="id_rsa")
    result = await tool.execute(params, context)

    assert result.success is False
    assert "Access denied" in (result.error or "")
    assert result.output is None


@pytest.mark.asyncio
async def test_find02_credentials_file_read_blocked(tmp_path: Path) -> None:
    """Proves credentials files are strictly blocked."""
    cred_file = tmp_path / "credentials.json"
    cred_file.write_text('{"client_secret": "my-secret-token"}')

    tool = FileTool(allowed_workspace_root=str(tmp_path))
    context = ExecutionContext(task_id="test_find02_3")

    params = FileActionParameters(action="read_file", path="credentials.json")
    result = await tool.execute(params, context)

    assert result.success is False
    assert "Access denied" in (result.error or "")
    assert result.output is None


@pytest.mark.asyncio
async def test_find02_normal_text_file_read_succeeds(tmp_path: Path) -> None:
    """Proves normal workspace files read cleanly within bounds."""
    doc = tmp_path / "hello.txt"
    doc.write_text("Normal non-sensitive content.")

    tool = FileTool(allowed_workspace_root=str(tmp_path))
    context = ExecutionContext(task_id="test_find02_4")

    params = FileActionParameters(action="read_file", path="hello.txt")
    result = await tool.execute(params, context)

    assert result.success is True
    assert result.output["content"] == "Normal non-sensitive content."


@pytest.mark.asyncio
async def test_find02_api_key_redaction_in_allowed_file(tmp_path: Path) -> None:
    """Proves identifiable secrets in allowed files are redacted before returning to agent."""
    code_file = tmp_path / "config.py"
    code_file.write_text('OPENAI_API_KEY = "sk-1234567890abcdef1234567890abcdef"\nDEBUG = True')

    tool = FileTool(allowed_workspace_root=str(tmp_path))
    context = ExecutionContext(task_id="test_find02_5")

    params = FileActionParameters(action="read_file", path="config.py")
    result = await tool.execute(params, context)

    assert result.success is True
    assert "sk-1234567890abcdef" not in result.output["content"]
    assert "[REDACTED API KEY]" in result.output["content"]
    assert "DEBUG = True" in result.output["content"]


@pytest.mark.asyncio
async def test_find02_traversal_remains_blocked(tmp_path: Path) -> None:
    """Proves path traversal attempts are rejected."""
    tool = FileTool(allowed_workspace_root=str(tmp_path))
    context = ExecutionContext(task_id="test_find02_6")

    params = FileActionParameters(action="read_file", path="../outside.txt")
    result = await tool.execute(params, context)

    assert result.success is False
    assert "Path safety violation" in (result.error or "")


# ==============================================================================
# FIND-03: IN-MEMORY SQLITE CONCURRENCY / ROLLBACK ISOLATION
# ==============================================================================

@pytest.mark.asyncio
async def test_find03_in_memory_concurrency_and_rollback_isolation() -> None:
    """
    Real concurrency regression test with asyncio.gather():
    - Task A begins a write, remains uncommitted
    - Another task (Task Fail) fails and rolls back
    - Task B performs a valid write
    - Task B must NOT have its successful transaction destroyed by Task A or Task Fail.
    - Failure correctly rolls back the failing operation.
    """
    db = DatabaseManager(":memory:")
    db.initialize()

    ev_a_started = asyncio.Event()
    ev_others_done = asyncio.Event()

    async def task_a():
        with db.get_connection() as conn:
            conn.execute("INSERT INTO audit_logs (task_id, action_name, risk_level, parameters_json) VALUES ('task_a', 'write_a', 'LOW', '{}');")
            ev_a_started.set()
            # Task A remains uncommitted while task_fail and task_b run
            await ev_others_done.wait()
            conn.execute("UPDATE audit_logs SET action_name = 'write_a_committed' WHERE task_id = 'task_a';")

    async def task_fail():
        await ev_a_started.wait()
        try:
            with db.get_connection() as conn:
                conn.execute("INSERT INTO audit_logs (task_id, action_name, risk_level, parameters_json) VALUES ('task_fail', 'write_fail', 'HIGH', '{}');")
                raise RuntimeError("Intentional Task Fail failure")
        except RuntimeError:
            pass

    async def task_b():
        await ev_a_started.wait()
        with db.get_connection() as conn:
            conn.execute("INSERT INTO audit_logs (task_id, action_name, risk_level, parameters_json) VALUES ('task_b', 'write_b', 'LOW', '{}');")
        ev_others_done.set()

    await asyncio.gather(task_a(), task_fail(), task_b())

    # Query results
    with db.get_connection() as conn:
        rows = conn.execute("SELECT task_id, action_name FROM audit_logs ORDER BY task_id;").fetchall()
        row_dict = {r["task_id"]: r["action_name"] for r in rows}

    # Task A's write committed
    assert "task_a" in row_dict
    assert row_dict["task_a"] == "write_a_committed"

    # Task B's write succeeded and was not destroyed
    assert "task_b" in row_dict
    assert row_dict["task_b"] == "write_b"

    # Failing task rolled back cleanly
    assert "task_fail" not in row_dict


# ==============================================================================
# FIND-04: UNSAFE FIRST-TOOL FALLBACK
# ==============================================================================

@pytest.mark.asyncio
async def test_find04_planner_never_arbitrarily_selects_first_tool() -> None:
    """Proves HierarchicalPlanner never selects terminal or arbitrary tools when echo is absent."""
    class TerminalDummyTool:
        name = "terminal"
        description = "Terminal execution"
        version = "1.0.0"
        risk_level = RiskLevel.HIGH
        parameters_schema = None

    class CustomTool:
        name = "custom_tool"
        description = "Custom tool"
        version = "1.0.0"
        risk_level = RiskLevel.LOW
        parameters_schema = None

    registry = ToolRegistry()
    registry.register(TerminalDummyTool())
    registry.register(CustomTool())

    from core.agent.models import AgentRequest

    planner = HierarchicalPlanner()
    req = AgentRequest(user_prompt="unsupported user query")

    # Planner should raise ValueError rather than picking terminal or arbitrary tool
    with pytest.raises(ValueError, match="No suitable tool found to satisfy request and no safe echo fallback"):
        planner._plan_default_fallback(req, registry)

    # Verify echo fallback still works if echo is present
    class EchoDummyTool:
        name = "echo"
        description = "Echo"
        version = "1.0.0"
        risk_level = RiskLevel.LOW
        parameters_schema = None

    registry.register(EchoDummyTool())
    plan = planner._plan_default_fallback(req, registry)
    first_step = list(plan.steps.values())[0]
    assert first_step.tool_name == "echo"


# ==============================================================================
# FIND-06: VOICE DEPENDENCY PACKAGING
# ==============================================================================

def test_find06_voice_optional_dependencies_declared() -> None:
    """Verifies pyproject.toml defines optional-dependencies.voice with sounddevice and faster-whisper."""
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    assert pyproject_path.exists(), "pyproject.toml not found"

    content = pyproject_path.read_text(encoding="utf-8")
    assert "[project.optional-dependencies]" in content
    assert "voice = [" in content
    assert "sounddevice>=" in content
    assert "faster-whisper>=" in content


# ==============================================================================
# FIND-07: EXTENSIONLESS RESOURCE CLASSIFICATION EVIDENCE HIERARCHY
# ==============================================================================

def test_find07_extensionless_resource_classification(tmp_path: Path) -> None:
    """
    Verifies evidence hierarchy for resource classification in build_snapshot_from_session:
    - existing file -> WORKSPACE_FILE
    - existing directory -> DIRECTORY
    - nonexistent extensionless file -> UNKNOWN
    - nonexistent extensionless directory with explicit metadata -> DIRECTORY
    - ambiguous target -> UNKNOWN
    """
    db = DatabaseManager(":memory:")
    db.initialize()

    # Setup files on disk
    real_file = tmp_path / "existing_file.txt"
    real_file.write_text("content")

    real_dir = tmp_path / "existing_dir"
    real_dir.mkdir()

    session = Session(
        session_id="session_f7_test",
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc),
        duration_seconds=10.0,
        event_count=5,
        applications=["notepad.exe"],
        resources=[
            str(real_file),             # 1. Existing file
            str(real_dir),              # 2. Existing dir
            "Makefile",                 # 3. Nonexistent extensionless file (should be UNKNOWN)
            "build_output",             # 4. Nonexistent with metadata explicitly saying DIRECTORY
            "AmbiguousResource",        # 5. Nonexistent extensionless without metadata
        ],
        session_metadata={
            "resource_types": {
                "build_output": "DIRECTORY",
            }
        },
    )
    db.record_session(session)

    from core.restoration.validator import PreflightValidator
    validator = PreflightValidator(allowed_workspace_root=str(tmp_path))
    engine = RestorationEngine(db=db, validator=validator)

    intent = IntentCandidate(
        goal_description="test goal",
        task_state=TaskContinuityState.ACTIVE,
        state_reasoning="continuation testing",
        provenance=IntentProvenance(session_ids=[session.session_id]),
    )

    snapshot = engine.build_snapshot_from_session(session.session_id, intent)
    assert snapshot is not None

    targets_by_raw = {r.raw_target: r.target_type for r in snapshot.resources}

    # 1. Existing file
    assert targets_by_raw[str(real_file)] == ResourceTargetType.WORKSPACE_FILE

    # 2. Existing directory
    assert targets_by_raw[str(real_dir)] == ResourceTargetType.DIRECTORY

    # 3. Nonexistent extensionless file is NOT classified as DIRECTORY
    assert targets_by_raw["Makefile"] == ResourceTargetType.UNKNOWN

    # 4. Nonexistent extensionless directory with explicit metadata is DIRECTORY
    assert targets_by_raw["build_output"] == ResourceTargetType.DIRECTORY

    # 5. Ambiguous/unresolved target is UNKNOWN
    assert targets_by_raw["AmbiguousResource"] == ResourceTargetType.UNKNOWN
