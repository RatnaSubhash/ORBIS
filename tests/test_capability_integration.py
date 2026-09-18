"""
Integration and regression test suite for ORBIS capability classification,
knowledge answering, system utility, and architectural routing generalization.

Validates that:
1. CapabilityClassifier classifies request types without entity/topic whitelists.
2. Unseen arbitrary queries correctly route to their respective capability classes.
3. EchoTool is strictly reserved for simple messages/capabilities and never echoes knowledge.
4. KnowledgeTool generates substantive answers or reports clean provider-unavailable behavior.
5. SystemUtilityTool handles time/date deterministically without web search.
6. Spoken responses extracted via extract_spoken_response() are voice-ready.
"""

import pytest
import asyncio
from typing import Dict, Any

from core.agent.planner.classifier import CapabilityClass, classify_capability
from core.tools.system_tool import SystemUtilityTool, SystemUtilityParameters
from core.tools.knowledge_tool import KnowledgeTool, KnowledgeParameters
from core.tools.registry import create_default_tool_registry
from core.agent.planner.hierarchical import HierarchicalPlanner
from core.agent.orchestrator import AgentOrchestrator, format_useful_tool_output
from core.agent.executor.executor import TaskExecutor
from core.agent.llm.mock import MockLLMProvider
from core.voice.service import extract_spoken_response
from core.types import ExecutionContext, ToolResult, VerificationReport


class TestCapabilityClassifier:
    """Tests that classification is driven by request patterns, not entity whitelists."""

    @pytest.mark.parametrize(
        "prompt,expected_class",
        [
            # System utility: local time and date
            ("What time is it now?", CapabilityClass.REAL_TIME_UTILITY),
            ("What time is it in London?", CapabilityClass.REAL_TIME_UTILITY),
            ("What is today's date?", CapabilityClass.REAL_TIME_UTILITY),
            ("Tell me the current date and time", CapabilityClass.REAL_TIME_UTILITY),
            # Unseen time/date queries
            ("What is the current time in Tokyo right now?", CapabilityClass.REAL_TIME_UTILITY),
            ("Which day is today?", CapabilityClass.REAL_TIME_UTILITY),

            # Capabilities overview
            ("What do you do?", CapabilityClass.ABOUT_ORBIS_CAPABILITIES),
            ("What can you do?", CapabilityClass.ABOUT_ORBIS_CAPABILITIES),
            ("What are your capabilities?", CapabilityClass.ABOUT_ORBIS_CAPABILITIES),

            # Technical explanation
            ("Explain TCP in simple words.", CapabilityClass.TECHNICAL_EXPLANATION),
            ("How does DNS work?", CapabilityClass.TECHNICAL_EXPLANATION),
            ("What is quantum computing?", CapabilityClass.GENERAL_INFORMATION),
            # Unseen technical concepts (demonstrating no entity whitelist)
            ("Explain Byzantine fault tolerance.", CapabilityClass.TECHNICAL_EXPLANATION),
            ("What is a deterministic finite automaton?", CapabilityClass.GENERAL_INFORMATION),
            ("How does memory paging operate?", CapabilityClass.TECHNICAL_EXPLANATION),

            # Comparisons
            ("Compare TCP and UDP.", CapabilityClass.TECHNICAL_EXPLANATION),
            ("Differences between REST and GraphQL.", CapabilityClass.TECHNICAL_EXPLANATION),
            ("Compare monolith vs microservices architecture.", CapabilityClass.TECHNICAL_EXPLANATION),

            # Project assistance
            ("Can you help me with a computer networks project?", CapabilityClass.PROJECT_ASSISTANCE),
            # Unseen project domains (demonstrating no project whitelist)
            ("Can you help me build a compiler in Rust?", CapabilityClass.PROJECT_ASSISTANCE),
            ("Help me build an embedded IoT sensor system.", CapabilityClass.PROJECT_ASSISTANCE),
            ("Assist me with building an operating system", CapabilityClass.PROJECT_ASSISTANCE),

            # General information
            ("Tell me about Abdul Kalam.", CapabilityClass.GENERAL_INFORMATION),
            # Unseen entities (demonstrating no biographical whitelist)
            ("Tell me about Ada Lovelace.", CapabilityClass.GENERAL_INFORMATION),
            ("Who was Srinivasa Ramanujan?", CapabilityClass.GENERAL_INFORMATION),
            ("Give me information about the James Webb Space Telescope.", CapabilityClass.GENERAL_INFORMATION),

            # Web research (fresh/external info)
            ("Search the web for the latest Python release notes.", CapabilityClass.FRESH_INFORMATION),
            ("Find recent news about lunar exploration missions.", CapabilityClass.FRESH_INFORMATION),

            # File operations
            ("List all files in the workspace.", CapabilityClass.WORKSPACE_INSPECTION),
            ("Read the contents of config.json", CapabilityClass.FILE_READING),

            # Document generation
            ("Create a document called architecture.md with design notes.", CapabilityClass.FILE_CREATION),

            # Terminal execution
            ("Run pytest in the terminal.", CapabilityClass.TERMINAL_COMMANDS),
        ],
    )
    def test_classifier_request_types(self, prompt: str, expected_class: CapabilityClass):
        result = classify_capability(prompt)
        assert result == expected_class, f"Failed for prompt '{prompt}': expected {expected_class}, got {result}"

    def test_classifier_malformed_and_ambiguous(self):
        assert classify_capability("???") == CapabilityClass.UNKNOWN_AMBIGUOUS
        assert classify_capability("   ") == CapabilityClass.UNKNOWN_AMBIGUOUS
        assert classify_capability("who is who is who is") == CapabilityClass.UNKNOWN_AMBIGUOUS


class TestSystemUtilityTool:
    """Tests the deterministic SystemUtilityTool."""

    @pytest.mark.asyncio
    async def test_get_current_time(self):
        tool = SystemUtilityTool()
        ctx = ExecutionContext()
        params = SystemUtilityParameters(action="get_current_time", query="What time is it now?")
        
        result = await tool.execute(params, ctx)
        assert result.success is True
        assert "formatted" in result.output
        assert "time" in result.output
        assert "The current time is" in result.output["formatted"]

        # Verification
        v_report = await tool.verify(params, result, ctx)
        assert v_report.verified is True
        assert "drift" in v_report.details.lower()

    @pytest.mark.asyncio
    async def test_get_current_date(self):
        tool = SystemUtilityTool()
        ctx = ExecutionContext()
        params = SystemUtilityParameters(action="get_current_date", query="What is today's date?")
        
        result = await tool.execute(params, ctx)
        assert result.success is True
        assert "date" in result.output
        assert "Today is" in result.output["formatted"]

        v_report = await tool.verify(params, result, ctx)
        assert v_report.verified is True

    @pytest.mark.asyncio
    async def test_timezone_query(self):
        tool = SystemUtilityTool()
        ctx = ExecutionContext()
        params = SystemUtilityParameters(action="get_current_time", query="What time is it in London?", timezone="London")
        
        result = await tool.execute(params, ctx)
        assert result.success is True
        assert "London" in result.output.get("timezone", "") or "BST" in result.output.get("timezone", "") or "GMT" in result.output.get("timezone", "")


class TestKnowledgeTool:
    """Tests KnowledgeTool synthesis, verification, and provider fallback."""

    @pytest.mark.asyncio
    async def test_knowledge_offline_fallback_for_core_networking(self):
        # Even if LLM is None, core networking concepts are grounded deterministically
        tool = KnowledgeTool(llm_provider=None)
        ctx = ExecutionContext()
        params = KnowledgeParameters(query="What is TCP?", mode="explanation")

        result = await tool.execute(params, ctx)
        assert result.success is True
        assert "TCP" in result.output["answer"]
        assert "Transmission Control Protocol" in result.output["answer"]

        v_report = await tool.verify(params, result, ctx)
        assert v_report.verified is True

    @pytest.mark.asyncio
    async def test_knowledge_offline_comparison(self):
        tool = KnowledgeTool(llm_provider=None)
        ctx = ExecutionContext()
        params = KnowledgeParameters(query="Compare TCP and UDP", mode="comparison")

        result = await tool.execute(params, ctx)
        assert result.success is True
        answer = result.output["answer"]
        assert "TCP" in answer and "UDP" in answer

        v_report = await tool.verify(params, result, ctx)
        assert v_report.verified is True

    @pytest.mark.asyncio
    async def test_knowledge_project_assistance(self):
        tool = KnowledgeTool(llm_provider=None)
        ctx = ExecutionContext()
        params = KnowledgeParameters(query="computer networks project", mode="project_assistance")

        result = await tool.execute(params, ctx)
        assert result.success is True
        answer = result.output["answer"]
        assert "help you with your" in answer.lower() and "project" in answer.lower()

        v_report = await tool.verify(params, result, ctx)
        assert v_report.verified is True

    @pytest.mark.asyncio
    async def test_knowledge_clean_provider_unavailable(self):
        tool = KnowledgeTool(llm_provider=None)
        ctx = ExecutionContext()
        # Unseen topic with no offline knowledge and no LLM
        params = KnowledgeParameters(query="Explain quantum chromodynamics", mode="explanation")

        result = await tool.execute(params, ctx)
        assert result.success is True
        answer = result.output["answer"]
        assert "language model service is currently unavailable" in answer.lower()

        v_report = await tool.verify(params, result, ctx)
        assert v_report.verified is True

    @pytest.mark.asyncio
    async def test_knowledge_verification_rejects_echo(self):
        tool = KnowledgeTool(llm_provider=None)
        ctx = ExecutionContext()
        params = KnowledgeParameters(query="What is an operating system?", mode="explanation")

        # Fake an echo output
        echo_result = ToolResult(
            success=True,
            output={"answer": "What is an operating system?", "query": "What is an operating system?"},
        )
        v_report = await tool.verify(params, echo_result, ctx)
        assert v_report.verified is False
        assert "echo" in v_report.details.lower()


class TestHierarchicalPlannerRouting:
    """Tests that the planner dispatches to genuine capabilities without prompt echoing."""

    @pytest.mark.asyncio
    async def test_planner_routes_system_utility(self):
        planner = HierarchicalPlanner()
        ctx = ExecutionContext()
        plan = await planner.plan("What time is it now?", ctx)
        
        assert len(plan.steps) == 1
        step = list(plan.steps.values())[0]
        assert step.tool_name == "system"
        assert step.parameters.get("action") == "get_current_time"

    @pytest.mark.asyncio
    async def test_planner_routes_explanation_to_knowledge(self):
        planner = HierarchicalPlanner()
        ctx = ExecutionContext()
        plan = await planner.plan("Explain TCP in simple words.", ctx)

        assert len(plan.steps) == 1
        step = list(plan.steps.values())[0]
        assert step.tool_name == "knowledge"
        assert step.parameters.get("mode") == "explanation"

    @pytest.mark.asyncio
    async def test_planner_routes_project_assistance_to_knowledge(self):
        planner = HierarchicalPlanner()
        ctx = ExecutionContext()
        plan = await planner.plan("Can you help me with a computer networks project?", ctx)

        assert len(plan.steps) == 1
        step = list(plan.steps.values())[0]
        assert step.tool_name == "knowledge"
        assert step.parameters.get("mode") == "project_assistance"

    @pytest.mark.asyncio
    async def test_planner_routes_capabilities_to_echo(self):
        planner = HierarchicalPlanner()
        ctx = ExecutionContext()
        plan = await planner.plan("What do you do?", ctx)

        assert len(plan.steps) == 1
        step = list(plan.steps.values())[0]
        assert step.tool_name == "echo"
        msg = step.parameters.get("message", "")
        assert "ORBIS" in msg and ("research" in msg.lower() or "capabilities" in msg.lower())

    @pytest.mark.asyncio
    async def test_echo_tool_never_used_for_knowledge_or_assistance(self):
        planner = HierarchicalPlanner()
        ctx = ExecutionContext()

        queries = [
            "Explain TCP",
            "What is quantum computing?",
            "Tell me about Abdul Kalam",
            "Can you help me with a computer networks project?",
            "What time is it?",
        ]
        for q in queries:
            plan = await planner.plan(q, ctx)
            for step in plan.steps.values():
                assert step.tool_name != "echo", f"EchoTool incorrectly used for query: '{q}'"


class TestSpeechAndSanitizationPipeline:
    """Tests that voice output extraction and event sanitization produce voice-ready text."""

    def test_extract_spoken_response_with_system_output(self):
        from core.agent.models import AgentExecutionResult, TaskPlan, TaskStep, TaskState

        plan = TaskPlan(
            plan_id="p1",
            request_id="req_1",
            user_prompt="What time is it now?",
            steps={
                "step_1": TaskStep(step_id="step_1", title="time", tool_name="system", description="time", parameters={})
            },
        )
        res = AgentExecutionResult(
            task_id="t1",
            request="What time is it now?",
            status=TaskState.COMPLETED,
            plan=plan,
            completed_steps=["step_1"],
            final_output={"formatted": "The current time is 10:30 PM India Standard Time.", "time": "10:30 PM"},
            summary="Multi-step task completed successfully.",
        )
        spoken = extract_spoken_response(res)
        assert spoken == "The current time is 10:30 PM India Standard Time."

    def test_extract_spoken_response_with_knowledge_output(self):
        from core.agent.models import AgentExecutionResult, TaskPlan, TaskStep, TaskState

        plan = TaskPlan(
            plan_id="p1",
            request_id="req_2",
            user_prompt="Explain TCP",
            steps={
                "step_1": TaskStep(step_id="step_1", title="knowledge", tool_name="knowledge", description="knowledge", parameters={})
            },
        )
        res = AgentExecutionResult(
            task_id="t1",
            request="Explain TCP",
            status=TaskState.COMPLETED,
            plan=plan,
            completed_steps=["step_1"],
            final_output={"answer": "TCP is a reliable, connection-oriented transport protocol.", "query": "Explain TCP"},
            summary="Multi-step task completed successfully.",
        )
        spoken = extract_spoken_response(res)
        assert spoken == "TCP is a reliable, connection-oriented transport protocol."
