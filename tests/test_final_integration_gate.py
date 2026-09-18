"""
Comprehensive Final Integration Gate Test Suite for ORBIS.
Verifies all 4 Architectural Conditions and Stabilization Requirements:
1. Deterministic Knowledge Grounding (Foundational concepts preserved when LLM unavailable)
2. Project Assistance End-to-End Traces (Intent & subject extraction without conversational wrappers)
3. Genuine Real Ollama LLM Answering (pipeline verification for FSM, Ada Lovelace, sky blue, virtual memory, DNS)
4. LLM Failure Behavior (Unavailable, timeout, malformed, exception -> clean explicit failure, never fake success)
5. Capability Router Generalization (Unseen queries across all capability classes)
6. Single Authoritative Routing Path & Parity (Voice and typed parity, single planner entry)
7. Full Registered Tool Reachability Matrix (All 11+ registered tools)
8. Answer Pipeline Speech Invariant (TaskResult -> extract_spoken_response -> EventSanitizer)
9. Security Regression (Approval gates, workspace boundary, command allowlist)
"""

import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.agent.orchestrator import AgentOrchestrator
from core.agent.planner.classifier import (
    CapabilityClass,
    classify_capability,
    extract_project_assistance_metadata,
    is_ambiguous_or_malformed,
)
from core.agent.planner.hierarchical import HierarchicalPlanner
from core.agent.models import AgentRequest, AgentExecutionResult, TaskPlan, TaskStep, TaskState
from core.voice.service import extract_spoken_response
from core.events.sanitizer import EventSanitizer
from core.tools.base import ExecutionContext, ToolResult
from core.tools.echo_tool import EchoTool
from core.tools.file_tool import FileTool
from core.tools.knowledge_tool import KnowledgeTool, KnowledgeParameters, CORE_OFFLINE_KNOWLEDGE
from core.tools.registry import create_default_tool_registry
from core.tools.system_tool import SystemUtilityTool, SystemUtilityParameters
from core.tools.terminal_tool import TerminalTool, TerminalActionParameters
from core.types import RiskLevel


# ==============================================================================
# CONDITION 1: DETERMINISTIC KNOWLEDGE GROUNDING
# ==============================================================================

class TestDeterministicKnowledgeGrounding:
    """Verifies that foundational concepts are answered safely without LLM dependence."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "query,expected_substrings",
        [
            ("What is TCP?", ["TCP", "Transmission Control Protocol"]),
            ("What is UDP?", ["UDP", "User Datagram Protocol"]),
            ("Compare TCP and UDP.", ["TCP", "UDP"]),
            ("What is DNS?", ["DNS", "Domain Name System"]),
            ("What is a computer network?", ["computer network", "connected devices"]),
        ],
    )
    async def test_foundational_grounding_offline(self, query: str, expected_substrings: list):
        """KnowledgeTool must answer foundational concepts deterministically when LLM is unavailable."""
        tool = KnowledgeTool(llm_provider=None)
        ctx = ExecutionContext()
        mode = "comparison" if "compare" in query.lower() else "explanation"
        params = KnowledgeParameters(query=query, mode=mode)

        result = await tool.execute(params, ctx)
        assert result.success is True, f"Execution failed for {query}"
        assert result.output["source"] == "offline_core"
        answer = result.output["answer"]
        for sub in expected_substrings:
            assert sub.lower() in answer.lower(), f"Expected '{sub}' in answer for '{query}': {answer}"

        # Verification must pass independently
        v_report = await tool.verify(params, result, ctx)
        assert v_report.verified is True

    @pytest.mark.asyncio
    async def test_planner_has_no_topic_whitelists(self):
        """HierarchicalPlanner must route to KnowledgeTool without hardcoded topic branches."""
        planner = HierarchicalPlanner()
        registry = create_default_tool_registry()

        # Both foundational and unseen topics route through KnowledgeTool
        queries = [
            "What is TCP?",
            "How does DNS work?",
            "What is a finite state machine?",
            "Who was Ada Lovelace?",
            "Explain quantum computing",
        ]
        for q in queries:
            plan = await planner.plan(q, tool_registry=registry)
            step = list(plan.steps.values())[0]
            assert step.tool_name == "knowledge", f"Planner did not route '{q}' to KnowledgeTool (got {step.tool_name})"
            assert step.tool_name != "echo", f"Planner fell back to EchoTool for knowledge query: '{q}'"


# ==============================================================================
# CONDITION 2: PROJECT ASSISTANCE END-TO-END VERIFICATION
# ==============================================================================

class TestProjectAssistanceEndToEnd:
    """Verifies end-to-end: prompt -> classification -> intent/subject extraction -> KnowledgeTool -> actual response."""

    @pytest.mark.parametrize(
        "prompt,expected_intent,expected_subject",
        [
            ("Can you help me build a compiler in Rust?", "build", "compiler in Rust"),
            ("Help me design a distributed cache.", "design", "distributed cache"),
            ("Assist me with an operating systems project.", "assist", "operating systems"),
            ("Can you help me create a robotics project?", "create", "robotics"),
            ("Help me plan a database project.", "plan", "database"),
            ("Can you help me with a computer networks project?", "assist", "computer networks"),
            ("I want to implement a Raft consensus algorithm", "implement", "Raft consensus algorithm"),
            ("Help me develop a chess engine in C++", "develop", "chess engine in C++"),
        ],
    )
    def test_semantic_metadata_extraction_unseen(self, prompt: str, expected_intent: str, expected_subject: str):
        """Verifies clean extraction of user intent and project subject without conversational wrappers."""
        intent, subject = extract_project_assistance_metadata(prompt)
        assert intent == expected_intent, f"For '{prompt}': expected intent '{expected_intent}', got '{intent}'"
        assert subject.lower() == expected_subject.lower(), f"For '{prompt}': expected subject '{expected_subject}', got '{subject}'"

        # Assert no conversational wrappers leaked into subject
        for wrapper in ("can you", "help me", "assist me", "please", "could you", "i want to", "with a", "with an"):
            assert wrapper not in subject.lower(), f"Conversational wrapper '{wrapper}' leaked into subject '{subject}'"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "prompt,expected_intent,expected_subject",
        [
            ("Can you help me build a compiler in Rust?", "build", "compiler in Rust"),
            ("Help me design a distributed cache.", "design", "distributed cache"),
            ("Assist me with an operating systems project.", "assist", "operating systems"),
            ("Can you help me create a robotics project?", "create", "robotics"),
            ("Help me plan a database project.", "plan", "database"),
        ],
    )
    async def test_project_assistance_end_to_end_trace(
        self, prompt: str, expected_intent: str, expected_subject: str
    ):
        """
        Traces full pipeline:
        user prompt -> classification -> planner -> KnowledgeTool -> response verification.
        """
        # 1. Classification
        cap = classify_capability(prompt)
        assert cap == CapabilityClass.PROJECT_ASSISTANCE, f"Classification failed for '{prompt}': got {cap}"

        # 2. Planner constructs plan with clean parameters
        planner = HierarchicalPlanner()
        ctx = ExecutionContext()
        registry = create_default_tool_registry()

        plan = await planner.plan(prompt, tool_registry=registry)
        assert len(plan.steps) == 1
        step = list(plan.steps.values())[0]
        assert step.tool_name == "knowledge"
        assert step.parameters.get("mode") == "project_assistance"
        assert step.parameters.get("intent") == expected_intent
        assert step.parameters.get("subject").lower() == expected_subject.lower()

        # 3. KnowledgeTool execution produces non-echo, non-wrapper response
        tool = registry.get("knowledge")
        params = KnowledgeParameters(
            query=step.parameters["query"],
            subject=step.parameters["subject"],
            intent=step.parameters["intent"],
            mode="project_assistance",
        )
        result = await tool.execute(params, ctx)
        assert result.success is True
        answer = result.output["answer"]

        # 4. Response assertions
        assert len(answer.strip()) > 30, f"Answer suspiciously brief: {answer}"
        # Subject must be referenced cleanly
        assert expected_subject.lower() in answer.lower() or expected_intent in answer.lower()
        # Conversational wrappers MUST NOT appear in the subject or prompt echo
        assert f"project on '{prompt}'" not in answer
        assert f"project on \"{prompt}\"" not in answer

        # 5. Tool verification report must pass
        v_report = await tool.verify(params, result, ctx)
        assert v_report.verified is True


# ==============================================================================
# CONDITION 3: REAL OLLAMA ANSWERING VERIFICATION
# ==============================================================================

class TestRealOllamaAnswering:
    """
    Tests genuine real-LLM execution through the actual ORBIS pipeline.
    Uses local Ollama provider (llama3.1:8b).
    Does NOT substitute MockLLM.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "query,expected_keywords",
        [
            ("What is a finite state machine?", ["state", "machine", "fsm", "automaton", "computation"]),
            ("Who was Ada Lovelace?", ["mathematician", "programmer", "computing", "lovelace", "babbage", "algorithm", "analytical engine", "first"]),
            ("Why is the sky blue?", ["light", "rayleigh", "scatter", "atmosphere", "wavelength", "blue", "sun"]),
            ("What is virtual memory?", ["memory", "ram", "disk", "address", "virtual", "paging", "storage", "hardware", "process"]),
            ("How does DNS work?", ["domain", "ip", "address", "server", "query", "nameserver", "resolution", "translate", "dns"]),
        ],
    )
    async def test_genuine_real_ollama_pipeline(self, query: str, expected_keywords: list):
        """
        Executes genuine real-LLM pipeline with configured Ollama provider.
        Must produce a substantive, accurate answer via KnowledgeTool -> Ollama.
        """
        from core.agent.llm.factory import get_llm_provider
        provider = get_llm_provider()
        assert provider.__class__.__name__ == "OllamaProvider", f"Configured provider is not Ollama: {provider}"
        assert provider.is_available() is True, "Local Ollama service is not available on http://localhost:11434"

        # 1. Classification
        cap = classify_capability(query)
        assert cap in (CapabilityClass.GENERAL_INFORMATION, CapabilityClass.TECHNICAL_EXPLANATION)

        # 2. Planning
        planner = HierarchicalPlanner()
        ctx = ExecutionContext()
        registry = create_default_tool_registry()

        plan = await planner.plan(query, tool_registry=registry)
        step = list(plan.steps.values())[0]
        assert step.tool_name == "knowledge"

        # 3. Execution via KnowledgeTool -> Ollama
        tool = registry.get("knowledge")
        params = KnowledgeParameters(query=query, mode=step.parameters.get("mode", "factual"))
        result = await tool.execute(params, ctx)

        assert result.success is True, f"KnowledgeTool execution failed for '{query}': {result.error}"
        assert result.output.get("source") == "llm", f"Expected source 'llm' for '{query}', got '{result.output.get('source')}'"
        answer = result.output.get("answer", "")

        # 4. Content assertions: meaningful, non-empty, contains key concept
        assert len(answer.strip()) >= 20, f"Answer too brief: '{answer}'"
        assert any(k.lower() in answer.lower() for k in expected_keywords), f"None of keywords {expected_keywords} found in answer: '{answer}'"
        assert answer.strip().lower() != query.strip().lower(), "Answer is an echo of the query"

        # 5. Verification passes
        v_report = await tool.verify(params, result, ctx)
        assert v_report.verified is True


# ==============================================================================
# CONDITION 4: LLM FAILURE BEHAVIOR AUDIT
# ==============================================================================

class TestLLMFailureBehavior:
    """
    Tests failure behavior when LLM encounters:
    - Provider unavailable
    - Timeout (20s)
    - Malformed response (empty/whitespace)
    - Provider exception
    Expected: explicit failure or safe offline answer. NEVER step_default, prompt echo, or fake success.
    """

    @pytest.mark.asyncio
    async def test_ollama_unavailable_for_unseen_topic(self):
        """When LLM is unavailable and no offline grounding exists, returns explicit provider failure notice."""
        tool = KnowledgeTool(llm_provider=None)
        ctx = ExecutionContext()
        params = KnowledgeParameters(query="Explain topological quantum field theory", mode="explanation")

        result = await tool.execute(params, ctx)
        assert result.success is True
        assert result.output["source"] == "provider_unavailable"
        answer = result.output["answer"]
        assert "language model service is currently unavailable" in answer.lower()
        # Must NEVER be a prompt echo or fake success
        assert answer.strip() != params.query.strip()
        assert "step_default" not in answer

    @pytest.mark.asyncio
    async def test_ollama_timeout_behavior(self):
        """When LLM times out, returns explicit timeout failure for open-ended queries."""
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True

        async def slow_generate(*args, **kwargs):
            await asyncio.sleep(25.0)
            return "Too late"

        mock_llm.generate = AsyncMock(side_effect=slow_generate)

        tool = KnowledgeTool(llm_provider=mock_llm)
        ctx = ExecutionContext()
        params = KnowledgeParameters(query="What is the history of the Byzantine Empire?", mode="factual")

        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            result = await tool.execute(params, ctx)
            assert result.success is False
            assert "timed out" in result.error.lower() or "timeout" in result.error.lower()
            assert result.output["source"] == "llm_timeout"

    @pytest.mark.asyncio
    async def test_malformed_provider_response_rejected(self):
        """When provider returns empty or whitespace response, returns explicit failure, not fake success."""
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.generate = AsyncMock(return_value="   ")

        tool = KnowledgeTool(llm_provider=mock_llm)
        ctx = ExecutionContext()
        params = KnowledgeParameters(query="Explain string theory", mode="explanation")

        result = await tool.execute(params, ctx)
        assert result.success is False
        assert "malformed" in result.error.lower() or "empty" in result.error.lower()
        assert result.output["source"] == "malformed_provider_response"

    @pytest.mark.asyncio
    async def test_provider_exception_clean_handling(self):
        """When provider raises connection error, returns explicit failure, not fake success."""
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.generate = AsyncMock(side_effect=ConnectionRefusedError("Connection to 11434 refused"))

        tool = KnowledgeTool(llm_provider=mock_llm)
        ctx = ExecutionContext()
        params = KnowledgeParameters(query="Explain dark matter", mode="explanation")

        result = await tool.execute(params, ctx)
        assert result.success is False
        assert "connection to 11434 refused" in result.error.lower()
        assert result.output["source"] == "provider_exception"

    @pytest.mark.asyncio
    async def test_timeout_fallback_to_offline_grounding_for_foundational_concept(self):
        """If LLM times out on foundational concept (e.g. TCP), safely falls back to offline grounding."""
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True

        tool = KnowledgeTool(llm_provider=mock_llm)
        ctx = ExecutionContext()
        params = KnowledgeParameters(query="What is TCP?", mode="explanation")

        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            result = await tool.execute(params, ctx)
            assert result.success is True
            assert result.output["source"] == "offline_core"
            assert "Transmission Control Protocol" in result.output["answer"]


# ==============================================================================
# GENERALIZATION: CAPABILITY ROUTER GENERALIZATION ACROSS UNSEEN TOPICS
# ==============================================================================

class TestCapabilityRouterGeneralization:
    """Tests router generalization across unseen topics for all capability classes."""

    @pytest.mark.parametrize(
        "prompt,expected_cap",
        [
            # About ORBIS
            ("What are your capabilities?", CapabilityClass.ABOUT_ORBIS_CAPABILITIES),
            ("What can you do?", CapabilityClass.ABOUT_ORBIS_CAPABILITIES),
            ("Who are you?", CapabilityClass.ABOUT_ORBIS_CAPABILITIES),

            # Real-time utility
            ("What time is it now?", CapabilityClass.REAL_TIME_UTILITY),
            ("What is today's date?", CapabilityClass.REAL_TIME_UTILITY),
            ("What time is it in Tokyo?", CapabilityClass.REAL_TIME_UTILITY),

            # Continuity
            ("What was I working on?", CapabilityClass.WORKSPACE_CONTINUITY),
            ("Where did I leave off?", CapabilityClass.WORKSPACE_CONTINUITY),
            ("What should I do next?", CapabilityClass.INTENT_CONTINUITY),
            ("What was I trying to accomplish?", CapabilityClass.INTENT_CONTINUITY),
            ("Restore my workspace from earlier.", CapabilityClass.WORKSPACE_RESTORATION),

            # Project assistance (unseen domains)
            ("Can you help me build a compiler in Rust?", CapabilityClass.PROJECT_ASSISTANCE),
            ("Help me design an avionics control pipeline", CapabilityClass.PROJECT_ASSISTANCE),
            ("Assist me with a bioinformatics project", CapabilityClass.PROJECT_ASSISTANCE),

            # Technical explanation (unseen concepts)
            ("Explain Raft consensus protocol in simple terms.", CapabilityClass.TECHNICAL_EXPLANATION),
            ("How does branch prediction operate?", CapabilityClass.TECHNICAL_EXPLANATION),
            ("Compare optimistic vs pessimistic concurrency control.", CapabilityClass.TECHNICAL_EXPLANATION),
            ("Why is the Pacific Ocean so deep?", CapabilityClass.TECHNICAL_EXPLANATION),

            # General information (unseen entities)
            ("Who was Alan Turing?", CapabilityClass.GENERAL_INFORMATION),
            ("What is the speed of light?", CapabilityClass.GENERAL_INFORMATION),

            # Fresh web research
            ("Search the web for the latest PyTorch release notes.", CapabilityClass.FRESH_INFORMATION),
            ("Find recent articles about nuclear fusion breakthroughs.", CapabilityClass.FRESH_INFORMATION),

            # Workspace inspection & file operations
            ("List all files in the current project.", CapabilityClass.WORKSPACE_INSPECTION),
            ("Read the contents of pyproject.toml", CapabilityClass.FILE_READING),
            ("Create a markdown document called design_doc.md", CapabilityClass.FILE_CREATION),

            # Terminal execution
            ("Run pytest in the terminal.", CapabilityClass.TERMINAL_COMMANDS),

            # Desktop inspection
            ("What window is currently open on my desktop?", CapabilityClass.DESKTOP_INSPECTION),

            # Malformed / Ambiguous
            ("???", CapabilityClass.UNKNOWN_AMBIGUOUS),
            ("what is what is what is", CapabilityClass.UNKNOWN_AMBIGUOUS),
        ],
    )
    def test_router_generalization_unseen(self, prompt: str, expected_cap: CapabilityClass):
        result = classify_capability(prompt)
        assert result == expected_cap, f"Prompt '{prompt}': expected {expected_cap}, got {result}"


# ==============================================================================
# TOOL REACHABILITY MATRIX ACROSS ALL REGISTERED TOOLS
# ==============================================================================

class TestToolReachabilityMatrix:
    """Verifies that all 11 registered tools are registered, valid, and reachable via planner."""

    def test_all_11_tools_registered(self):
        registry = create_default_tool_registry()
        expected_tools = [
            "echo",
            "file",
            "desktop",
            "system",
            "knowledge",
            "continuity_tool",
            "intent_continuity",
            "restoration_tool",
            "terminal",
            "web_research",
            "content_generator",
            "document",
        ]
        registered_names = [t.name for t in registry.list_tools()]
        for tool_name in expected_tools:
            assert tool_name in registered_names, f"Tool '{tool_name}' missing from default registry"
        assert len(registered_names) >= 11

    @pytest.mark.asyncio
    async def test_tool_reachability_via_planner(self):
        """Verifies each registered capability invokes its corresponding tool."""
        planner = HierarchicalPlanner()
        registry = create_default_tool_registry()

        dispatch_matrix = [
            ("What time is it?", "system"),
            ("What is a finite state machine?", "knowledge"),
            ("Can you help me build a compiler in Rust?", "knowledge"),
            ("List all files in the workspace.", "file"),
            ("Read pyproject.toml", "file"),
            ("Search the web for python news", "web_research"),
            ("What is currently on my screen?", "desktop"),
            ("What was I working on?", "continuity_tool"),
            ("Restore my workspace", "restoration_tool"),
            ("Create a document called plan.md", "content_generator"),
            ("What can you do?", "echo"),
        ]

        for query, expected_tool in dispatch_matrix:
            plan = await planner.plan(query, tool_registry=registry)
            step_tools = [s.tool_name for s in plan.steps.values()]
            assert expected_tool in step_tools, f"Query '{query}' did not reach tool '{expected_tool}' (steps used: {step_tools})"


# ==============================================================================
# VOICE/TYPED PARITY & ANSWER PIPELINE INVARIANT
# ==============================================================================

class TestVoiceTypedParityAndSpeechPipeline:
    """Verifies voice/typed convergence and the invariant speech pipeline."""

    @pytest.mark.asyncio
    async def test_voice_and_typed_identical_routing(self):
        """Voice and typed queries must produce identical plan structure and tool dispatch."""
        planner = HierarchicalPlanner()
        registry = create_default_tool_registry()

        pairs = [
            ("Explain TCP in simple words.", "explain tcp in simple words"),
            ("Can you help me with a computer networks project?", "help me with a computer networks project"),
            ("What time is it now?", "what time is it"),
        ]
        for typed, voice in pairs:
            plan_typed = await planner.plan(typed, tool_registry=registry)
            plan_voice = await planner.plan(voice, tool_registry=registry)

            assert len(plan_typed.steps) == len(plan_voice.steps)
            step_t = list(plan_typed.steps.values())[0]
            step_v = list(plan_voice.steps.values())[0]
            assert step_t.tool_name == step_v.tool_name
            assert step_t.risk_level == step_v.risk_level

    def test_spoken_response_extraction_and_sanitization(self):
        """Verifies final_output -> extract_spoken_response -> EventSanitizer -> speech ready text."""
        plan = TaskPlan(
            plan_id="p1",
            request_id="req_test",
            user_prompt="What is a finite state machine?",
            steps={"s1": TaskStep(step_id="s1", title="k", tool_name="knowledge", description="k", parameters={})},
        )
        raw_answer = (
            "A finite state machine is a mathematical model of computation. "
            "It is an abstract machine that can be in exactly one of a finite number of states at any given time."
        )
        res = AgentExecutionResult(
            task_id="t1",
            request="What is a finite state machine?",
            status=TaskState.COMPLETED,
            plan=plan,
            completed_steps=["s1"],
            final_output={"answer": raw_answer, "source": "llm", "mode": "factual"},
            summary="Completed",
        )

        spoken = extract_spoken_response(res)
        assert spoken == raw_answer

        clean_tts = EventSanitizer.clean_message(spoken)
        assert len(clean_tts) > 20
        assert "finite state machine" in clean_tts


# ==============================================================================
# SECURITY REGRESSION
# ==============================================================================

class TestSecurityInvariants:
    """Verifies security controls: approval gates, workspace boundary, command allowlist."""

    @pytest.mark.asyncio
    async def test_non_allowlisted_command_requires_approval(self):
        """Commands outside the strict read-only allowlist require human approval."""
        tool = TerminalTool(allowed_workspace_root=os.getcwd())
        ctx = ExecutionContext()
        dangerous_params = TerminalActionParameters(command="rm", args=["-rf", "/"])

        # Risk level should be high or medium
        assert tool.risk_level in (RiskLevel.HIGH, RiskLevel.MEDIUM)

        # In safe mode, executing without approval must be blocked or validated
        result = await tool.execute(dangerous_params, ctx)
        assert result.success is False or "blocked" in str(result.output).lower() or "not allowed" in str(result.output).lower()

    @pytest.mark.asyncio
    async def test_file_tool_workspace_boundary_enforcement(self):
        """FileTool strictly enforces path boundary within allowed workspace."""
        tool = FileTool(allowed_workspace_root=os.getcwd())
        ctx = ExecutionContext()

        # Path traversal attempt outside workspace
        params = tool.validate_parameters({"action": "read_file", "path": "../../Windows/System32/drivers/etc/hosts"})
        result = await tool.execute(params, ctx)
        assert result.success is False
        assert any(w in (result.error or "").lower() for w in ("outside", "denied", "invalid", "traversal", "not permitted", "violation"))
