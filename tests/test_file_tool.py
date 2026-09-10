"""
Unit and integration tests for FileTool.
Validates strictly read-only execution, path safety boundary enforcement,
directory traversal rejection, independent verification, and orchestrator integration.
"""

from pathlib import Path
import tempfile
import pytest

from core.agent.llm.mock import MockLLMProvider
from core.agent.orchestrator import AgentOrchestrator
from core.tools.file_tool import FileActionParameters, FileTool
from core.tools.registry import ToolRegistry
from core.types import ExecutionContext, RiskLevel, StatusEventType, ToolCall, ToolResult


@pytest.fixture
def temp_workspace():
    """Sets up a temporary directory with files and subdirectories for testing."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        # Create test files and dirs
        file1 = root / "sample.txt"
        file1.write_text("Hello ORBIS file content")

        file2 = root / "script.py"
        file2.write_text("print('test')")

        sub = root / "nested_dir"
        sub.mkdir()
        (sub / "nested_file.md").write_text("# Markdown")

        yield root


@pytest.mark.asyncio
async def test_file_tool_check_exists(temp_workspace: Path) -> None:
    """Verifies check_exists returns correct boolean existence and type flags."""
    tool = FileTool(allowed_workspace_root=str(temp_workspace))
    context = ExecutionContext(task_id="task_ft_1")

    # Existing file
    params_exist = FileActionParameters(action="check_exists", path="sample.txt")
    result_exist = await tool.execute(params_exist, context)
    assert result_exist.success is True
    assert result_exist.output["exists"] is True
    assert result_exist.output["is_file"] is True
    assert result_exist.output["is_dir"] is False

    report = await tool.verify(params_exist, result_exist, context)
    assert report.verified is True

    # Non-existent file
    params_missing = FileActionParameters(action="check_exists", path="missing.txt")
    result_missing = await tool.execute(params_missing, context)
    assert result_missing.success is True
    assert result_missing.output["exists"] is False


@pytest.mark.asyncio
async def test_file_tool_inspect_path(temp_workspace: Path) -> None:
    """Verifies inspect_path returns size, modification time, and file extension."""
    tool = FileTool(allowed_workspace_root=str(temp_workspace))
    context = ExecutionContext(task_id="task_ft_2")

    params = FileActionParameters(action="inspect_path", path="sample.txt")
    result = await tool.execute(params, context)

    assert result.success is True
    assert result.output["name"] == "sample.txt"
    assert result.output["extension"] == ".txt"
    assert result.output["size_bytes"] == len("Hello ORBIS file content")
    assert "modified_at" in result.output

    report = await tool.verify(params, result, context)
    assert report.verified is True


@pytest.mark.asyncio
async def test_file_tool_list_directory(temp_workspace: Path) -> None:
    """Verifies bounded list_directory execution."""
    tool = FileTool(allowed_workspace_root=str(temp_workspace))
    context = ExecutionContext(task_id="task_ft_3")

    params = FileActionParameters(action="list_directory", path=".", max_entries=10)
    result = await tool.execute(params, context)

    assert result.success is True
    assert result.output["total_entries_sampled"] == 3  # sample.txt, script.py, nested_dir
    entry_names = [e["name"] for e in result.output["entries"]]
    assert "sample.txt" in entry_names
    assert "nested_dir" in entry_names

    report = await tool.verify(params, result, context)
    assert report.verified is True


def test_file_tool_path_traversal_rejection(temp_workspace: Path) -> None:
    """Verifies that relative path traversal ('../') is strictly rejected."""
    tool = FileTool(allowed_workspace_root=str(temp_workspace))

    # Path attempting to escape via parent directory
    with pytest.raises(ValueError) as exc_info:
        tool.resolve_safe_path("../outside.txt")
    assert "Path safety violation" in str(exc_info.value)

    # Double traversal
    with pytest.raises(ValueError) as exc_info:
        tool.resolve_safe_path("nested_dir/../../secret.env")
    assert "Path safety violation" in str(exc_info.value)


def test_file_tool_outside_root_absolute_path_rejection(temp_workspace: Path) -> None:
    """Verifies that absolute paths pointing outside the allowed root are rejected."""
    tool = FileTool(allowed_workspace_root=str(temp_workspace))

    # C:\Windows or C:\ outside the temporary root
    with pytest.raises(ValueError) as exc_info:
        tool.resolve_safe_path("C:\\Windows\\System32")
    assert "Path safety violation" in str(exc_info.value)


@pytest.mark.asyncio
async def test_file_tool_traversal_in_execution(temp_workspace: Path) -> None:
    """Verifies that execute() returns a safe failure ToolResult on path traversal."""
    tool = FileTool(allowed_workspace_root=str(temp_workspace))
    context = ExecutionContext(task_id="task_ft_4")

    params = FileActionParameters(action="inspect_path", path="../escaped.txt")
    result = await tool.execute(params, context)

    assert result.success is False
    assert "Path safety violation" in result.error


@pytest.mark.asyncio
async def test_file_tool_in_orchestrator(temp_workspace: Path) -> None:
    """Verifies FileTool integrated into AgentOrchestrator."""
    tool = FileTool(allowed_workspace_root=str(temp_workspace))
    registry = ToolRegistry()
    registry.register(tool)

    mock_call = ToolCall(tool_name="file", parameters={"action": "inspect_path", "path": "sample.txt"})
    llm = MockLLMProvider(default_tool_call=mock_call)

    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=registry)
    response = await orchestrator.run("Inspect sample.txt")

    assert response.status == StatusEventType.COMPLETED
    assert response.tool_result is not None
    assert response.tool_result.output["name"] == "sample.txt"
    assert response.verification is not None
    assert response.verification.verified is True
