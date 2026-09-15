"""
Capability Stabilization & Live Integration Tests for ORBIS.
Verifies fixes for:
- Issue A: Generalized multi-step research + file creation (no sample.txt assumption)
- Issue B: Workspace file query with safe path normalization (no '/' escape violation)
- Issue C: Desktop tool result surfacing to user response
- Issue D: Factual workspace continuity (honest empty fallback and populated retrieval)
- Section 3: General multi-step intent decomposition classes
"""

import os
from pathlib import Path
import tempfile
import pytest

from core.agent.llm.mock import MockLLMProvider
from core.agent.models import AgentRequest, TaskState, TaskType
from core.agent.orchestrator import AgentOrchestrator
from core.agent.planner.hierarchical import HierarchicalPlanner
from core.observation.mock import MockWorkspaceObserver
from core.tools.desktop_tool import DesktopActionParameters, DesktopTool
from core.tools.document_tool import DocumentTool
from core.tools.file_tool import FileActionParameters, FileTool
from core.tools.registry import ToolRegistry, create_default_tool_registry
from core.voice.service import extract_spoken_response


# ============================================================================
# Issue A: Multi-Step Research + File Creation
# ============================================================================
@pytest.mark.asyncio
async def test_multi_step_research_and_text_file_creation_plan():
    """
    Verifies that 'Find three good computer network project ideas, compare them and
    create a text file containing your recommendations' produces a 3-step DAG
    (web_research -> content_generator -> document) and does NOT invent a sample.txt dependency.
    """
    registry = create_default_tool_registry()
    planner = HierarchicalPlanner()
    prompt = "Find three good computer network project ideas, compare them and create a text file containing your recommendations."

    plan = await planner.plan(AgentRequest(user_prompt=prompt), registry)

    assert plan.task_type == TaskType.SEQUENTIAL
    assert "step_web_research" in plan.steps
    assert "step_generate_content" in plan.steps
    assert "step_create_document" in plan.steps

    # Confirm NO sample.txt was invented
    assert "step_locate" not in plan.steps
    assert "sample.txt" not in str(plan.steps)

    # Confirm correct document step parameters
    doc_step = plan.steps["step_create_document"]
    assert doc_step.parameters["file_path"] == "recommendations.txt"
    assert doc_step.parameters["format"] == "text"


@pytest.mark.asyncio
async def test_multi_step_research_and_text_file_creation_execution():
    """
    End-to-end execution of Issue A request using orchestrator.
    Verifies that web research runs, content is synthesized, text document is written to disk,
    and verified independently.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = create_default_tool_registry()
        # Override document and file tools with isolated temp directory
        doc_tool = DocumentTool(allowed_workspace_root=tmp_dir)
        file_tool = FileTool(allowed_workspace_root=tmp_dir)
        registry._tools["document"] = doc_tool
        registry._tools["file"] = file_tool

        planner = HierarchicalPlanner()
        orchestrator = AgentOrchestrator(
            llm_provider=MockLLMProvider(),
            tool_registry=registry,
            planner=planner,
        )

        prompt = "Find three good computer network project ideas, compare them and create a text file containing your recommendations."
        result = await orchestrator.run_task(prompt)

        assert result.status == TaskState.COMPLETED
        assert result.completed_steps == ["step_web_research", "step_generate_content", "step_create_document"]

        # Confirm target file was created on disk in allowed workspace
        target_file = Path(tmp_dir) / "recommendations.txt"
        assert target_file.exists()
        assert target_file.is_file()
        content = target_file.read_text(encoding="utf-8")
        assert len(content) > 0


# ============================================================================
# Issue B: Workspace File Query & Safe Path Normalization
# ============================================================================
@pytest.mark.asyncio
async def test_workspace_file_query_path_normalization():
    """
    Verifies that '/', '\\', '', and '.' all resolve strictly to the allowed workspace root
    without throwing a Path safety violation.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tool = FileTool(allowed_workspace_root=tmp_dir)
        canonical_root = Path(tmp_dir).resolve()

        assert tool.resolve_safe_path("/") == canonical_root
        assert tool.resolve_safe_path("\\") == canonical_root
        assert tool.resolve_safe_path(".") == canonical_root
        assert tool.resolve_safe_path("") == canonical_root
        assert tool.resolve_safe_path("/subdir") == canonical_root / "subdir"


@pytest.mark.asyncio
async def test_workspace_file_query_security_preservation():
    """
    Confirms that path normalization does NOT weaken traversal or UNC protections.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tool = FileTool(allowed_workspace_root=tmp_dir)

        with pytest.raises(ValueError, match="Traversal"):
            tool.resolve_safe_path("../outside")

        with pytest.raises(ValueError, match="UNC network paths are prohibited"):
            tool.resolve_safe_path("//attacker-server/share")

        with pytest.raises(ValueError, match="resolves outside allowed root"):
            tool.resolve_safe_path("C:/Windows/System32")


@pytest.mark.asyncio
async def test_workspace_file_query_via_orchestrator():
    """
    Verifies natural-language request 'Check the files in my Orbis workspace.'
    executes cleanly and surfaces the directory listing.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Create a couple of sample files in the workspace
        (Path(tmp_dir) / "alpha.txt").write_text("hello", encoding="utf-8")
        (Path(tmp_dir) / "beta.md").write_text("world", encoding="utf-8")

        registry = ToolRegistry()
        file_tool = FileTool(allowed_workspace_root=tmp_dir)
        registry.register(file_tool)

        planner = HierarchicalPlanner()
        orchestrator = AgentOrchestrator(
            llm_provider=MockLLMProvider(),
            tool_registry=registry,
            planner=planner,
        )

        response = await orchestrator.run("Check the files in my Orbis workspace.")
        assert response.status.value == "COMPLETED"
        assert "alpha.txt" in response.summary or "beta.md" in response.summary
        assert "Found" in response.summary


# ============================================================================
# Issue C: Active Application Query Surfacing
# ============================================================================
@pytest.mark.asyncio
async def test_active_application_query_surfacing_in_orchestrator():
    """
    Verifies that 'What application am I using right now?' surfaces the actual
    application identity (e.g. Antigravity) rather than a generic task completion message.
    """
    observer = MockWorkspaceObserver()
    observer.set_active_window(
        title="pyproject.toml - ORBIS - Antigravity",
        process_name="Antigravity.exe",
        process_id=4096,
    )

    registry = ToolRegistry()
    registry.register(DesktopTool(observer=observer))

    planner = HierarchicalPlanner()
    orchestrator = AgentOrchestrator(
        llm_provider=MockLLMProvider(),
        tool_registry=registry,
        planner=planner,
    )

    response = await orchestrator.run("What application am I using right now?")
    assert response.status.value == "COMPLETED"
    assert "Antigravity" in response.summary
    assert "Multi-step task completed successfully" not in response.summary


@pytest.mark.asyncio
async def test_active_application_voice_spoken_extraction():
    """
    Verifies that extract_spoken_response surfaces the active application
    from DesktopTool execution output.
    """
    observer = MockWorkspaceObserver()
    observer.set_active_window(
        title="Visual Studio Code",
        process_name="Code.exe",
        process_id=2024,
    )

    registry = ToolRegistry()
    registry.register(DesktopTool(observer=observer))

    orchestrator = AgentOrchestrator(
        llm_provider=MockLLMProvider(),
        tool_registry=registry,
        planner=HierarchicalPlanner(),
    )

    exec_res = await orchestrator.run_task("What application am I using right now?")
    spoken = extract_spoken_response(exec_res)

    assert "Code" in spoken or "Visual Studio" in spoken
    assert "Multi-step task completed successfully" not in spoken


# ============================================================================
# Issue D: Workspace Continuity Grounded Fallback
# ============================================================================
@pytest.mark.asyncio
async def test_workspace_continuity_honest_fallback_when_empty():
    """
    Verifies that 'What was I working on?' honestly states that no activity or
    sessions were recorded when the database is empty, adhering to strict grounding.
    """
    from core.continuity.service import ContinuityService
    from core.memory.db import DatabaseManager

    db = DatabaseManager(db_path=":memory:")
    db.initialize()
    service = ContinuityService(db=db)

    result = service.execute_query("What was I working on?")
    assert result.has_sufficient_context is False
    assert "insufficient" in result.grounded_summary.lower() or "no recorded" in result.grounded_summary.lower()


# ============================================================================
# Section 3: General Multi-Step Planning Classes
# ============================================================================
@pytest.mark.asyncio
async def test_plan_research_hsrp_and_markdown():
    """
    Class 1: 'Research HSRP, summarize it, and create a Markdown file.'
    Expected: research -> synthesis -> document -> verification
    """
    registry = create_default_tool_registry()
    planner = HierarchicalPlanner()
    prompt = "Research HSRP, summarize it, and create a Markdown file."

    plan = await planner.plan(AgentRequest(user_prompt=prompt), registry)

    assert plan.task_type == TaskType.SEQUENTIAL
    assert list(plan.steps.keys()) == ["step_web_research", "step_generate_content", "step_create_document"]
    assert plan.steps["step_web_research"].parameters["query"] == "HSRP"
    assert plan.steps["step_create_document"].parameters["format"] == "markdown"


@pytest.mark.asyncio
async def test_plan_create_readme_for_project():
    """
    Class 3: 'Create a README for my Computer Networks project.'
    Expected: content generation -> document -> verification (no web research needed)
    """
    registry = create_default_tool_registry()
    planner = HierarchicalPlanner()
    prompt = "Create a README for my Computer Networks project."

    plan = await planner.plan(AgentRequest(user_prompt=prompt), registry)

    assert list(plan.steps.keys()) == ["step_generate_content", "step_create_document"]
    assert plan.steps["step_create_document"].parameters["file_path"] == "README.md"
    assert plan.steps["step_create_document"].parameters["format"] == "markdown"


@pytest.mark.asyncio
async def test_plan_research_tcp_congestion_notes():
    """
    Class 4: 'Research TCP congestion control and create study notes.'
    Expected: research -> synthesis -> document
    """
    registry = create_default_tool_registry()
    planner = HierarchicalPlanner()
    prompt = "Research TCP congestion control and create study notes."

    plan = await planner.plan(AgentRequest(user_prompt=prompt), registry)

    assert list(plan.steps.keys()) == ["step_web_research", "step_generate_content", "step_create_document"]
    assert "TCP congestion control" in plan.steps["step_web_research"].parameters["query"]
    assert "notes" in plan.steps["step_create_document"].parameters["file_path"]


# ============================================================================
# FINDING-STAB-01: Protected Files Filtered from Directory Listings
# ============================================================================
@pytest.mark.asyncio
async def test_finding_stab_01_list_directory_filters_protected_files():
    """
    Verifies that FileTool.list_directory() strictly filters out protected files
    (.env, .env.*, .git, credentials, id_rsa, *.pem, *.key) while keeping normal files visible,
    and confirms direct protected-file reads remain blocked.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        # Create protected resources
        (root / ".env").write_text("SECRET=123", encoding="utf-8")
        (root / ".env.local").write_text("LOCAL_SECRET=456", encoding="utf-8")
        (root / ".env.production").write_text("PROD_SECRET=789", encoding="utf-8")
        (root / "credentials.json").write_text('{"key": "val"}', encoding="utf-8")
        (root / "id_rsa").write_text("PRIVATE KEY DATA", encoding="utf-8")
        (root / "server.pem").write_text("CERT DATA", encoding="utf-8")
        (root / "private.key").write_text("KEY DATA", encoding="utf-8")
        git_dir = root / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("git config", encoding="utf-8")

        # Create normal resources
        (root / "README.md").write_text("# Documentation", encoding="utf-8")
        (root / "main.py").write_text("print('hello')", encoding="utf-8")
        (root / "notes.txt").write_text("normal notes", encoding="utf-8")

        file_tool = FileTool(allowed_workspace_root=tmp_dir)
        from core.types import ExecutionContext
        ctx = ExecutionContext(task_id="test_stab_01")

        # 1. Execute list_directory
        list_params = FileActionParameters(action="list_directory", path=".")
        list_result = await file_tool.execute(list_params, ctx)

        assert list_result.success is True
        entry_names = [e["name"] for e in list_result.output["entries"]]

        # 1 & 2. Protected files must be absent from directory listing
        protected_names = [
            ".env", ".env.local", ".env.production", ".git",
            "credentials.json", "id_rsa", "server.pem", "private.key"
        ]
        for prot in protected_names:
            assert prot not in entry_names, f"Protected file '{prot}' was exposed in list_directory!"

        # 3. Normal files remain visible
        assert "README.md" in entry_names
        assert "main.py" in entry_names
        assert "notes.txt" in entry_names
        assert list_result.output["total_entries_sampled"] == 3

        # 4. Direct protected-file reads remain blocked
        for prot in (".env", ".env.production", "credentials.json", "id_rsa", "server.pem"):
            read_params = FileActionParameters(action="read_file", path=prot)
            read_result = await file_tool.execute(read_params, ctx)
            assert read_result.success is False
            assert "Access denied" in read_result.error

        # Direct normal file read succeeds
        read_normal = FileActionParameters(action="read_file", path="README.md")
        norm_res = await file_tool.execute(read_normal, ctx)
        assert norm_res.success is True
        assert norm_res.output["content"] == "# Documentation"


@pytest.mark.asyncio
async def test_finding_stab_01_voice_and_orchestrator_summary_does_not_surface_protected_names():
    """
    Verifies that neither AgentOrchestrator summary nor VoiceService spoken output
    can surface protected filenames when listing workspace directory contents.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        # Create sensitive files that would previously sort to the top
        (root / ".env").write_text("SECRET=123", encoding="utf-8")
        (root / ".env.example").write_text("SECRET=example", encoding="utf-8")
        (root / ".git").mkdir()
        (root / "credentials.json").write_text("{}", encoding="utf-8")

        # Create normal files
        (root / "app.py").write_text("code", encoding="utf-8")
        (root / "config.toml").write_text("title = 'app'", encoding="utf-8")
        (root / "README.md").write_text("docs", encoding="utf-8")

        registry = create_default_tool_registry()
        registry.get("file").allowed_root = Path(tmp_dir).resolve()
        planner = HierarchicalPlanner()
        orchestrator = AgentOrchestrator(
            llm_provider=MockLLMProvider(),
            tool_registry=registry,
            planner=planner,
        )

        result = await orchestrator.run_task("Check the files in my ORBIS workspace.")

        assert result.status == TaskState.COMPLETED

        # Verify summary does not contain protected names
        summary = result.summary
        assert ".env" not in summary
        assert ".git" not in summary
        assert "credentials" not in summary

        # Verify summary contains normal files
        assert "app.py" in summary or "config.toml" in summary or "README.md" in summary

        # Verify spoken voice output does not contain protected names
        spoken = extract_spoken_response(result)
        assert ".env" not in spoken
        assert ".git" not in spoken
        assert "credentials" not in spoken

