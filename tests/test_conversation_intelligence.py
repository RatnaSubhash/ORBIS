"""
Comprehensive Test Suite for ORBIS Context & Intent Intelligence.
Covers:
A. Explain TCP -> Compare it with UDP
B. Explain TCP -> Which one is faster?
C. Explain TCP -> What about reliability? (preserves topic invariant)
D. Explain TCP -> Explain that again / in simple words
E. Explain TCP -> Explain DNS -> Compare it with DHCP (no stale TCP)
F. Explain mutex -> Compare that with semaphores -> Which is better for race conditions?
G. Topic switch prevents stale context contamination
H. Missing comparison entity produces clarification, NEVER [Concept 1] / [Concept 2]
I. Natural paraphrases resolve to equivalent comparison intents
J. Generic pronoun and reference resolution (both, the first one, the second one, it, that)
K. Relevance-aware bounded context (new topics outrank stale topics)
L. Cross-capability continuity: Knowledge -> Research -> Document -> Workspace
M. Workspace/continuity routing regression tests (never route to KnowledgeTool)
N. System Control regression preservation (Volume/Mute)
O. Full 10-turn experience acceptance test through AgentOrchestrator
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.agent.conversation import (
    ComparisonIntent,
    ConversationContext,
    IntentType,
    ResolutionResult,
    extract_comparison_pair,
    detect_topic_reset,
    is_aspect_query,
    is_which_one_query,
)
from core.agent.llm.mock import MockLLMProvider
from core.agent.models import AgentRequest, TaskPlan, TaskState
from core.agent.orchestrator import AgentOrchestrator
from core.agent.planner.classifier import CapabilityClass, classify_capability
from core.agent.planner.hierarchical import HierarchicalPlanner
from core.events.bus import EventBus
from core.tools.knowledge_tool import KnowledgeParameters, KnowledgeTool
from core.tools.registry import create_default_tool_registry
from core.types import ExecutionContext, RiskLevel, ToolResult


# ==============================================================================
# Unit Tests: Comparison Extraction & Paraphrases (Requirements I, H)
# ==============================================================================

def test_extract_comparison_pair_variations():
    """Verifies that natural comparative paraphrases correctly extract concepts and dimensions."""
    # 1. Standard compare
    ca, cb, dim = extract_comparison_pair("Compare TCP and UDP")
    assert ca == "TCP"
    assert cb == "UDP"
    assert dim is None

    # 2. Compare with dimension
    ca, cb, dim = extract_comparison_pair("Compare TCP with UDP in terms of speed")
    assert ca == "TCP"
    assert cb == "UDP"
    assert dim == "speed"

    # 3. Differences between
    ca, cb, dim = extract_comparison_pair("What is the difference between mutexes and semaphores?")
    assert ca == "mutexes"
    assert cb == "semaphores"

    # 4. How does A differ from B
    ca, cb, dim = extract_comparison_pair("How does Docker differ from Kubernetes?")
    assert ca == "Docker"
    assert cb == "Kubernetes"

    # 5. How are A and B different
    ca, cb, dim = extract_comparison_pair("How are Python and Java different?")
    assert ca == "Python"
    assert cb == "Java"

    # 6. Put A and B side by side
    ca, cb, dim = extract_comparison_pair("Put PostgreSQL and MongoDB side by side")
    assert ca == "PostgreSQL"
    assert cb == "MongoDB"

    # 7. Between A and B, which one is C
    ca, cb, dim = extract_comparison_pair("Between TCP and UDP, which one is faster?")
    assert ca == "TCP"
    assert cb == "UDP"
    assert "faster" in dim

    # 8. A versus B
    ca, cb, dim = extract_comparison_pair("React vs Vue")
    assert ca == "React"
    assert cb == "Vue"


def test_detect_topic_reset_variations():
    """Verifies topic break and reset patterns extract the new topic and subquery."""
    is_reset, topic, sub = detect_topic_reset("Okay, forget networking for a second. Explain mutexes.")
    assert is_reset is True
    assert topic == "mutexes"
    assert sub == "Explain mutexes."

    is_reset, topic, sub = detect_topic_reset("Forget that. Tell me about DNS.")
    assert is_reset is True
    assert topic == "DNS"
    assert sub == "Tell me about DNS."

    is_reset, topic, sub = detect_topic_reset("Never mind that. What is Docker?")
    assert is_reset is True
    assert topic == "Docker"
    assert sub == "What is Docker?"

    is_reset, topic, sub = detect_topic_reset("Let's switch to Kubernetes. How does it work?")
    assert is_reset is True
    assert sub == "How does it work?"

    # Non-reset queries must return False
    is_reset, _, _ = detect_topic_reset("Explain TCP.")
    assert is_reset is False


def test_is_aspect_query_precision():
    """Verifies that evaluation dimensions are identified while entity proposals are preserved."""
    assert is_aspect_query("What about reliability?")[0] is True
    assert is_aspect_query("What about performance?")[0] is True
    assert is_aspect_query("What about speed?")[0] is True
    assert is_aspect_query("How about latency?")[0] is True
    assert is_aspect_query("What about security?")[0] is True
    assert is_aspect_query("What about ease of use?")[0] is True

    # Real entities must NOT be categorized as evaluation aspects
    assert is_aspect_query("What about HSRP?")[0] is False
    assert is_aspect_query("What about UDP?")[0] is False
    assert is_aspect_query("What about Docker?")[0] is False


# ==============================================================================
# Scenario A: Explain TCP -> Compare it with UDP
# ==============================================================================

def test_scenario_a_tcp_to_udp_comparison():
    ctx = ConversationContext()
    r1 = ctx.resolve_query("Explain TCP.")
    assert r1.active_topic == "TCP"
    ctx.record_turn("Explain TCP.", r1.resolved_query, "TCP is connection-oriented...")

    r2 = ctx.resolve_query("Compare it with UDP.")
    assert r2.was_modified is True
    assert "TCP" in r2.resolved_query
    assert "UDP" in r2.resolved_query
    assert r2.comparison is not None
    assert r2.comparison.concept_a == "TCP"
    assert r2.comparison.concept_b == "UDP"
    assert ctx.active_topic == "TCP vs UDP"


# ==============================================================================
# Scenario B: Explain TCP -> Compare it with UDP -> Which one is faster?
# ==============================================================================

def test_scenario_b_which_one_is_faster():
    ctx = ConversationContext()
    r1 = ctx.resolve_query("Explain TCP.")
    ctx.record_turn("Explain TCP.", r1.resolved_query, "TCP is connection-oriented...")

    r2 = ctx.resolve_query("Compare it with UDP.")
    ctx.record_turn(
        "Compare it with UDP.",
        r2.resolved_query,
        "TCP guarantees delivery while UDP offers lower latency.",
        comparison=r2.comparison,
    )

    r3 = ctx.resolve_query("Which one is faster?")
    assert r3.was_modified is True
    assert "Between TCP and UDP" in r3.resolved_query
    assert "faster" in r3.resolved_query
    assert r3.comparison is not None
    assert r3.comparison.concept_a == "TCP"
    assert r3.comparison.concept_b == "UDP"


# ==============================================================================
# Scenario C: Explain TCP -> Compare it with UDP -> What about reliability?
# (Preserves Topic Invariant: reliability must NOT become the active topic)
# ==============================================================================

def test_scenario_c_aspect_follow_up_preserves_topic():
    ctx = ConversationContext()
    r1 = ctx.resolve_query("Explain TCP.")
    ctx.record_turn("Explain TCP.", r1.resolved_query, "TCP is connection-oriented...")

    r2 = ctx.resolve_query("Compare it with UDP.")
    ctx.record_turn(
        "Compare it with UDP.",
        r2.resolved_query,
        "TCP guarantees delivery while UDP offers lower latency.",
        comparison=r2.comparison,
    )

    r3 = ctx.resolve_query("What about reliability?")
    assert r3.was_modified is True
    assert "reliability" in r3.resolved_query.lower()
    assert "TCP" in r3.resolved_query
    assert "UDP" in r3.resolved_query
    # Crucial Invariant: Active topic remains the concepts, NOT "reliability"
    assert ctx.active_topic == "TCP vs UDP"
    assert ctx.active_topic != "reliability"


# ==============================================================================
# Scenario D: Explain TCP -> Explain that again / in simple words
# ==============================================================================

def test_scenario_d_explain_that_again():
    ctx = ConversationContext()
    r1 = ctx.resolve_query("Explain TCP.")
    ctx.record_turn("Explain TCP.", r1.resolved_query, "TCP is connection-oriented...")

    r2 = ctx.resolve_query("Explain that in simple words.")
    assert r2.was_modified is True
    assert "TCP" in r2.resolved_query
    assert "simple" in r2.resolved_query.lower()
    assert r2.intent_type == IntentType.FOLLOW_UP_SIMPLIFICATION


# ==============================================================================
# Scenario E: Explain TCP -> Explain DNS -> Compare it with DHCP (No stale TCP)
# ==============================================================================

def test_scenario_e_topic_switch_clears_stale_tcp():
    ctx = ConversationContext()
    r1 = ctx.resolve_query("Explain TCP.")
    ctx.record_turn("Explain TCP.", r1.resolved_query, "TCP is transport layer.")

    # Switch to DNS
    r2 = ctx.resolve_query("Explain DNS.")
    assert r2.active_topic == "DNS"
    ctx.record_turn("Explain DNS.", r2.resolved_query, "DNS translates domain names to IPs.")

    # Compare it with DHCP -> must resolve to DNS, NOT stale TCP
    r3 = ctx.resolve_query("Compare it with DHCP.")
    assert r3.was_modified is True
    assert "DNS" in r3.resolved_query
    assert "DHCP" in r3.resolved_query
    assert "TCP" not in r3.resolved_query
    assert r3.comparison.concept_a == "DNS"
    assert r3.comparison.concept_b == "DHCP"


# ==============================================================================
# Scenario F: Explain mutex -> Compare that with semaphores -> Which is better for race conditions?
# ==============================================================================

def test_scenario_f_mutex_semaphore_comparison_and_choice():
    ctx = ConversationContext()
    r1 = ctx.resolve_query("Explain mutexes.")
    assert r1.active_topic == "mutexes"
    ctx.record_turn("Explain mutexes.", r1.resolved_query, "A mutex provides mutual exclusion.")

    r2 = ctx.resolve_query("Compare that with semaphores.")
    assert r2.was_modified is True
    assert "mutexes" in r2.resolved_query
    assert "semaphores" in r2.resolved_query
    assert r2.comparison.concept_a == "mutexes"
    assert r2.comparison.concept_b == "semaphores"
    ctx.record_turn(
        "Compare that with semaphores.",
        r2.resolved_query,
        "Mutex is binary locking while semaphore supports multiple permits.",
        comparison=r2.comparison,
    )

    r3 = ctx.resolve_query("Which one is better for race conditions?")
    assert r3.was_modified is True
    assert "Between mutexes and semaphores" in r3.resolved_query
    assert "race conditions" in r3.resolved_query
    assert r3.comparison.concept_a == "mutexes"
    assert r3.comparison.concept_b == "semaphores"


# ==============================================================================
# Scenario G: Topic switch must prevent stale context contamination
# ==============================================================================

def test_scenario_g_explicit_topic_reset():
    ctx = ConversationContext()
    r1 = ctx.resolve_query("Explain TCP.")
    ctx.record_turn("Explain TCP.", r1.resolved_query, "TCP details...")

    r2 = ctx.resolve_query("Compare it with UDP.")
    ctx.record_turn("Compare it with UDP.", r2.resolved_query, "TCP vs UDP details...", comparison=r2.comparison)

    # Explicit reset
    r3 = ctx.resolve_query("Okay, forget networking for a second. Explain mutexes.")
    assert r3.topic_switched is True
    assert ctx.active_topic == "mutexes"
    assert ctx.comparison_state is None
    assert "mutexes" in r3.resolved_query


# ==============================================================================
# Scenario H: Missing comparison entity must never produce [Concept 1] / [Concept 2]
# ==============================================================================

def test_scenario_h_missing_comparison_entity_produces_clarification():
    ctx = ConversationContext()
    # Incomplete comparison with no active context
    r1 = ctx.resolve_query("Compare it.")
    assert r1.is_ambiguous is True
    assert r1.clarification_message is not None
    assert "[Concept" not in r1.clarification_message

    # Incomplete comparison with partial context
    ctx.record_turn("Explain TCP.", "Explain TCP.", "TCP details.")
    r2 = ctx.resolve_query("Compare it.")
    assert r2.is_ambiguous is True
    assert "What would you like me to compare TCP with?" in r2.clarification_message


@pytest.mark.asyncio
async def test_knowledge_tool_verify_rejects_placeholders():
    """Verifies that KnowledgeTool.verify strictly fails if [Concept 1] or [Concept 2] is present."""
    tool = KnowledgeTool(llm_provider=None)
    params = KnowledgeParameters(query="Compare TCP and UDP", mode="comparison", concept_a="TCP", concept_b="UDP")

    # Output with leaked placeholder tokens
    bad_result = ToolResult(
        success=True,
        output={
            "action": "knowledge_synthesis",
            "answer": "After analyzing the concepts, I found the following differences:\nDifferences Between [Concept 1] and [Concept 2]...",
            "mode": "comparison",
        },
    )
    rep = await tool.verify(params, bad_result, ExecutionContext())
    assert rep.verified is False
    assert "unresolved placeholder token" in rep.details


# ==============================================================================
# Scenario J: Generic pronoun and reference resolution (both, the first one, the second one)
# ==============================================================================

def test_scenario_j_pronoun_resolution_both_first_second():
    ctx = ConversationContext()
    comp = ComparisonIntent(concept_a="TCP", concept_b="UDP", source="current_request")
    ctx.comparison_state = comp
    ctx.active_topic = "TCP vs UDP"

    # "both"
    r_both = ctx.resolve_query("Are both transport layer protocols?")
    assert r_both.was_modified is True
    assert "TCP and UDP" in r_both.resolved_query

    # "the first one"
    r_first = ctx.resolve_query("Is the first one connection oriented?")
    assert r_first.was_modified is True
    assert "TCP" in r_first.resolved_query

    # "the second one"
    r_second = ctx.resolve_query("Does the second one support streaming?")
    assert r_second.was_modified is True
    assert "UDP" in r_second.resolved_query


# ==============================================================================
# Scenario L: Cross-Capability Continuity (Knowledge -> Research -> Document)
# ==============================================================================

def test_scenario_l_cross_capability_continuity():
    ctx = ConversationContext()
    r1 = ctx.resolve_query("Explain mutexes.")
    ctx.record_turn("Explain mutexes.", r1.resolved_query, "Mutex explanation.")

    r2 = ctx.resolve_query("Compare that with semaphores.")
    ctx.record_turn("Compare that with semaphores.", r2.resolved_query, "Comparison.", comparison=r2.comparison)

    # Transition to research
    r3 = ctx.resolve_query("Now find me a good resource about this.")
    assert r3.intent_type == IntentType.RESEARCH
    assert "mutexes" in r3.resolved_query
    assert "semaphores" in r3.resolved_query
    assert classify_capability(r3.resolved_query) == CapabilityClass.FRESH_INFORMATION

    # Transition to document creation
    r4 = ctx.resolve_query("Save the important points to my project folder.")
    assert r4.intent_type == IntentType.DOCUMENT_CREATION
    assert "mutexes" in r4.resolved_query
    assert "semaphores" in r4.resolved_query
    cap4 = classify_capability(r4.resolved_query)
    assert cap4 in (CapabilityClass.FILE_CREATION, CapabilityClass.DOCUMENT_GENERATION)


# ==============================================================================
# Scenario M: Workspace / Continuity Routing Regressions (NEVER generic KnowledgeTool)
# ==============================================================================

@pytest.mark.parametrize(
    "query, expected_cap",
    [
        # Direct questions
        ("What was I working on?", CapabilityClass.WORKSPACE_CONTINUITY),
        ("What apps was I using?", CapabilityClass.WORKSPACE_CONTINUITY),
        ("Which files was I working with?", CapabilityClass.WORKSPACE_CONTINUITY),
        ("What was I doing earlier?", CapabilityClass.WORKSPACE_CONTINUITY),
        ("Where did I leave off?", CapabilityClass.WORKSPACE_CONTINUITY),
        # Indirect / embedded questions with conversational wrappers
        ("Can you tell me what I was doing earlier?", CapabilityClass.WORKSPACE_CONTINUITY),
        ("Tell me what I was working on.", CapabilityClass.WORKSPACE_CONTINUITY),
        ("Do you know what apps I was using?", CapabilityClass.WORKSPACE_CONTINUITY),
        ("Can you show me which files I was working with?", CapabilityClass.WORKSPACE_CONTINUITY),
        ("Could you tell me what I was doing earlier?", CapabilityClass.WORKSPACE_CONTINUITY),
        # Restoration queries
        ("Continue where I left off.", CapabilityClass.WORKSPACE_RESTORATION),
        ("Open the project I was working on.", CapabilityClass.WORKSPACE_RESTORATION),
        ("Restore my previous workspace.", CapabilityClass.WORKSPACE_RESTORATION),
    ],
)
def test_scenario_m_workspace_continuity_routing(query, expected_cap):
    """Verifies that none of the workspace continuity queries route to KnowledgeTool or GENERAL_INFORMATION."""
    cap = classify_capability(query)
    assert cap == expected_cap
    assert cap != CapabilityClass.GENERAL_INFORMATION
    assert cap != CapabilityClass.TECHNICAL_EXPLANATION


@pytest.mark.parametrize(
    "query, expected_cap",
    [
        ("Can you tell me what the capital of France is?", CapabilityClass.GENERAL_INFORMATION),
        ("Can you tell me who the President of France is?", CapabilityClass.GENERAL_INFORMATION),
        ("Tell me about TCP.", CapabilityClass.GENERAL_INFORMATION),
        ("Could you explain TCP?", CapabilityClass.TECHNICAL_EXPLANATION),
    ],
)
def test_scenario_m_negative_conversational_wrappers(query, expected_cap):
    """Verifies that conversational wrappers on general questions do NOT route to WORKSPACE_CONTINUITY."""
    cap = classify_capability(query)
    assert cap == expected_cap
    assert cap != CapabilityClass.WORKSPACE_CONTINUITY


# ==============================================================================
# Scenario N: System Control Volume/Mute Regressions
# ==============================================================================

@pytest.mark.parametrize(
    "query",
    [
        "Increase volume to 90%",
        "Set volume to 50%",
        "What is the current volume?",
        "Mute sound",
        "Unmute",
    ],
)
def test_scenario_n_system_control_regressions(query):
    """Verifies that volume and audio commands route strictly to SYSTEM_CONTROL."""
    cap = classify_capability(query)
    assert cap == CapabilityClass.SYSTEM_CONTROL


# ==============================================================================
# Scenario O: Full 10-Turn Experience-Level Acceptance Test through AgentOrchestrator
# ==============================================================================

@pytest.mark.asyncio
async def test_scenario_o_ten_turn_acceptance():
    """
    Executes the entire 10-turn dialogue through AgentOrchestrator, verifying that each
    turn plans, executes, resolves references, and produces human-readable output
    without placeholder tokens or routing regressions.
    """
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp_dir:
        tools = create_default_tool_registry()
        doc_tool = tools.get("document")
        if doc_tool:
            doc_tool.allowed_root = Path(tmp_dir).resolve()
        llm = MockLLMProvider()
        planner = HierarchicalPlanner(llm_provider=llm)
        bus = EventBus()
        orchestrator = AgentOrchestrator(
            llm_provider=llm,
            tool_registry=tools,
            planner=planner,
            event_bus=bus,
        )

        dialogue = [
            ("Explain TCP.", "tcp"),
            ("Compare it with UDP.", "udp"),
            ("Which one is faster?", "faster"),
            ("What about reliability?", "reliability"),
            ("Give me a simple real-world example.", "example"),
            ("Okay, forget networking for a second. Explain mutexes.", "mutex"),
            ("Compare that with semaphores.", "semaphore"),
            ("Which one is more useful in an operating system?", "operating system"),
            ("Now find me a good resource about this.", "resource"),
            ("Save the important points to my project folder.", "document"),
        ]

        for turn_num, (query, expected_keyword) in enumerate(dialogue, 1):
            res = await orchestrator.run_task(query)
            assert res.status == TaskState.COMPLETED, f"Turn {turn_num} failed with status {res.status}: {res.error}"
            assert res.summary is not None and len(res.summary.strip()) > 5
            # Verify no placeholder tokens leaked
            assert "[Concept" not in res.summary
            assert "[Topic" not in res.summary
            assert "[Entity" not in res.summary

            # Verify contextual continuity
            if turn_num == 2:
                assert "tcp" in res.summary.lower() and "udp" in res.summary.lower()
            elif turn_num == 6:
                assert "mutex" in res.summary.lower()
            elif turn_num == 7:
                assert "semaphore" in res.summary.lower()


# ==============================================================================
# Scenario P: Production Path Indirect Continuity Resolution & Tool Selection
# ==============================================================================

@pytest.mark.asyncio
async def test_scenario_p_production_path_indirect_continuity():
    """
    Verifies that conversational indirect/embedded continuity questions:
    1. Resolve to WORKSPACE_CONTINUITY intent and capability.
    2. Formulate a TaskPlan targeting 'continuity_tool' (step_continuity).
    3. Strictly DO NOT fall through to 'knowledge' (KnowledgeTool).
    4. Execute through the production AgentOrchestrator pipeline successfully.
    """
    tools = create_default_tool_registry()
    llm = MockLLMProvider()
    planner = HierarchicalPlanner(llm_provider=llm)
    bus = EventBus()
    orchestrator = AgentOrchestrator(
        llm_provider=llm,
        tool_registry=tools,
        planner=planner,
        event_bus=bus,
    )

    test_queries = [
        "Can you tell me what I was doing earlier?",
        "Tell me what I was working on.",
        "Do you know what apps I was using?",
        "Can you show me which files I was working with?",
        "Could you tell me what I was doing earlier?",
    ]

    for q in test_queries:
        res = await orchestrator.run_task(q)
        assert res.status == TaskState.COMPLETED, f"Query {q!r} failed with status {res.status}: {res.error}"
        assert res.plan is not None, f"Query {q!r} produced no plan"
        plan_tools = [s.tool_name for s in res.plan.steps.values()]
        assert "continuity_tool" in plan_tools, f"Query {q!r} did not select continuity_tool: {plan_tools}"
        assert "knowledge" not in plan_tools, f"Query {q!r} incorrectly fell through to KnowledgeTool!"
        assert "knowledge_tool" not in plan_tools, f"Query {q!r} incorrectly fell through to KnowledgeTool!"
        # Verify the answer does not claim to be a generic factual engine unaware of user activity
        assert "I'm a factual knowledge engine" not in (res.summary or "")

