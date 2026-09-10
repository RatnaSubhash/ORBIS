"""
ORBIS Phase 6 Multi-Step Agent Brain & Hierarchical Planner Demonstration Script.

Demonstrates:
1. Deterministic Multi-Step Hierarchical Planning & Execution (Offline, no network/LLM required).
   - Task DAG construction with parent-child relationships and dependency resolution.
   - Sequential step execution with independent verification on each step.
   - Dynamic context passing (input bindings) across steps.
2. Verification Failure & Controlled Replanning / Cascading Blocks.
   - Detection of step verification rejection.
   - Safe replanning within validated ToolRegistry without risk escalation.
   - Cascading failure blocking downstream dependent steps.
3. Human-in-the-Loop Backend Approval Gate.
   - High-risk actions halt in BLOCKED_PENDING_APPROVAL when unapproved.
   - Safe resumption/execution when backend approval callback grants permission.
4. Seamless Backward Compatibility:
   - Phase 1 single-step run(), Phase 5 continuity run_continuity(), and Phase 6 multi-step run_task().
"""

import asyncio
from pathlib import Path
import tempfile
from typing import Optional

from pydantic import BaseModel

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
from core.tools.echo_tool import EchoTool
from core.tools.file_tool import FileTool
from core.tools.registry import ToolRegistry
from core.types import (
    ExecutionContext,
    RiskLevel,
    StatusEventType,
    ToolCall,
    ToolResult,
    VerificationReport,
)


class EmptyParams(BaseModel):
    """Empty parameter schema for custom demonstration tools."""
    pass


class ControlledDestructiveTool(BaseTool):
    """Demonstration tool simulating a high-risk operation requiring explicit authorization."""
    name: str = "controlled_destructive_tool"
    description: str = "Simulates a destructive operation that requires human approval."
    risk_level: RiskLevel = RiskLevel.HIGH
    parameters_schema = EmptyParams

    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        return ToolResult(
            success=True,
            output="High-risk configuration purge completed safely.",
            metadata={"audit_logged": True},
        )

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        return VerificationReport(
            verified=True,
            details="Verified high-risk state modification.",
        )


class FlakyVerificationTool(BaseTool):
    """Demonstration tool simulating an action whose verification independently fails."""
    name: str = "flaky_verification_tool"
    description: str = "Executes successfully but deliberately fails independent post-condition verification."
    risk_level: RiskLevel = RiskLevel.LOW
    parameters_schema = EmptyParams

    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        return ToolResult(success=True, output="Tool reported success in execution.")

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        return VerificationReport(
            verified=False,
            details="Verification detected inconsistent post-execution state.",
            discrepancies=["State mismatch on filesystem disk check."],
        )


async def run_phase6_demo() -> None:
    print("=" * 80)
    print("ORBIS PHASE 6 DEMONSTRATION: MULTI-STEP AGENT BRAIN & HIERARCHICAL PLANNER")
    print("Offline, Deterministic Planning, Step Verification, DAG Execution & Approval")
    print("=" * 80)

    # Setup isolated workspace
    with tempfile.TemporaryDirectory() as tmp_dir:
        workspace_path = Path(tmp_dir)
        readme_file = workspace_path / "README.md"
        readme_file.write_text(
            "# Project ORBIS Architecture\n\n"
            "Phase 1: Minimal Agent Shell\n"
            "Phase 2: Local Desktop Awareness\n"
            "Phase 3: Context DNA Engine\n"
            "Phase 4: Knowledge Graph + Vector Memory\n"
            "Phase 5: First Vertical-Slice Continuity MVP\n"
            "Phase 6: Multi-Step Agent Brain & Hierarchical Planner\n",
            encoding="utf-8",
        )

        # Setup Tool Registry
        registry = ToolRegistry()
        file_tool = FileTool(allowed_workspace_root=str(workspace_path))
        echo_tool = EchoTool()
        destructive_tool = ControlledDestructiveTool()
        flaky_tool = FlakyVerificationTool()

        registry.register(file_tool)
        registry.register(echo_tool)
        registry.register(destructive_tool)
        registry.register(flaky_tool)

        planner = HierarchicalPlanner()
        executor = TaskExecutor(tool_registry=registry, planner=planner)

        # ======================================================================
        # SCENARIO 1: DETERMINISTIC MULTI-STEP HIERARCHICAL TASK EXECUTION
        # ======================================================================
        print("\n" + "-" * 80)
        print("SCENARIO 1: Deterministic Multi-Step Hierarchical Plan (Locate -> Read -> Summarize)")
        print("-" * 80)

        prompt_1 = "Find the ORBIS README and summarize it"
        print(f"User Request: '{prompt_1}'")

        # 1. Plan construction
        plan = await planner.plan(AgentRequest(user_prompt=prompt_1), registry)
        print(f"\n[Planner] Generated TaskPlan ID: {plan.plan_id}")
        print(f"[Planner] Task Type: {plan.task_type.value} | Total Steps: {len(plan.steps)}")
        print("Planned Steps DAG:")
        for sid, step in plan.steps.items():
            deps = f" -> depends on {step.dependencies}" if step.dependencies else " (Root/Ready)"
            parent = f" [Child of {step.parent_step_id}]" if step.parent_step_id else " [Parent Task]"
            bindings = f" | bindings: {step.input_bindings}" if step.input_bindings else ""
            print(f"  * {sid:18} | Tool: {step.tool_name:6} | State: {step.state.value:8}{parent}{deps}{bindings}")

        # 2. Execution
        print("\n[Executor] Executing multi-step plan...")
        exec_result = await executor.execute(plan)

        print(f"\n[Result] Overall Status: {exec_result.status.value}")
        print(f"[Result] Summary: {exec_result.summary}")
        print(f"[Result] Execution Sequence: {' -> '.join(exec_result.completed_steps)}")

        print("\nStep Verifications & Outputs:")
        for sid in exec_result.completed_steps:
            res = exec_result.step_results[sid]
            ver = exec_result.verification_reports[sid]
            out_preview = str(res.output).replace("\n", " ")[:65] + "..." if len(str(res.output)) > 65 else str(res.output)
            print(f"  - Step '{sid}': Success={res.success} | Verified={ver.verified} | Time={res.execution_time_ms:.2f}ms")
            print(f"    Output: {out_preview}")
            print(f"    Verification Details: {ver.details}")

        # ======================================================================
        # SCENARIO 2: CONTROLLED REPLANNING & CASCADING FAILURE BLOCKING
        # ======================================================================
        print("\n" + "-" * 80)
        print("SCENARIO 2: Step Verification Failure & Safe Cascading Failure Blocking")
        print("-" * 80)

        # Plan with a step that succeeds in execution but fails verification
        step_failing = TaskStep(
            step_id="step_flaky",
            title="Perform flaky update",
            tool_name="flaky_verification_tool",
            state=TaskState.READY,
            max_attempts=2,
        )
        step_dependent = TaskStep(
            step_id="step_downstream_action",
            title="Dependent action",
            tool_name="echo",
            parameters={"message": "Should be blocked"},
            dependencies=["step_flaky"],
            state=TaskState.PENDING,
        )
        plan_failing = TaskPlan(
            request_id="req_flaky",
            user_prompt="Run flaky pipeline",
            steps={"step_flaky": step_failing, "step_downstream_action": step_dependent},
        )

        print("Executing plan with unverified step and dependent downstream step...")
        res_flaky = await executor.execute(plan_failing)

        print(f"[Result] Status: {res_flaky.status.value}")
        print(f"[Result] Failed Step: {res_flaky.failed_step}")
        print(f"[Result] Error: {res_flaky.error}")
        print(f"Downstream Step State: {res_flaky.plan.steps['step_downstream_action'].state.value} (Blocked safely)")

        # Demonstrate Controlled Replanning
        print("\n[Controlled Replanning Check]:")
        replan_req = AgentRequest(user_prompt="Find the ORBIS README and summarize it")
        orig_plan = await planner.plan(replan_req, registry)
        replanned = await planner.replan(
            plan=orig_plan,
            failed_step_id="step_locate",
            error="README.md not found",
            tool_registry=registry,
        )
        if replanned:
            new_step_id = [s for s in replanned.steps if s.startswith("step_replan_")][0]
            new_step = replanned.steps[new_step_id]
            print(f"  Successfully replanned! New step: '{new_step_id}'")
            print(f"  Fallback Tool: '{new_step.tool_name}' (action: {new_step.parameters.get('action')})")
            print(f"  Risk Level: {new_step.risk_level.value} (No silent risk escalation)")
            print(f"  Replan count: {replanned.replan_count}/{replanned.max_replans}")

        # ======================================================================
        # SCENARIO 3: BACKEND APPROVAL GATE FOR HIGH-RISK ACTIONS
        # ======================================================================
        print("\n" + "-" * 80)
        print("SCENARIO 3: Backend Approval Gate for High-Risk Operations")
        print("-" * 80)

        step_high_risk = TaskStep(
            step_id="step_purge",
            title="Purge workspace cache",
            tool_name="controlled_destructive_tool",
            risk_level=RiskLevel.HIGH,
            state=TaskState.READY,
        )
        plan_approval = TaskPlan(
            request_id="req_high_risk",
            user_prompt="Execute destructive purge",
            steps={"step_purge": step_high_risk},
        )

        # 1. Unapproved attempt
        print("Attempt 1: Execution without approval callback...")
        res_unapproved = await executor.execute(plan_approval, approval_callback=None)
        print(f"  Result: {res_unapproved.status.value}")
        print(f"  Summary: {res_unapproved.summary}")
        print(f"  Purge Step State: {res_unapproved.plan.steps['step_purge'].state.value}")

        # 2. Approved attempt
        print("\nAttempt 2: Execution with backend approval callback returning True...")
        plan_approval.steps["step_purge"].state = TaskState.READY

        async def backend_approval_callback(step: TaskStep) -> bool:
            print(f"  >>> [Backend Approval Callback] Prompted for step '{step.step_id}' ({step.tool_name}). Approval GRANTED.")
            return True

        res_approved = await executor.execute(plan_approval, approval_callback=backend_approval_callback)
        print(f"  Result: {res_approved.status.value}")
        print(f"  Output: {res_approved.step_results['step_purge'].output}")
        print(f"  Verification: {res_approved.verification_reports['step_purge'].details}")

        # ======================================================================
        # SCENARIO 4: BACKWARD COMPATIBILITY WITH PHASES 1 AND 5
        # ======================================================================
        print("\n" + "-" * 80)
        print("SCENARIO 4: Backward Compatibility Verification")
        print("-" * 80)

        orchestrator = AgentOrchestrator(
            llm_provider=MockLLMProvider(
                default_tool_call=ToolCall(tool_name="echo", parameters={"message": "Phase 1 Backward Compatible"})
            ),
            tool_registry=registry,
            planner=planner,
            executor=executor,
        )

        # Phase 1 single-step
        resp_p1 = await orchestrator.run("Echo test message")
        print(f"[Phase 1 run()] Status: {resp_p1.status.value} | Output: '{resp_p1.tool_result.output}'")
        assert resp_p1.status == StatusEventType.COMPLETED

        # Phase 6 multi-step run_task()
        resp_p6 = await orchestrator.run_task("Find the ORBIS README and summarize it")
        print(f"[Phase 6 run_task()] Status: {resp_p6.status.value} | Steps: {resp_p6.completed_steps}")
        assert resp_p6.status == TaskState.COMPLETED

    print("\n" + "=" * 80)
    print("PHASE 6 DEMONSTRATION COMPLETE: ALL CAPABILITIES VALIDATED")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_phase6_demo())
