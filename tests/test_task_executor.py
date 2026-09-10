"""
Unit and integration tests for ORBIS Phase 6 Multi-Step Task Executor.
Tests multi-step execution, dynamic context passing, step verification,
failure cascading / blocking, execution limits, approval gates, and orchestrator integration.
"""

from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional, Type
import pytest

from core.agent.executor.executor import TaskExecutor
from core.agent.llm.mock import MockLLMProvider
from core.agent.models import (
    AgentRequest,
    TaskPlan,
    TaskState,
    TaskStep,
    TaskType,
)
from core.agent.orchestrator import AgentOrchestrator
from core.agent.planner.hierarchical import HierarchicalPlanner
from core.tools.base import BaseTool
from core.tools.continuity_tool import ContinuityTool
from core.tools.echo_tool import EchoTool
from core.tools.file_tool import FileActionParameters, FileTool
from core.tools.registry import ToolRegistry
from core.types import (
    ExecutionContext,
    RiskLevel,
    StatusEventType,
    ToolCall,
    ToolResult,
    VerificationReport,
)
from pydantic import BaseModel, Field


# ------------------------------------------------------------------------------
# Test Doubles for Testing Verification Failures and Approval Gates
# ------------------------------------------------------------------------------

class EmptyParameters(BaseModel):
    """Empty parameter model for test double tools."""
    pass


class FailingVerifyTool(BaseTool):
    name: str = "failing_verify_tool"
    description: str = "Tool that succeeds in execution but fails verification."
    risk_level: RiskLevel = RiskLevel.LOW
    parameters_schema: Type[BaseModel] = EmptyParameters

    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        return ToolResult(success=True, output="execution succeeded")

    async def verify(self, params: BaseModel, result: ToolResult, context: ExecutionContext) -> VerificationReport:
        return VerificationReport(verified=False, details="Intentional verification rejection")


class HighRiskTool(BaseTool):
    name: str = "high_risk_tool"
    description: str = "Destructive tool requiring explicit user approval."
    risk_level: RiskLevel = RiskLevel.HIGH
    parameters_schema: Type[BaseModel] = EmptyParameters

    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        return ToolResult(success=True, output="high-risk operation completed")

    async def verify(self, params: BaseModel, result: ToolResult, context: ExecutionContext) -> VerificationReport:
        return VerificationReport(verified=True, details="Verified high-risk action")


@pytest.fixture
def workspace_root():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        readme = root / "README.md"
        readme.write_text("# Project ORBIS\nAutonomous Computer Assistant.")
        yield root


@pytest.fixture
def executor_registry(workspace_root: Path) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(EchoTool())
    reg.register(FileTool(allowed_workspace_root=str(workspace_root)))
    reg.register(FailingVerifyTool())
    reg.register(HighRiskTool())
    return reg


@pytest.mark.asyncio
async def test_two_step_sequential_execution(executor_registry: ToolRegistry):
    """Verifies that step 1 executes and completes before dependent step 2 executes."""
    step_1 = TaskStep(
        step_id="s1",
        title="Step 1",
        tool_name="echo",
        parameters={"message": "First output"},
        state=TaskState.READY,
    )
    step_2 = TaskStep(
        step_id="s2",
        title="Step 2",
        tool_name="echo",
        parameters={"message": "Second output"},
        dependencies=["s1"],
        state=TaskState.PENDING,
    )

    plan = TaskPlan(
        request_id="req_1",
        user_prompt="Two steps",
        steps={"s1": step_1, "s2": step_2},
    )

    executor = TaskExecutor(tool_registry=executor_registry)
    result = await executor.execute(plan)

    assert result.status == TaskState.COMPLETED
    assert result.completed_steps == ["s1", "s2"]
    assert result.step_results["s1"].success is True
    assert result.step_results["s2"].success is True
    assert result.verification_reports["s1"].verified is True
    assert result.verification_reports["s2"].verified is True


@pytest.mark.asyncio
async def test_dynamic_context_passing(executor_registry: ToolRegistry, workspace_root: Path):
    """Verifies that step 2 dynamically consumes the output path from step 1."""
    step_1 = TaskStep(
        step_id="s_locate",
        title="Check file exists",
        tool_name="file",
        parameters={"action": "check_exists", "path": "README.md"},
        state=TaskState.READY,
    )
    step_2 = TaskStep(
        step_id="s_read",
        title="Read file contents",
        tool_name="file",
        parameters={"action": "read_file", "path": "will_be_overwritten"},
        dependencies=["s_locate"],
        input_bindings={"path": "s_locate.path"},
        state=TaskState.PENDING,
    )

    plan = TaskPlan(
        request_id="req_ctx",
        user_prompt="Locate and read",
        steps={"s_locate": step_1, "s_read": step_2},
    )

    executor = TaskExecutor(tool_registry=executor_registry)
    result = await executor.execute(plan)

    assert result.status == TaskState.COMPLETED
    assert result.step_results["s_read"].success is True
    read_output = result.step_results["s_read"].output
    assert "Project ORBIS" in read_output["content"]


@pytest.mark.asyncio
async def test_verification_failure_blocks_downstream(executor_registry: ToolRegistry):
    """Verifies that verification failure marks step FAILED and dependent steps BLOCKED."""
    step_1 = TaskStep(
        step_id="s1_bad_ver",
        title="Failing verify step",
        tool_name="failing_verify_tool",
        state=TaskState.READY,
        max_attempts=1,
    )
    step_2 = TaskStep(
        step_id="s2_dependent",
        title="Dependent step",
        tool_name="echo",
        parameters={"message": "Should never execute"},
        dependencies=["s1_bad_ver"],
        state=TaskState.PENDING,
    )

    plan = TaskPlan(
        request_id="req_fail_ver",
        user_prompt="Fail verify",
        steps={"s1_bad_ver": step_1, "s2_dependent": step_2},
    )

    executor = TaskExecutor(tool_registry=executor_registry)
    result = await executor.execute(plan)

    assert result.status == TaskState.FAILED
    assert result.failed_step == "s1_bad_ver"
    assert result.plan.steps["s1_bad_ver"].state == TaskState.FAILED
    assert result.plan.steps["s2_dependent"].state == TaskState.BLOCKED
    assert "s2_dependent" not in result.completed_steps


@pytest.mark.asyncio
async def test_high_risk_step_requires_approval_halts(executor_registry: ToolRegistry):
    """Verifies that high-risk step halts with BLOCKED_PENDING_APPROVAL when no approval callback is provided."""
    step_high = TaskStep(
        step_id="step_destructive",
        title="Destructive action",
        tool_name="high_risk_tool",
        risk_level=RiskLevel.HIGH,
        state=TaskState.READY,
    )

    plan = TaskPlan(
        request_id="req_approval",
        user_prompt="Perform high-risk operation",
        steps={"step_destructive": step_high},
    )

    executor = TaskExecutor(tool_registry=executor_registry)
    result = await executor.execute(plan, approval_callback=None)

    assert result.status == TaskState.BLOCKED_PENDING_APPROVAL
    assert result.plan.steps["step_destructive"].state == TaskState.BLOCKED_PENDING_APPROVAL


@pytest.mark.asyncio
async def test_high_risk_step_approval_granted(executor_registry: ToolRegistry):
    """Verifies that high-risk step executes successfully when approval callback returns True."""
    step_high = TaskStep(
        step_id="step_destructive",
        title="Destructive action",
        tool_name="high_risk_tool",
        risk_level=RiskLevel.HIGH,
        state=TaskState.READY,
    )

    plan = TaskPlan(
        request_id="req_approved",
        user_prompt="Perform high-risk operation with approval",
        steps={"step_destructive": step_high},
    )

    async def approve_all(step: TaskStep) -> bool:
        return True

    executor = TaskExecutor(tool_registry=executor_registry)
    result = await executor.execute(plan, approval_callback=approve_all)

    assert result.status == TaskState.COMPLETED
    assert result.plan.steps["step_destructive"].state == TaskState.COMPLETED
    assert result.step_results["step_destructive"].success is True


@pytest.mark.asyncio
async def test_maximum_steps_limit_enforced(executor_registry: ToolRegistry):
    """Verifies that task halts when max_steps bound is reached."""
    # Create 5 steps in linear dependency chain, but limit max_steps to 2
    steps = {}
    prev_id = None
    for i in range(1, 6):
        sid = f"s{i}"
        steps[sid] = TaskStep(
            step_id=sid,
            title=f"Step {i}",
            tool_name="echo",
            parameters={"message": f"msg {i}"},
            dependencies=[prev_id] if prev_id else [],
            state=TaskState.READY if not prev_id else TaskState.PENDING,
        )
        prev_id = sid

    plan = TaskPlan(
        request_id="req_limit",
        user_prompt="Too many steps",
        max_steps=10,
        steps=steps,
    )

    executor = TaskExecutor(tool_registry=executor_registry, max_steps=2)
    result = await executor.execute(plan)

    assert result.status == TaskState.FAILED
    assert len(result.completed_steps) <= 2
    assert "exceeded maximum allowed steps" in (result.error or "")


@pytest.mark.asyncio
async def test_orchestrator_run_task_integration(executor_registry: ToolRegistry, workspace_root: Path):
    """Verifies end-to-end multi-step planning and execution via AgentOrchestrator.run_task."""
    planner = HierarchicalPlanner()
    executor = TaskExecutor(tool_registry=executor_registry, planner=planner)

    orchestrator = AgentOrchestrator(
        llm_provider=MockLLMProvider(),
        tool_registry=executor_registry,
        planner=planner,
        executor=executor,
    )

    result = await orchestrator.run_task("Find the ORBIS README and summarize it")

    assert result.status == TaskState.COMPLETED
    assert "step_locate" in result.completed_steps
    assert "step_read" in result.completed_steps
    assert "step_summarize" in result.completed_steps
    assert "Multi-step task completed successfully" in result.summary


@pytest.mark.asyncio
async def test_single_step_backward_compatibility(executor_registry: ToolRegistry):
    """Verifies that Phase 1 orchestrator.run() continues to work 100% identically."""
    mock_llm = MockLLMProvider(
        default_tool_call=ToolCall(tool_name="echo", parameters={"message": "Hello Phase 1"})
    )
    orchestrator = AgentOrchestrator(
        llm_provider=mock_llm,
        tool_registry=executor_registry,
    )

    response = await orchestrator.run("echo test")
    assert response.status == StatusEventType.COMPLETED
    assert response.tool_result.success is True
    assert response.tool_result.output == "Hello Phase 1"


@pytest.mark.asyncio
async def test_authoritative_tool_risk_enforcement_prevents_bypass(executor_registry: ToolRegistry):
    """
    Regression test for Fix 1:
    Verifies that a step referencing a HIGH-risk tool cannot bypass the approval gate,
    even if TaskStep.risk_level is deliberately set to LOW and requires_approval is False.
    """
    step_spoofed = TaskStep(
        step_id="step_spoofed",
        title="Spoofed low risk step",
        tool_name="high_risk_tool",
        risk_level=RiskLevel.LOW,       # Deliberately spoofed as LOW!
        requires_approval=False,        # Deliberately False!
        state=TaskState.READY,
    )

    plan = TaskPlan(
        request_id="req_spoof",
        user_prompt="Attempt unauthorized high risk execution",
        steps={"step_spoofed": step_spoofed},
    )

    # 1. Execute without approval callback -> MUST NOT execute; state becomes BLOCKED_PENDING_APPROVAL
    executor = TaskExecutor(tool_registry=executor_registry)
    result = await executor.execute(plan, approval_callback=None)

    assert result.status == TaskState.BLOCKED_PENDING_APPROVAL
    assert result.plan.steps["step_spoofed"].state == TaskState.BLOCKED_PENDING_APPROVAL
    assert "step_spoofed" not in result.completed_steps

    # 2. Reset step to READY and execute with approval -> executes exactly once and completes
    plan.steps["step_spoofed"].state = TaskState.READY
    executed_count = 0

    async def approve_callback(s: TaskStep) -> bool:
        nonlocal executed_count
        executed_count += 1
        return True

    res_approved = await executor.execute(plan, approval_callback=approve_callback)
    assert res_approved.status == TaskState.COMPLETED
    assert res_approved.plan.steps["step_spoofed"].state == TaskState.COMPLETED
    assert "step_spoofed" in res_approved.completed_steps
    assert executed_count == 1


@pytest.mark.asyncio
async def test_complete_successful_inflight_replan(executor_registry: ToolRegistry, workspace_root: Path):
    """
    Regression test for Fix 2:
    Verifies that an in-flight replan cleanly recovers, marks the superseded step as SKIPPED,
    resets executor failure tracking, and allows the final result to reach COMPLETED.
    """
    planner = HierarchicalPlanner()
    executor = TaskExecutor(tool_registry=executor_registry, planner=planner, max_replans=2)

    # step_locate fails by attempting to escape workspace root (path="../escaped.txt")
    step_locate = TaskStep(
        step_id="step_locate",
        title="Check file exists",
        tool_name="file",
        parameters={"action": "check_exists", "path": "../escaped.txt"},
        state=TaskState.READY,
        max_attempts=1,
    )
    step_notify = TaskStep(
        step_id="step_notify",
        title="Notify completion",
        tool_name="echo",
        parameters={"message": "All steps done"},
        dependencies=["step_locate"],
        state=TaskState.PENDING,
    )

    plan = TaskPlan(
        request_id="req_replan_recovery",
        user_prompt="Recover via replan",
        steps={"step_locate": step_locate, "step_notify": step_notify},
    )

    result = await executor.execute(plan)

    # 1. Final status must be COMPLETED
    assert result.status == TaskState.COMPLETED
    # 2. Original failed step must be marked SKIPPED (superseded)
    assert result.plan.steps["step_locate"].state == TaskState.SKIPPED
    assert "Superseded" in result.plan.steps["step_locate"].description
    # 3. Replanned replacement step exists and is COMPLETED
    replan_step_ids = [s for s in result.plan.steps if s.startswith("step_replan_")]
    assert len(replan_step_ids) == 1
    new_step_id = replan_step_ids[0]
    assert result.plan.steps[new_step_id].state == TaskState.COMPLETED
    assert new_step_id in result.completed_steps
    # 4. Downstream step_notify successfully executed and completed
    assert result.plan.steps["step_notify"].state == TaskState.COMPLETED
    assert "step_notify" in result.completed_steps
    # 5. No stale failed_step in final result
    assert result.failed_step is None
    assert result.error is None


@pytest.mark.asyncio
async def test_replan_exhaustion_or_failure_remains_failed(executor_registry: ToolRegistry):
    """
    Regression test for Fix 2:
    Verifies that when replanning cannot recover (e.g. max_replans reached),
    the final task status remains FAILED and downstream steps remain BLOCKED.
    """
    planner = HierarchicalPlanner()
    # Set max_replans=0 so replanning cannot occur
    executor = TaskExecutor(tool_registry=executor_registry, planner=planner, max_replans=0)

    step_fail = TaskStep(
        step_id="step_fail",
        title="Failing step",
        tool_name="failing_verify_tool",
        state=TaskState.READY,
        max_attempts=1,
    )
    step_dep = TaskStep(
        step_id="step_dep",
        title="Dependent step",
        tool_name="echo",
        parameters={"message": "Should be blocked"},
        dependencies=["step_fail"],
        state=TaskState.PENDING,
    )

    plan = TaskPlan(
        request_id="req_exhaust",
        user_prompt="Exhaust replan",
        steps={"step_fail": step_fail, "step_dep": step_dep},
    )

    result = await executor.execute(plan)
    assert result.status == TaskState.FAILED
    assert result.failed_step == "step_fail"
    assert result.plan.steps["step_dep"].state == TaskState.BLOCKED


@pytest.mark.asyncio
async def test_async_llm_planning_inside_active_event_loop(executor_registry: ToolRegistry):
    """
    Regression test for Fix 3:
    Verifies that orchestrator.run_task() can execute an LLM planning path inside
    an active asyncio event loop without RuntimeError (asyncio.run collision),
    and validates both LLM-planned execution and deterministic non-LLM planning.
    """
    sample_plan_json = """{
        "plan_id": "plan_async_llm_1",
        "request_id": "req_async_llm_1",
        "user_prompt": "Custom LLM request",
        "task_type": "SEQUENTIAL",
        "root_step_ids": ["s1"],
        "steps": {
            "s1": {
                "step_id": "s1",
                "title": "Echo from async LLM",
                "tool_name": "echo",
                "parameters": {"message": "Async LLM planner working"},
                "dependencies": [],
                "state": "READY"
            }
        }
    }"""
    mock_llm = MockLLMProvider(default_text_response=sample_plan_json)
    planner = HierarchicalPlanner(llm_provider=mock_llm)
    executor = TaskExecutor(tool_registry=executor_registry, planner=planner)

    orchestrator = AgentOrchestrator(
        llm_provider=mock_llm,
        tool_registry=executor_registry,
        planner=planner,
        executor=executor,
    )

    # 1. Custom prompt not matching deterministic templates -> triggers async LLM planning
    result = await orchestrator.run_task("Perform novel workflow not in templates")
    assert result.status == TaskState.COMPLETED
    assert "s1" in result.completed_steps
    assert result.step_results["s1"].output == "Async LLM planner working"

    # 2. Deterministic non-LLM planning still works identically
    det_result = await orchestrator.run_task("echo deterministic test message")
    assert det_result.status == TaskState.COMPLETED
    assert "step_echo" in det_result.completed_steps
    assert "deterministic test message" in det_result.step_results["step_echo"].output
