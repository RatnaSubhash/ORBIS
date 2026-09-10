"""
Unit tests for ORBIS Phase 6 Hierarchical Task Planner and Validator.
Tests deterministic planning, DAG dependency validation, circular dependency detection,
missing tool handling, parent-child task hierarchy, and controlled replanning boundaries.
"""

from typing import Dict, List
import pytest

from core.agent.llm.mock import MockLLMProvider
from core.agent.models import (
    AgentRequest,
    TaskPlan,
    TaskState,
    TaskStep,
    TaskType,
)
from core.agent.planner.hierarchical import HierarchicalPlanner
from core.agent.planner.validator import PlanValidator
from core.tools.echo_tool import EchoTool
from core.tools.file_tool import FileTool
from core.tools.registry import ToolRegistry
from core.types import RiskLevel


@pytest.fixture
def test_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(EchoTool())
    reg.register(FileTool())
    return reg


def test_validator_detects_empty_plan():
    """Verifies PlanValidator rejects plans with no steps."""
    plan = TaskPlan(request_id="req_1", user_prompt="test", steps={})
    errors = PlanValidator.validate_plan(plan)
    assert any("contains no steps" in e for e in errors)


def test_validator_detects_step_count_limit():
    """Verifies PlanValidator rejects plans exceeding max_steps."""
    steps = {
        f"s_{i}": TaskStep(step_id=f"s_{i}", title=f"S{i}", tool_name="echo")
        for i in range(5)
    }
    plan = TaskPlan(request_id="req_1", user_prompt="test", max_steps=3, steps=steps)
    errors = PlanValidator.validate_plan(plan)
    assert any("exceeds maximum allowed steps" in e for e in errors)


def test_validator_detects_missing_dependency():
    """Verifies PlanValidator detects dependencies referencing non-existent step IDs."""
    step_1 = TaskStep(
        step_id="step_1",
        title="Step 1",
        tool_name="echo",
        dependencies=["step_non_existent"],
    )
    plan = TaskPlan(request_id="req_1", user_prompt="test", steps={"step_1": step_1})
    errors = PlanValidator.validate_plan(plan)
    assert any("depends on non-existent step 'step_non_existent'" in e for e in errors)


def test_validator_detects_circular_dependency():
    """Verifies PlanValidator detects circular dependency chains (A -> B -> A)."""
    step_a = TaskStep(
        step_id="step_a",
        title="Step A",
        tool_name="echo",
        dependencies=["step_b"],
    )
    step_b = TaskStep(
        step_id="step_b",
        title="Step B",
        tool_name="echo",
        dependencies=["step_a"],
    )
    plan = TaskPlan(
        request_id="req_1",
        user_prompt="test",
        steps={"step_a": step_a, "step_b": step_b},
    )
    errors = PlanValidator.validate_plan(plan)
    assert any("Circular dependency detected" in e for e in errors)


def test_validator_detects_unregistered_tool(test_registry: ToolRegistry):
    """Verifies PlanValidator rejects steps referencing tools missing from ToolRegistry."""
    step = TaskStep(step_id="step_1", title="Step 1", tool_name="unregistered_tool")
    plan = TaskPlan(request_id="req_1", user_prompt="test", steps={"step_1": step})
    errors = PlanValidator.validate_plan(plan, tool_registry=test_registry)
    assert any("references unregistered tool 'unregistered_tool'" in e for e in errors)


def test_validator_detects_undeclared_dependency_in_binding():
    """Verifies PlanValidator rejects input bindings from steps not declared as dependencies."""
    step_1 = TaskStep(step_id="step_1", title="S1", tool_name="echo")
    step_2 = TaskStep(
        step_id="step_2",
        title="S2",
        tool_name="echo",
        dependencies=[],  # Forgot to declare step_1 as dependency!
        input_bindings={"message": "step_1.output"},
    )
    plan = TaskPlan(
        request_id="req_1", user_prompt="test", steps={"step_1": step_1, "step_2": step_2}
    )
    errors = PlanValidator.validate_plan(plan)
    assert any("consumes output from 'step_1' without declaring it as a dependency" in e for e in errors)


@pytest.mark.asyncio
async def test_deterministic_file_summary_hierarchy(test_registry: ToolRegistry):
    """Verifies deterministic generation of hierarchical file locate + read + summarize plan."""
    planner = HierarchicalPlanner()
    request = AgentRequest(user_prompt="Find the ORBIS README and summarize it")
    plan = await planner.plan(request, test_registry)

    assert plan.task_type == TaskType.HIERARCHICAL
    assert "task_file_summary" in plan.root_step_ids
    assert len(plan.steps) == 4

    # Verify parent-child relationships
    parent = plan.steps["task_file_summary"]
    step_locate = plan.steps["step_locate"]
    step_read = plan.steps["step_read"]
    step_summarize = plan.steps["step_summarize"]

    assert step_locate.parent_step_id == parent.step_id
    assert step_read.parent_step_id == parent.step_id
    assert step_summarize.parent_step_id == parent.step_id

    # Verify dependency chain: locate -> read -> summarize
    assert step_locate.dependencies == [parent.step_id]
    assert step_read.dependencies == [step_locate.step_id]
    assert step_summarize.dependencies == [step_read.step_id]

    # Verify input bindings
    assert step_read.input_bindings == {"path": "step_locate.path"}
    assert step_summarize.input_bindings == {"message": "step_read.content"}


@pytest.mark.asyncio
async def test_controlled_replan_within_bounds(test_registry: ToolRegistry):
    """Verifies that controlled replanning generates a safe alternative step without risk escalation."""
    planner = HierarchicalPlanner()
    request = AgentRequest(user_prompt="Find the ORBIS README and summarize it")
    plan = await planner.plan(request, test_registry)

    # Simulate step_locate failure
    replanned_plan = await planner.replan(
        plan=plan,
        failed_step_id="step_locate",
        error="Target file does not exist",
        tool_registry=test_registry,
    )

    assert replanned_plan is not None
    assert replanned_plan.replan_count == 1
    # Check that original step is marked SKIPPED (Fix 2)
    assert replanned_plan.steps["step_locate"].state == TaskState.SKIPPED
    # Check that replacement step exists and uses a registered tool
    new_step_id = [sid for sid in replanned_plan.steps if sid.startswith("step_replan_")][0]
    new_step = replanned_plan.steps[new_step_id]
    assert new_step.tool_name == "file"
    assert new_step.parameters.get("action") == "list_directory"
    assert new_step.risk_level == RiskLevel.LOW

    # Check that step_read now depends on the replanned step
    assert new_step_id in replanned_plan.steps["step_read"].dependencies


@pytest.mark.asyncio
async def test_replan_limit_enforced(test_registry: ToolRegistry):
    """Verifies that replanning stops when max_replans limit is reached."""
    planner = HierarchicalPlanner()
    request = AgentRequest(user_prompt="Find the ORBIS README and summarize it")
    plan = await planner.plan(request, test_registry)
    plan.replan_count = plan.max_replans  # Exhaust limit

    replanned = await planner.replan(
        plan=plan,
        failed_step_id="step_locate",
        error="Error",
        tool_registry=test_registry,
    )
    assert replanned is None


@pytest.mark.asyncio
async def test_llm_planner_with_mock_provider(test_registry: ToolRegistry):
    """Verifies optional LLM plan generation using deterministic MockLLMProvider."""
    # Create valid JSON representation of a plan
    sample_plan_json = """{
        "plan_id": "plan_llm_1",
        "request_id": "req_llm_1",
        "user_prompt": "Custom user request",
        "task_type": "SEQUENTIAL",
        "root_step_ids": ["s1"],
        "steps": {
            "s1": {
                "step_id": "s1",
                "title": "Echo hello",
                "tool_name": "echo",
                "parameters": {"message": "hello from LLM"},
                "dependencies": [],
                "state": "READY"
            }
        }
    }"""
    mock_llm = MockLLMProvider(default_text_response=sample_plan_json)
    planner = HierarchicalPlanner(llm_provider=mock_llm)

    request = AgentRequest(user_prompt="Custom unmatched request")
    plan = await planner.plan(request, test_registry)

    assert plan.plan_id == "plan_llm_1"
    assert "s1" in plan.steps
    assert plan.steps["s1"].parameters["message"] == "hello from LLM"


@pytest.mark.asyncio
async def test_malformed_llm_plan_rejection(test_registry: ToolRegistry):
    """Verifies that invalid or malformed LLM responses are rejected and fall back safely."""
    # LLM returns invalid JSON
    mock_llm = MockLLMProvider(default_text_response="Invalid Non-JSON response")
    planner = HierarchicalPlanner(llm_provider=mock_llm)

    request = AgentRequest(user_prompt="Custom unmatched request")
    plan = await planner.plan(request, test_registry)

    # Should fall back to safe default plan
    assert plan is not None
    assert "step_default" in plan.steps
