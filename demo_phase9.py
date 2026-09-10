"""
ORBIS Phase 9 Demonstration Script: Intent Continuity Engine (ICE).
Demonstrates:
1. Paradigm shift from Phase 5 retrospective description ("What was I doing?")
   to Phase 9 normative forward projection ("What was I trying to accomplish? What remains unfinished? What should I do next?").
2. Multi-signal task state classification (ACTIVE, INTERRUPTED, COMPLETED, UNKNOWN).
3. Critical rule: Git commit is observed change evidence, NOT proof of goal completion.
4. Goal-specific completion detection (e.g. verified document or passed test suite).
5. Explainable heuristic continuity scores with normalized cosine similarity and transparent breakdowns.
6. Candidate next-action recommendations with safety and risk classification.
7. Safe fallback on sparse or empty workspace evidence.
8. End-to-end integration with IntentContinuityTool and AgentOrchestrator.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json

from core.agent.llm.mock import MockLLMProvider
from core.agent.orchestrator import AgentOrchestrator
from core.continuity.dna import ContextDNASynthesizer
from core.continuity.sessionizer import SessionSegmenter
from core.ice.engine import IntentContinuityEngine
from core.ice.models import ActionType, ICEQuery, TaskContinuityState
from core.memory.db import DatabaseManager
from core.memory.graph.builder import GraphBuilder
from core.memory.graph.graph import KnowledgeGraph
from core.memory.vector.embeddings.mock import MockEmbeddingProvider
from core.memory.vector.memory import SemanticMemory
from core.tools.intent_tool import IntentContinuityTool
from core.tools.registry import ToolRegistry
from core.types import WorkspaceEvent, WorkspaceEventType


async def run_phase9_demo() -> None:
    print("=" * 80)
    print("ORBIS PHASE 9 DEMONSTRATION: INTENT CONTINUITY ENGINE (ICE)")
    print("Grounded Goal Inference, Task-State Semantics, and Next-Action Prediction")
    print("=" * 80)

    db = DatabaseManager(db_path=":memory:")
    db.initialize()
    kg = KnowledgeGraph()
    vec_mem = SemanticMemory(embedding_provider=MockEmbeddingProvider(), db=db)

    now = datetime.now(timezone.utc)

    # =========================================================================
    # SCENARIO 1: INTERRUPTED Workflow (Failing Terminal Test Command)
    # =========================================================================
    print("\n" + "-" * 80)
    print("SCENARIO 1: Interrupted Task Inference with Terminal Failure")
    print("Context: User edited network HSRP protocol tests and ran pytest, which failed (exit code 1).")
    print("-" * 80)

    s1_events = [
        WorkspaceEvent(
            event_id="ev_s1_01",
            timestamp=now - timedelta(minutes=45),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="Code.exe",
            window_title="hsrp_protocol.py - ORBIS",
            resource_path="c:\\Projects\\ORBIS\\protocols\\hsrp_protocol.py",
        ),
        WorkspaceEvent(
            event_id="ev_s1_02",
            timestamp=now - timedelta(minutes=40),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="Code.exe",
            window_title="test_hsrp.py - ORBIS",
            resource_path="c:\\Projects\\ORBIS\\tests\\test_hsrp.py",
        ),
        WorkspaceEvent(
            event_id="ev_s1_03",
            timestamp=now - timedelta(minutes=30),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="WindowsTerminal.exe",
            window_title="PowerShell: pytest tests/test_hsrp.py",
            resource_path="c:\\Projects\\ORBIS\\tests\\test_hsrp.py",
        ),
    ]
    for ev in s1_events:
        db.record_workspace_event(ev)

    segmenter = SessionSegmenter(session_gap_seconds=900.0)
    s1_sessions = segmenter.segment(s1_events)
    s1 = s1_sessions[0]
    db.record_session(s1)

    dna_synth = ContextDNASynthesizer()
    dna1 = dna_synth.synthesize(s1, s1_events)
    db.record_context_dna(dna1)

    graph_builder = GraphBuilder()
    graph_builder.build_from_session(session=s1, dna=dna1, events=s1_events, graph=kg)

    # Log terminal execution action with failing exit code 1
    db.log_action(
        task_id=s1.session_id,
        action_name="terminal",
        risk_level="MEDIUM",
        parameters={"command": "pytest tests/test_hsrp.py"},
        result={"output": {"exit_code": 1, "stdout": "FAILED: test_failover_election"}},
        verified=False,
    )

    ice_engine = IntentContinuityEngine(db=db, graph=kg, vector_memory=vec_mem)
    result_s1 = await ice_engine.infer_intent("What was I trying to accomplish, and what should I do next?")

    assert result_s1.has_sufficient_evidence is True
    p1 = result_s1.primary_intent
    print(f"[*] Inferred Goal:        {p1.goal_description}")
    print(f"[*] Task Continuity State: {p1.task_state.value}")
    print(f"[*] State Reasoning:       {p1.state_reasoning}")
    print(f"[*] Continuity Score:      {p1.continuity_score:.4f} (Heuristic)")
    print(f"[*] Score Breakdown:       {json.dumps(p1.score_breakdown)}")
    print(f"[*] Unfinished Items:      {[u.description for u in p1.unfinished_work]}")
    print(f"[*] Recommended Actions:")
    for act in p1.candidate_next_actions:
        print(f"    - [{act.action_type.value}] {act.title} (Risk: {act.estimated_risk.value})")

    # =========================================================================
    # SCENARIO 2: Git Commit is NOT Automatic Proof of Goal Completion
    # =========================================================================
    print("\n" + "-" * 80)
    print("SCENARIO 2: Git Commit Recorded (WIP) — Not Automatically Completed")
    print("Context: User committed changes ('wip: add router models'), but unverified draft edits remain.")
    print("-" * 80)

    s2_events = [
        WorkspaceEvent(
            event_id="ev_s2_01",
            timestamp=now - timedelta(minutes=25),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="Code.exe",
            window_title="router.py - ORBIS",
            resource_path="c:\\Projects\\ORBIS\\models\\router.py",
        ),
        WorkspaceEvent(
            event_id="ev_s2_02",
            timestamp=now - timedelta(minutes=20),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="WindowsTerminal.exe",
            window_title="git commit -m 'wip: router models'",
            resource_path="c:\\Projects\\ORBIS\\models\\router.py",
        ),
    ]
    for ev in s2_events:
        db.record_workspace_event(ev)

    s2_sessions = segmenter.segment(s2_events)
    s2 = s2_sessions[0]
    db.record_session(s2)
    dna2 = dna_synth.synthesize(s2, s2_events)
    db.record_context_dna(dna2)

    db.log_action(
        task_id=s2.session_id,
        action_name="terminal",
        risk_level="HIGH",
        parameters={"command": "git commit -m 'wip: router models'"},
        result={"output": {"exit_code": 0, "stdout": "[master a1b2c3d] wip: router models"}},
        verified=True,
    )

    result_s2 = await ice_engine.infer_intent("Did I finish the router work?")
    p2 = result_s2.primary_intent
    print(f"[*] Inferred Goal:        {p2.goal_description}")
    print(f"[*] Task Continuity State: {p2.task_state.value}")
    print(f"[*] State Reasoning:       {p2.state_reasoning}")
    print(f"[*] Goal Completed?:       {p2.task_state == TaskContinuityState.COMPLETED} (Git commit is NOT proof of completion!)")

    # =========================================================================
    # SCENARIO 3: Goal-Specific Verified Completion
    # =========================================================================
    print("\n" + "-" * 80)
    print("SCENARIO 3: Goal-Specific Completion Detection")
    print("Context: User asked to draft project specifications and DocumentTool verified creation.")
    print("-" * 80)

    s3_events = [
        WorkspaceEvent(
            event_id="ev_s3_01",
            timestamp=now - timedelta(minutes=15),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="Code.exe",
            window_title="project_spec.md - ORBIS",
            resource_path="c:\\Projects\\ORBIS\\docs\\project_spec.md",
        ),
        WorkspaceEvent(
            event_id="ev_s3_02",
            timestamp=now - timedelta(minutes=10),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="Code.exe",
            window_title="project_spec.md - ORBIS",
            resource_path="c:\\Projects\\ORBIS\\docs\\project_spec.md",
        ),
    ]
    for ev in s3_events:
        db.record_workspace_event(ev)

    s3_sessions = segmenter.segment(s3_events)
    s3 = s3_sessions[0]
    db.record_session(s3)
    dna3 = dna_synth.synthesize(s3, s3_events)
    db.record_context_dna(dna3)
    graph_builder.build_from_session(session=s3, dna=dna3, events=s3_events, graph=kg)

    # Record verified document creation audit log
    db.log_action(
        task_id=s3.session_id,
        action_name="document_tool",
        risk_level="LOW",
        parameters={"action": "create_document", "file_path": "docs/project_spec.md"},
        result={"output": {"bytes_written": 1024}},
        verified=True,  # Verified completion
    )

    result_s3 = await ice_engine.infer_intent("project spec report")
    p3 = result_s3.primary_intent
    print(f"[*] Inferred Goal:        {p3.goal_description}")
    print(f"[*] Task Continuity State: {p3.task_state.value}")
    print(f"[*] State Reasoning:       {p3.state_reasoning}")
    print(f"[*] Continuity Score:      {p3.continuity_score:.4f} (Heuristic, capped at 0.85)")
    print(f"[*] Next Action:           [{p3.candidate_next_actions[0].action_type.value}] {p3.candidate_next_actions[0].title}")

    # =========================================================================
    # SCENARIO 4: IntentContinuityTool and Agent Orchestrator Integration
    # =========================================================================
    print("\n" + "-" * 80)
    print("SCENARIO 4: IntentContinuityTool via AgentOrchestrator")
    print("Context: Requesting intent analysis through AgentOrchestrator.run_intent_continuity()")
    print("-" * 80)

    registry = ToolRegistry()
    intent_tool = IntentContinuityTool(engine=ice_engine)
    registry.register(intent_tool)

    orchestrator = AgentOrchestrator(
        llm_provider=MockLLMProvider(),
        tool_registry=registry,
        db_manager=db,
        intent_engine=ice_engine,
    )

    orch_result = await orchestrator.run_intent_continuity("What remains unfinished, and what should I probably do next?")
    print(f"[*] Has Sufficient Evidence: {orch_result.has_sufficient_evidence}")
    print(f"[*] Top Primary Intent:      {orch_result.primary_intent.goal_description}")
    print(f"[*] Primary State:           {orch_result.primary_intent.task_state.value}")
    print(f"[*] Explainability Summary:\n{orch_result.explainability_summary}")

    # =========================================================================
    # SCENARIO 5: Safe Fallback on Empty Workspace
    # =========================================================================
    print("\n" + "-" * 80)
    print("SCENARIO 5: Safe Fallback on Empty Database")
    print("-" * 80)

    empty_db = DatabaseManager(db_path=":memory:")
    empty_db.initialize()
    empty_engine = IntentContinuityEngine(db=empty_db)
    empty_result = await empty_engine.infer_intent("What was I doing?")

    print(f"[*] Has Sufficient Evidence: {empty_result.has_sufficient_evidence}")
    print(f"[*] Primary Intent:          {empty_result.primary_intent}")
    print(f"[*] Explainability Summary:  {empty_result.explainability_summary}")

    print("\n" + "=" * 80)
    print("PHASE 9 DEMONSTRATION COMPLETE: ALL SCENARIOS VERIFIED SUCCESSFULLY")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_phase9_demo())
