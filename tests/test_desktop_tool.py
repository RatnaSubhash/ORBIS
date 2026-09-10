"""
Unit and integration tests for DesktopTool.
Validates read-only execution, schema validation, independent verification,
and integration with ToolRegistry and AgentOrchestrator.
"""

import pytest

from core.agent.llm.mock import MockLLMProvider
from core.agent.orchestrator import AgentOrchestrator
from core.observation.mock import MockWorkspaceObserver
from core.tools.desktop_tool import DesktopActionParameters, DesktopTool
from core.tools.registry import ToolRegistry
from core.types import ExecutionContext, RiskLevel, StatusEventType, ToolCall, ToolResult


@pytest.mark.asyncio
async def test_desktop_tool_get_active_window() -> None:
    """Verifies getting active window metadata through DesktopTool."""
    observer = MockWorkspaceObserver()
    observer.set_active_window(
        title="Visual Studio Code",
        process_name="Code.exe",
        process_id=2024,
    )

    tool = DesktopTool(observer=observer)
    context = ExecutionContext(task_id="task_dt_1")
    params = DesktopActionParameters(action="get_active_window")

    result = await tool.execute(params, context)
    assert result.success is True
    assert result.output is not None
    assert result.output["process_name"] == "Code.exe"
    assert result.output["title"] == "Visual Studio Code"

    report = await tool.verify(params, result, context)
    assert report.verified is True


@pytest.mark.asyncio
async def test_desktop_tool_capture_snapshot() -> None:
    """Verifies snapshot capture through DesktopTool."""
    observer = MockWorkspaceObserver()
    observer.set_active_window(
        title="PowerShell",
        process_name="pwsh.exe",
        process_id=3000,
    )

    tool = DesktopTool(observer=observer)
    context = ExecutionContext(task_id="task_dt_2")
    params = DesktopActionParameters(action="capture_snapshot")

    result = await tool.execute(params, context)
    assert result.success is True
    assert result.output["process_name"] == "pwsh.exe"

    report = await tool.verify(params, result, context)
    assert report.verified is True


@pytest.mark.asyncio
async def test_desktop_tool_verification_failure_on_corrupt_output() -> None:
    """Verifies that verification fails if the output dictionary misses essential keys."""
    tool = DesktopTool(observer=MockWorkspaceObserver())
    context = ExecutionContext(task_id="task_dt_3")
    params = DesktopActionParameters(action="get_active_window")

    # Output missing 'process_name', 'title', etc.
    corrupt_result = ToolResult(success=True, output={"some_key": 123})
    report = await tool.verify(params, corrupt_result, context)
    assert report.verified is False
    assert len(report.discrepancies) > 0


@pytest.mark.asyncio
async def test_desktop_tool_in_orchestrator() -> None:
    """Verifies that DesktopTool integrates seamlessly into AgentOrchestrator."""
    observer = MockWorkspaceObserver()
    observer.set_active_window(title="Browser", process_name="chrome.exe", process_id=500)

    registry = ToolRegistry()
    registry.register(DesktopTool(observer=observer))

    mock_call = ToolCall(tool_name="desktop", parameters={"action": "get_active_window"})
    llm = MockLLMProvider(default_tool_call=mock_call)

    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=registry)
    response = await orchestrator.run("What window is active?")

    assert response.status == StatusEventType.COMPLETED
    assert response.tool_result is not None
    assert response.tool_result.output["process_name"] == "chrome.exe"
    assert response.verification is not None
    assert response.verification.verified is True
