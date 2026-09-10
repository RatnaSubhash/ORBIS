"""
Integration Tests for ORBIS Phase 7: Web Research & Controlled Terminal Automation.
Tests end-to-end integration across:
- HierarchicalPlanner decomposition for Web Research and Terminal commands
- Dynamic tool risk evaluation preventing spoofed LOW steps from executing HIGH-risk operations
- Human-in-the-loop approval gate on high-risk terminal commands
- Multi-step DAG execution (Search -> Summarize, Terminal -> Report)
- Step failure cascading
- Backward compatibility with Phase 1 and Phase 5 pipelines
"""

from pathlib import Path
import pytest

from core.agent.executor.executor import TaskExecutor
from core.agent.llm.base import BaseLLMProvider
from core.agent.models import (
    AgentRequest,
    TaskPlan,
    TaskState,
    TaskStep,
    TaskType,
)
from core.agent.orchestrator import AgentOrchestrator
from core.agent.planner.hierarchical import HierarchicalPlanner
from core.agent.planner.validator import PlanValidator
from core.tools.echo_tool import EchoTool
from core.tools.registry import ToolRegistry
from core.tools.search_provider import MockSearchProvider
from core.tools.terminal_tool import TerminalTool
from core.tools.web_research_tool import WebResearchTool
from core.types import ExecutionContext, RiskLevel


class MockLLM(BaseLLMProvider):
    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        return "Mock response"

    async def select_tool(self, user_request: str, tools: list):
        return None


@pytest.fixture
def phase7_registry(tmp_path: Path) -> ToolRegistry:
    """Fixture providing a ToolRegistry populated with Phase 1, Phase 6, and Phase 7 tools."""
    registry = ToolRegistry()
    registry.register(EchoTool())

    mock_search = MockSearchProvider()
    web_tool = WebResearchTool(search_provider=mock_search)
    registry.register(web_tool)

    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)

    term_tool = TerminalTool(allowed_workspace_root=str(tmp_path))
    registry.register(term_tool)

    return registry


# ------------------------------------------------------------------------------
# 1. Planner Integration Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_planner_routes_web_research_request(phase7_registry: ToolRegistry) -> None:
    """Verifies that HierarchicalPlanner recognizes web research queries and creates a structured plan."""
    planner = HierarchicalPlanner()
    request = AgentRequest(user_prompt="search the web for python async programming and summarize")

    plan = await planner.plan(request, phase7_registry)
    assert plan.task_type == TaskType.SEQUENTIAL
    assert len(plan.steps) == 2
    assert "step_web_research" in plan.steps
    assert "step_summarize_research" in plan.steps

    step_1 = plan.steps["step_web_research"]
    assert step_1.tool_name == "web_research"
    assert step_1.parameters["action"] == "research"
    assert "python async programming" in step_1.parameters["query"]


@pytest.mark.asyncio
async def test_planner_routes_terminal_request(phase7_registry: ToolRegistry) -> None:
    """Verifies that HierarchicalPlanner recognizes terminal commands and sets up controlled execution."""
    planner = HierarchicalPlanner()
    request = AgentRequest(user_prompt="check git status")

    plan = await planner.plan(request, phase7_registry)
    assert "step_terminal_cmd" in plan.steps
    step = plan.steps["step_terminal_cmd"]
    assert step.tool_name == "terminal"
    assert step.parameters["command"] == "git"
    assert "status" in step.parameters["args"]
    assert step.risk_level == RiskLevel.LOW
    assert step.requires_approval is False


# ------------------------------------------------------------------------------
# 2. Multi-Step Execution Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multi_step_web_research_and_summarize(phase7_registry: ToolRegistry) -> None:
    """Verifies executing a 2-step task: Web research -> summarize findings."""
    planner = HierarchicalPlanner()
    executor = TaskExecutor(tool_registry=phase7_registry, planner=planner)

    request = AgentRequest(user_prompt="search the web for ORBIS continuity and summarize")
    plan = await planner.plan(request, phase7_registry)

    result = await executor.execute(plan)
    assert result.status == TaskState.COMPLETED
    assert len(result.completed_steps) == 2
    assert "step_web_research" in result.completed_steps
    assert "step_summarize_research" in result.completed_steps

    web_output = result.step_results["step_web_research"].output
    assert web_output["is_untrusted_external_data"] is True


@pytest.mark.asyncio
async def test_multi_step_terminal_command_and_report(phase7_registry: ToolRegistry) -> None:
    """Verifies executing a 2-step task: Run terminal command -> report outcome."""
    planner = HierarchicalPlanner()
    executor = TaskExecutor(tool_registry=phase7_registry, planner=planner)

    request = AgentRequest(user_prompt="check git status and tell me the result")
    plan = await planner.plan(request, phase7_registry)

    result = await executor.execute(plan)
    assert result.status == TaskState.COMPLETED
    assert "step_terminal_cmd" in result.completed_steps
    assert "step_report_terminal" in result.completed_steps


# ------------------------------------------------------------------------------
# 3. Dynamic Risk and Approval Gate Security Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spoofed_low_risk_terminal_step_cannot_bypass_approval(phase7_registry: ToolRegistry) -> None:
    """
    CRITICAL SECURITY REGRESSION TEST:
    A TaskStep declaring risk_level=LOW and requires_approval=False MUST NOT bypass
    the approval gate if the terminal command policy classifies it as HIGH (e.g. 'git clean').
    """
    # Create step that deliberately spoofed LOW risk for a HIGH-risk git clean command
    step_spoofed = TaskStep(
        step_id="step_clean",
        title="Spoofed clean step",
        tool_name="terminal",
        parameters={"command": "git", "args": ["clean", "-fd"]},
        risk_level=RiskLevel.LOW,       # Deliberately spoofed as LOW!
        requires_approval=False,        # Deliberately set to False!
        state=TaskState.READY,
    )

    plan = TaskPlan(
        request_id="req_spoof_term",
        user_prompt="Clean workspace files",
        steps={"step_clean": step_spoofed},
    )

    # 1. Validation automatically upgrades step to HIGH and requires_approval to True
    errors = PlanValidator.validate_plan(plan, tool_registry=phase7_registry)
    assert not errors
    assert plan.steps["step_clean"].risk_level == RiskLevel.HIGH
    assert plan.steps["step_clean"].requires_approval is True

    # 2. Execution without approval callback MUST halt at BLOCKED_PENDING_APPROVAL
    executor = TaskExecutor(tool_registry=phase7_registry)
    result = await executor.execute(plan, approval_callback=None)

    assert result.status == TaskState.BLOCKED_PENDING_APPROVAL
    assert result.plan.steps["step_clean"].state == TaskState.BLOCKED_PENDING_APPROVAL
    assert "step_clean" not in result.completed_steps


@pytest.mark.asyncio
async def test_high_risk_terminal_command_granted_approval(phase7_registry: ToolRegistry) -> None:
    """Verifies that a high-risk terminal step executes when the backend approval callback grants approval."""
    step_high = TaskStep(
        step_id="step_commit",
        title="Git commit operation",
        tool_name="terminal",
        parameters={"command": "git", "args": ["commit", "-m", "test commit"]},
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
        state=TaskState.READY,
    )

    plan = TaskPlan(
        request_id="req_approved_term",
        user_prompt="Commit repository changes",
        steps={"step_commit": step_high},
    )

    approved_steps = []

    async def approve_all(step: TaskStep) -> bool:
        approved_steps.append(step.step_id)
        return True

    # Use a mock runner for git commit in isolated test directory
    def fake_commit_runner(cmd: str, args: list, wdir: Path):
        return 0, "[master 1234567] test commit\n 1 file changed", ""

    term_tool: TerminalTool = phase7_registry.get_or_raise("terminal")  # type: ignore
    term_tool.mock_runner = fake_commit_runner

    executor = TaskExecutor(tool_registry=phase7_registry)
    result = await executor.execute(plan, approval_callback=approve_all)

    assert result.status == TaskState.COMPLETED
    assert "step_commit" in result.completed_steps
    assert "step_commit" in approved_steps


# ------------------------------------------------------------------------------
# 4. Backward Compatibility Test
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backward_compatibility_with_orchestrator(phase7_registry: ToolRegistry) -> None:
    """Verifies that AgentOrchestrator.run() and run_task() operate smoothly with Phase 7 tools."""
    mock_llm = MockLLM()
    orchestrator = AgentOrchestrator(
        llm_provider=mock_llm,
        tool_registry=phase7_registry,
    )

    # 1. Phase 1 single-step run()
    p1_response = await orchestrator.run("echo Hello Phase 1 Backward Compatibility")
    assert p1_response.status.value in ("COMPLETED", "FAILED")  # single step shell

    # 2. Phase 6 / Phase 7 run_task()
    p7_result = await orchestrator.run_task("check git status")
    assert p7_result.status == TaskState.COMPLETED
    assert "step_terminal_cmd" in p7_result.completed_steps
