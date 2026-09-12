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


@pytest.mark.asyncio
async def test_markdown_fenced_llm_plan_response(test_registry: ToolRegistry):
    """Verifies that markdown code fenced JSON (```json ... ```) is parsed correctly."""
    fenced_plan = """```json
    {
        "plan_id": "plan_fenced_1",
        "request_id": "req_fenced",
        "user_prompt": "Custom request",
        "task_type": "sequential",
        "root_step_ids": ["s1"],
        "steps": {
            "s1": {
                "step_id": "s1",
                "title": "Fenced Echo",
                "tool_name": "echo",
                "parameters": {"message": "hello from fenced JSON"},
                "dependencies": [],
                "state": "READY"
            }
        }
    }
    ```"""
    mock_llm = MockLLMProvider(default_text_response=fenced_plan)
    planner = HierarchicalPlanner(llm_provider=mock_llm)

    request = AgentRequest(user_prompt="Custom unmatched request")
    plan = await planner.plan(request, test_registry)

    assert plan.plan_id == "plan_fenced_1"
    assert "s1" in plan.steps
    assert plan.steps["s1"].parameters["message"] == "hello from fenced JSON"


@pytest.mark.asyncio
async def test_preamble_and_conversational_llm_plan_response(test_registry: ToolRegistry):
    """Verifies that conversational preamble and postscript surrounding JSON are handled cleanly."""
    conversational_plan = """Here is your plan in JSON format:

    ```json
    {
        "plan_id": "plan_preamble_1",
        "request_id": "req_preamble",
        "user_prompt": "Custom request",
        "task_type": "sequential",
        "root_step_ids": ["s1"],
        "steps": {
            "s1": {
                "step_id": "s1",
                "title": "Preamble Echo",
                "tool_name": "echo",
                "parameters": {"message": "hello despite preamble"},
                "dependencies": [],
                "state": "READY"
            }
        }
    }
    ```

    I hope this plan helps you complete your task successfully!"""
    mock_llm = MockLLMProvider(default_text_response=conversational_plan)
    planner = HierarchicalPlanner(llm_provider=mock_llm)

    request = AgentRequest(user_prompt="Custom unmatched request")
    plan = await planner.plan(request, test_registry)

    assert plan.plan_id == "plan_preamble_1"
    assert "s1" in plan.steps
    assert plan.steps["s1"].parameters["message"] == "hello despite preamble"


@pytest.mark.asyncio
async def test_valid_json_but_invalid_taskplan_fallback(test_registry: ToolRegistry):
    """Verifies that valid JSON not conforming to TaskPlan schema falls back safely."""
    invalid_schema_json = '{"name": "Not a task plan", "status": 200, "data": [1, 2, 3]}'
    mock_llm = MockLLMProvider(default_text_response=invalid_schema_json)
    planner = HierarchicalPlanner(llm_provider=mock_llm)

    request = AgentRequest(user_prompt="Custom unmatched request")
    plan = await planner.plan(request, test_registry)

    # Must fall back safely to deterministic plan
    assert plan is not None
    assert "step_default" in plan.steps


@pytest.mark.asyncio
async def test_planvalidator_unregistered_tool_rejection(test_registry: ToolRegistry):
    """Verifies that a plan specifying an unregistered tool is rejected by PlanValidator and falls back safely."""
    unregistered_tool_plan = """{
        "plan_id": "plan_bad_tool",
        "request_id": "req_bad",
        "user_prompt": "Run malicious tool",
        "task_type": "sequential",
        "root_step_ids": ["s1"],
        "steps": {
            "s1": {
                "step_id": "s1",
                "title": "Dangerous step",
                "tool_name": "unregistered_dangerous_tool",
                "parameters": {},
                "dependencies": [],
                "state": "READY"
            }
        }
    }"""
    mock_llm = MockLLMProvider(default_text_response=unregistered_tool_plan)
    planner = HierarchicalPlanner(llm_provider=mock_llm)

    request = AgentRequest(user_prompt="Custom unmatched request")
    plan = await planner.plan(request, test_registry)

    # Must fall back safely to deterministic plan
    assert plan is not None
    assert "step_default" in plan.steps


def test_extract_json_object_helper():
    """Unit test the extract_json_object defensive parsing helper directly."""
    from core.agent.planner.hierarchical import extract_json_object

    assert extract_json_object('{"a": 1}') == {"a": 1}
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('```\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('`{"a": 1}`') == {"a": 1}
    assert extract_json_object('Preamble text ```json\n{"a": 1}\n``` commentary') == {"a": 1}
    assert extract_json_object('Preamble {"a": 1} commentary') == {"a": 1}
    assert extract_json_object('{"a": "hello } world", "b": 2}') == {"a": "hello } world", "b": 2}
    assert extract_json_object("No json here") is None
    assert extract_json_object("{malformed json:") is None
    assert extract_json_object("[1, 2, 3]") is None
    assert extract_json_object("") is None


# ------------------------------------------------------------------------------
# Phase 11.5 Voice & Q&A Quality Hardening Tests
# ------------------------------------------------------------------------------

@pytest.fixture
def full_test_registry(test_registry: ToolRegistry) -> ToolRegistry:
    from core.tools.web_research_tool import WebResearchTool
    from core.tools.terminal_tool import TerminalTool
    from core.tools.desktop_tool import DesktopTool
    from core.tools.continuity_tool import ContinuityTool
    from core.memory.db import DatabaseManager
    from core.continuity.service import ContinuityService

    reg = ToolRegistry()
    for t in test_registry.list_tools():
        reg.register(t)
    reg.register(WebResearchTool())
    reg.register(TerminalTool())
    reg.register(DesktopTool())
    db = DatabaseManager(db_path=":memory:")
    db.initialize()
    cs = ContinuityService(db=db)
    reg.register(ContinuityTool(continuity_service=cs))
    return reg


@pytest.mark.asyncio
async def test_temporal_query_routing_to_web_research(full_test_registry: ToolRegistry):
    """
    Verifies that requests with freshness/temporal signals ('latest', 'current', 'today', 'recently', etc.)
    route deterministically to WebResearchTool rather than relying on stale/hallucinated local LLM knowledge.
    """
    planner = HierarchicalPlanner()

    prompts = [
        ("What is the latest version of Python?", "latest version of Python"),
        ("What is the current version of Docker?", "current version of Docker"),
        ("What is the news today?", "news today"),
        ("What happened recently in AI?", "recently in AI"),
        ("Tell me the current version of PostgreSQL", "current version of PostgreSQL"),
    ]

    for user_prompt, expected_substr in prompts:
        req = AgentRequest(user_prompt=user_prompt)
        plan = await planner.plan(req, full_test_registry)

        assert "step_web_research" in plan.steps, f"Failed to route '{user_prompt}' to web_research"
        step = plan.steps["step_web_research"]
        assert step.tool_name == "web_research"
        assert step.parameters["action"] == "research"
        assert expected_substr.lower() in step.parameters["query"].lower()


@pytest.mark.asyncio
async def test_implicit_current_office_holder_query_routing(full_test_registry: ToolRegistry):
    """
    Verifies that questions implicitly requesting current office holders or leadership roles
    (Chief Minister, Prime Minister, President, CEO, Governor, Mayor, etc.) are treated as
    freshness-sensitive factual queries and routed to WebResearchTool even without explicit 'current'.
    """
    planner = HierarchicalPlanner()

    prompts = [
        ("Who is the Chief Minister of Tamil Nadu?", "Chief Minister of Tamil Nadu"),
        ("Who is the Prime Minister of India?", "Prime Minister of India"),
        ("Who is the current CEO of Microsoft?", "current CEO of Microsoft"),
        ("Who is the President of India?", "President of India"),
    ]

    for user_prompt, expected_query in prompts:
        req = AgentRequest(user_prompt=user_prompt)
        plan = await planner.plan(req, full_test_registry)

        assert "step_web_research" in plan.steps, f"Failed to route office-holder question '{user_prompt}' to web_research"
        step = plan.steps["step_web_research"]
        assert step.tool_name == "web_research"
        assert step.parameters["action"] == "research"
        assert step.parameters["query"].lower() == expected_query.lower()


@pytest.mark.asyncio
async def test_temporal_query_local_system_exclusion(full_test_registry: ToolRegistry):
    """
    Verifies that temporal words ('current', 'latest') referring to local system context
    (git, terminal, desktop, windows) are NOT misrouted to web research.
    """
    planner = HierarchicalPlanner()

    # Git command with 'current' or 'status'
    req_git = AgentRequest(user_prompt="Check current git status")
    plan_git = await planner.plan(req_git, full_test_registry)
    assert "step_terminal_cmd" in plan_git.steps
    assert "step_web_research" not in plan_git.steps

    # Desktop window inspection with 'current' or 'window'
    req_desk = AgentRequest(user_prompt="Inspect current desktop windows")
    plan_desk = await planner.plan(req_desk, full_test_registry)
    assert "step_inspect_desktop" in plan_desk.steps
    assert "step_web_research" not in plan_desk.steps


@pytest.mark.asyncio
async def test_continuity_precedence_over_temporal_signals(full_test_registry: ToolRegistry):
    """
    Verifies that continuity queries ('What was I working on?', 'Where did I leave off?')
    strictly maintain top precedence and are routed to continuity rather than web research.
    """
    planner = HierarchicalPlanner()

    continuity_prompts = [
        "What was I working on?",
        "What did I work on in my previous session?",
        "Where did I leave off?",
    ]

    for p in continuity_prompts:
        req = AgentRequest(user_prompt=p)
        plan = await planner.plan(req, full_test_registry)
        # Should be an intent continuity or descriptive continuity step, never web research
        assert "step_web_research" not in plan.steps
        step_ids = list(plan.steps.keys())
        assert any("continuity" in sid for sid in step_ids)


@pytest.mark.asyncio
async def test_llm_planner_simple_question_uses_echo(test_registry: ToolRegistry):
    """
    Verifies that the LLM planner creates a single-step 'echo' plan for general Q&A
    rather than invoking heavy multi-step document generation.
    """
    direct_qa_plan = """{
        "plan_id": "plan_qa_1",
        "request_id": "req_qa",
        "user_prompt": "What is artificial intelligence?",
        "task_type": "single_step",
        "root_step_ids": ["step1"],
        "steps": {
            "step1": {
                "step_id": "step1",
                "title": "Explain AI",
                "tool_name": "echo",
                "parameters": {
                    "message": "Artificial intelligence is the simulation of human intelligence by machines."
                },
                "dependencies": [],
                "state": "READY"
            }
        }
    }"""
    mock_llm = MockLLMProvider(default_text_response=direct_qa_plan)
    planner = HierarchicalPlanner(llm_provider=mock_llm)

    req = AgentRequest(user_prompt="What is artificial intelligence?")
    plan = await planner.plan(req, test_registry)

    assert len(plan.steps) == 1
    assert "step1" in plan.steps
    assert plan.steps["step1"].tool_name == "echo"
    assert "Artificial intelligence" in plan.steps["step1"].parameters["message"]


@pytest.mark.asyncio
async def test_llm_planner_ambiguous_question_asks_clarification(test_registry: ToolRegistry):
    """
    Verifies that ambiguous requests produce a plan that asks for clarification
    rather than fabricating an answer.
    """
    clarification_plan = """{
        "plan_id": "plan_clarify_1",
        "request_id": "req_ambiguous",
        "user_prompt": "Do you know Raja version?",
        "task_type": "single_step",
        "root_step_ids": ["step1"],
        "steps": {
            "step1": {
                "step_id": "step1",
                "title": "Ask for clarification",
                "tool_name": "echo",
                "parameters": {
                    "message": "I am not sure what Raja refers to. Could you clarify which software or project you mean?"
                },
                "dependencies": [],
                "state": "READY"
            }
        }
    }"""
    mock_llm = MockLLMProvider(default_text_response=clarification_plan)
    planner = HierarchicalPlanner(llm_provider=mock_llm)

    req = AgentRequest(user_prompt="Do you know Raja version?")
    plan = await planner.plan(req, test_registry)

    assert "step1" in plan.steps
    msg = plan.steps["step1"].parameters["message"]
    assert "clarify" in msg.lower() or "not sure" in msg.lower()
    assert "1.2.3" not in msg  # No hallucinated version number


@pytest.mark.asyncio
async def test_web_research_tool_grounded_summary_and_no_results():
    """
    Verifies that WebResearchTool action='research' grounds the spoken summary
    in the actual extracted page content and provides a clear no-results message when empty.
    """
    import types
    from core.tools.web_research_tool import WebResearchTool, WebResearchParameters
    from core.tools.search_provider import MockSearchProvider, SearchResult
    from core.types import ExecutionContext
    from core.voice.service import extract_spoken_response
    from core.agent.models import TaskState

    # Case 1: Extracted content available
    def custom_fetcher(url: str):
        return 200, "Python Docs", "<html><body><p>Python 3.12 is the latest stable release featuring enhanced error messages.</p></body></html>"

    mock_provider = MockSearchProvider()
    mock_provider.set_mock_results(
        "latest python",
        [SearchResult(title="Python Org", url="https://python.org/latest", snippet="Python downloads")],
    )

    tool = WebResearchTool(search_provider=mock_provider, mock_fetcher=custom_fetcher)
    ctx = ExecutionContext()
    res = await tool.execute(WebResearchParameters(action="research", query="latest python"), ctx)

    assert res.success is True
    assert "summary" in res.output
    assert "text" in res.output
    # Must be grounded in actual extracted content, not just the snippet
    assert "enhanced error messages" in res.output["summary"]
    assert "python.org" in res.output["summary"]

    # Verify extract_spoken_response extracts this grounded summary for voice
    fake_exec_res = types.SimpleNamespace(
        plan_id="p1",
        status=TaskState.COMPLETED,
        completed_steps=["step1"],
        final_output=res.output,
        summary="Task completed successfully.",
    )
    spoken = extract_spoken_response(fake_exec_res)
    assert "enhanced error messages" in spoken
    assert "python.org" in spoken

    # Case 2: No search results found
    empty_provider = MockSearchProvider()
    empty_provider.set_mock_results("unknown query xyz", [])
    empty_tool = WebResearchTool(search_provider=empty_provider)
    res_empty = await empty_tool.execute(WebResearchParameters(action="research", query="unknown query xyz"), ctx)

    assert res_empty.success is True
    assert "No web research results found" in res_empty.output["summary"]
    fake_empty_exec = types.SimpleNamespace(
        plan_id="p2",
        status=TaskState.COMPLETED,
        completed_steps=["step1"],
        final_output=res_empty.output,
        summary="Task completed successfully.",
    )
    spoken_empty = extract_spoken_response(fake_empty_exec)
    assert "No web research results found" in spoken_empty


@pytest.mark.asyncio
async def test_ambiguous_malformed_voice_transcript_triggers_clarification(full_test_registry: ToolRegistry):
    """
    Regression Test (Criterion 6):
    Verifies that malformed or ambiguous voice transcripts such as:
    'Who is who is the current chief minister of the melodic?'
    trigger deterministic clarification rather than guessing or web research.
    """
    from core.agent.planner.hierarchical import is_ambiguous_or_malformed_query

    planner = HierarchicalPlanner()

    ambiguous_prompts = [
        "Who is who is the current chief minister of the melodic?",
        "who is who is the current chief minister of the melodic",
        "What is what is the price of the",
        "Who is the chief minister of the melodic",
        "Who is the chief minister of ",
    ]

    for p in ambiguous_prompts:
        is_ambig, clarif = is_ambiguous_or_malformed_query(p)
        assert is_ambig is True, f"Expected '{p}' to be recognized as ambiguous."
        assert clarif is not None

        req = AgentRequest(user_prompt=p)
        plan = await planner.plan(req, full_test_registry)

        # Must route to step_clarification with echo tool, NEVER to web_research
        assert "step_clarification" in plan.steps, f"Expected clarification step for '{p}'"
        assert "step_web_research" not in plan.steps, f"Must not perform web research for ambiguous query '{p}'"
        clarif_step = plan.steps["step_clarification"]
        assert clarif_step.tool_name in ("echo", "echo_tool")
        assert any(word in clarif_step.parameters.get("message", "").lower() for word in ("clarify", "incomplete", "unclear", "repeat"))


@pytest.mark.asyncio
async def test_normal_natural_language_queries_accepted(full_test_registry: ToolRegistry):
    """
    Regression Test (Criterion 7):
    Verifies that standard natural-language questions (including those with 'current',
    prices, and office-holder roles) remain fully accepted without false ambiguity rejection.
    """
    from core.agent.planner.hierarchical import is_ambiguous_or_malformed_query

    planner = HierarchicalPlanner()

    valid_prompts = [
        "What is the current price of Apple's 2TB iCloud plus storage plan?",
        "Who is the current Chief Minister of Tamil Nadu?",
        "What is the latest Python version?",
        "Who is the CEO of Microsoft?",
        "What is the current version of Docker?",
    ]

    for p in valid_prompts:
        is_ambig, clarif = is_ambiguous_or_malformed_query(p)
        assert is_ambig is False, f"Expected '{p}' to NOT be flagged as ambiguous."
        assert clarif is None

        req = AgentRequest(user_prompt=p)
        plan = await planner.plan(req, full_test_registry)

        assert "step_web_research" in plan.steps, f"Expected web research step for '{p}'"
        assert "step_clarification" not in plan.steps, f"Valid query '{p}' must not trigger clarification"