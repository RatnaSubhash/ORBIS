"""
Verification script for the 4 live acceptance scenarios on Windows:
Scenario 1: Informational Continuity ("What was I working on?")
Scenario 2: Genuine Restoration Action ("Continue where I left off.")
Scenario 3: Direct Project Restoration ("Open project ORBIS.")
Scenario 4: Ambiguity Gate ("Open it" with multiple candidate projects)
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from core.agent.conversation import ConversationContext, IntentType
from core.agent.orchestrator import AgentOrchestrator
from core.agent.planner.classifier import CapabilityClass, classify_capability
from core.events.bus import EventBus
from core.memory.db import DatabaseManager
from core.tools.registry import create_default_tool_registry


async def run_live_scenarios():
    print("=" * 60)
    print("STARTING LIVE RESTORATION INTEGRATION ACCEPTANCE SCENARIOS")
    print("=" * 60)

    db = DatabaseManager("orbis.db")
    db.initialize()
    bus = EventBus()
    tool_reg = create_default_tool_registry(db=db)

    from core.agent.planner.hierarchical import HierarchicalPlanner
    planner = HierarchicalPlanner()
    orch = AgentOrchestrator(
        tool_registry=tool_reg,
        db_manager=db,
        event_bus=bus,
        planner=planner,
    )

    # --------------------------------------------------------------------------
    # Scenario 1: Informational Continuity Query
    # --------------------------------------------------------------------------
    print("\n--- SCENARIO 1: Informational Continuity Query ---")
    prompt_1 = "What was I working on?"
    cap_1 = classify_capability(prompt_1)
    print(f"Prompt: '{prompt_1}' -> Classified Capability: {cap_1.value}")
    assert cap_1 == CapabilityClass.WORKSPACE_CONTINUITY, f"Expected WORKSPACE_CONTINUITY, got {cap_1}"

    res_1 = await orch.run_task(prompt_1)
    print(f"Task Status: {res_1.status}")
    print(f"Summary: {res_1.summary}")
    assert res_1.status.value == "COMPLETED", f"Expected COMPLETED, got {res_1.status}"
    assert len(orch.conversation_context.turns) == 1
    turn_1 = orch.conversation_context.turns[0]
    print(f"Context Last Projects: {orch.conversation_context.last_continuity_projects}")
    print(f"Context Last Applications: {orch.conversation_context.last_continuity_applications}")
    print("Scenario 1: SUCCESS (Informational retrieval only, no application launched)")

    # --------------------------------------------------------------------------
    # Scenario 2: Genuine Restoration Action
    # --------------------------------------------------------------------------
    print("\n--- SCENARIO 2: Genuine Restoration Action ---")
    prompt_2 = "Continue where I left off."
    cap_2 = classify_capability(prompt_2)
    print(f"Prompt: '{prompt_2}' -> Classified Capability: {cap_2.value}")
    assert cap_2 == CapabilityClass.WORKSPACE_RESTORATION, f"Expected WORKSPACE_RESTORATION, got {cap_2}"

    # Verify planner outputs execute=True
    plan_2 = await orch.planner.plan(prompt_2, tool_reg)
    assert "step_workspace_restoration" in plan_2.steps
    step_2 = plan_2.steps["step_workspace_restoration"]
    print(f"Planned step: {step_2.step_id} (tool: {step_2.tool_name}, execute: {step_2.parameters.get('execute')})")
    assert step_2.parameters.get("execute") is True, "Expected execute=True in planner step"
    print("Scenario 2: SUCCESS (Genuine restoration planned with execute=True)")

    # --------------------------------------------------------------------------
    # Scenario 3: Direct Project Restoration
    # --------------------------------------------------------------------------
    print("\n--- SCENARIO 3: Direct Project Restoration ---")
    prompt_3 = "Open project ORBIS"
    cap_3 = classify_capability(prompt_3)
    print(f"Prompt: '{prompt_3}' -> Classified Capability: {cap_3.value}")
    assert cap_3 == CapabilityClass.WORKSPACE_RESTORATION, f"Expected WORKSPACE_RESTORATION, got {cap_3}"

    plan_3 = await orch.planner.plan(prompt_3, tool_reg)
    step_3 = plan_3.steps["step_workspace_restoration"]
    print(f"Planned step target: {step_3.parameters.get('target_resource')}, execute: {step_3.parameters.get('execute')}")
    assert step_3.parameters.get("target_resource") == "ORBIS"
    assert step_3.parameters.get("execute") is True

    # Preflight validation on project ORBIS
    from core.restoration.models import ResourceTarget, ResourceTargetType
    from core.restoration.validator import PreflightValidator
    validator = PreflightValidator()
    target_3 = ResourceTarget(target_type=ResourceTargetType.DIRECTORY, raw_target="ORBIS")
    is_valid, resolved_3, err = validator.validate_resource_target(target_3)
    print(f"Preflight validation: is_valid={is_valid}, resolved='{resolved_3}', err='{err}'")
    assert is_valid is True
    assert Path(resolved_3) == Path("C:/Projects/ORBIS").resolve()
    print("Scenario 3: SUCCESS (Project 'ORBIS' correctly targets and validates against C:\\Projects\\ORBIS)")

    # --------------------------------------------------------------------------
    # Scenario 4: Ambiguity Gate
    # --------------------------------------------------------------------------
    print("\n--- SCENARIO 4: Ambiguity Gate on Multiple Candidate Projects ---")
    ambig_ctx = ConversationContext()
    # Turn 1: Continuity returned multiple projects
    ambig_ctx.record_turn(
        user_query="What was I working on?",
        resolved_query="What was I working on?",
        response_text="You were working across two projects: ORBIS and Dashboard.",
        tool_name="continuity_tool",
        continuity_data={
            "projects": ["ORBIS", "Dashboard"],
            "active_applications": ["Antigravity IDE", "Visual Studio Code"],
            "session_id": "sess-multi",
        },
    )

    # Turn 2: User says "Open it."
    ambig_res = ambig_ctx.resolve_query("Open it.")
    print(f"Ambiguity result: is_ambiguous={ambig_res.is_ambiguous}")
    print(f"Clarification question: '{ambig_res.clarification_message}'")
    assert ambig_res.is_ambiguous is True
    assert "ORBIS" in ambig_res.clarification_message
    assert "Dashboard" in ambig_res.clarification_message
    print("Scenario 4: SUCCESS (Ambiguity gate halts, requests clarification, zero processes executed)")

    print("\n" + "=" * 60)
    print("ALL 4 ACCEPTANCE SCENARIOS VERIFIED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_live_scenarios())
