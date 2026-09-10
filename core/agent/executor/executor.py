"""
Multi-Step Task Executor for ORBIS Phase 6.
Executes hierarchical, sequential, and dependent tasks with strict step verification,
topological dependency resolution, human-in-the-loop approval gates, and bounded replanning.
"""

import inspect
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from core.agent.executor.context import TaskExecutionContext
from core.agent.models import (
    AgentExecutionResult,
    StepResult,
    StepVerification,
    TaskPlan,
    TaskState,
    TaskStep,
)
from core.agent.planner.base import BasePlanner
from core.agent.planner.validator import PlanValidator
from core.tools.registry import ToolRegistry
from core.types import ExecutionContext, RiskLevel

logger = logging.getLogger("orbis.agent.executor")

ApprovalCallback = Callable[[TaskStep], Union[bool, Awaitable[bool]]]


class TaskExecutor:
    """
    Executes a TaskPlan sequentially in topological dependency order.
    Enforces independent verification, approval gates, and bounded limits.
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        planner: Optional[BasePlanner] = None,
        max_steps: int = 10,
        max_attempts_per_step: int = 2,
        max_replans: int = 2,
    ) -> None:
        self.tool_registry = tool_registry
        self.planner = planner
        self.max_steps = max_steps
        self.max_attempts_per_step = max_attempts_per_step
        self.max_replans = max_replans

    def _cascade_block(self, plan: TaskPlan, failed_step_id: str) -> None:
        """Recursively marks all downstream dependent steps as BLOCKED."""
        blocked_set = {failed_step_id}
        changed = True
        while changed:
            changed = False
            for step in plan.steps.values():
                if step.step_id not in blocked_set and step.state in (TaskState.PENDING, TaskState.READY):
                    if any(dep in blocked_set for dep in step.dependencies):
                        step.state = TaskState.BLOCKED
                        blocked_set.add(step.step_id)
                        changed = True

    async def _handle_approval(
        self, step: TaskStep, approval_callback: Optional[ApprovalCallback]
    ) -> bool:
        """Invokes the backend approval callback if provided."""
        if approval_callback is None:
            return False

        try:
            res = approval_callback(step)
            if inspect.isawaitable(res):
                return await res
            return bool(res)
        except Exception as e:
            logger.warning("Approval callback raised exception: %s", e)
            return False

    async def execute(
        self,
        plan: TaskPlan,
        context: Optional[ExecutionContext] = None,
        approval_callback: Optional[ApprovalCallback] = None,
    ) -> AgentExecutionResult:
        """
        Executes a validated TaskPlan to completion or safe failure.
        """
        exec_ctx = context or ExecutionContext()
        task_context = TaskExecutionContext()

        # Validate plan structure before execution
        PlanValidator.validate_plan_or_raise(plan, tool_registry=self.tool_registry)

        completed_steps: List[str] = []
        failed_step: Optional[str] = None
        failure_error: Optional[str] = None
        step_results: Dict[str, StepResult] = {}
        verification_reports: Dict[str, StepVerification] = {}

        total_executed_steps = 0
        execution_limit = min(plan.max_steps, self.max_steps)

        while True:
            # 1. Update state of PENDING steps based on dependencies
            progress_possible = False

            for step in plan.steps.values():
                if step.state == TaskState.PENDING:
                    # Check dependencies
                    deps = [plan.steps[d] for d in step.dependencies if d in plan.steps]
                    if any(d.state in (TaskState.FAILED, TaskState.BLOCKED, TaskState.CANCELLED) for d in deps):
                        step.state = TaskState.BLOCKED
                    elif all(d.state == TaskState.COMPLETED for d in deps):
                        step.state = TaskState.READY
                        progress_possible = True

            # 2. Pick the next READY step in deterministic plan order
            ready_step: Optional[TaskStep] = None
            for sid, step in plan.steps.items():
                if step.state == TaskState.READY:
                    ready_step = step
                    break

            if ready_step is None:
                # No more steps ready to execute
                break

            # 3. Check step execution bounds
            if total_executed_steps >= execution_limit:
                failed_step = ready_step.step_id
                failure_error = f"Execution exceeded maximum allowed steps ({execution_limit})."
                ready_step.state = TaskState.BLOCKED
                self._cascade_block(plan, ready_step.step_id)
                break

            # 4. Safety Boundary & Authoritative Tool Approval Check (Fix 1)
            tool = self.tool_registry.get_or_raise(ready_step.tool_name)
            is_approval_needed = (
                ready_step.requires_approval
                or ready_step.risk_level == RiskLevel.HIGH
                or tool.risk_level == RiskLevel.HIGH
            )
            if is_approval_needed:
                ready_step.state = TaskState.BLOCKED_PENDING_APPROVAL
                approved = await self._handle_approval(ready_step, approval_callback)

                if approved:
                    ready_step.state = TaskState.READY
                else:
                    failed_step = ready_step.step_id
                    failure_error = f"Step '{ready_step.step_id}' requires explicit approval before execution."
                    self._cascade_block(plan, ready_step.step_id)
                    return AgentExecutionResult(
                        task_id=exec_ctx.task_id,
                        request=plan.user_prompt,
                        status=TaskState.BLOCKED_PENDING_APPROVAL,
                        summary=f"Execution halted: step '{ready_step.step_id}' ({ready_step.tool_name}) requires approval.",
                        plan=plan,
                        completed_steps=completed_steps,
                        failed_step=failed_step,
                        error=failure_error,
                        step_results=step_results,
                        verification_reports=verification_reports,
                    )

            # 5. Execute the step
            ready_step.state = TaskState.RUNNING
            ready_step.attempts += 1
            total_executed_steps += 1
            t_start = time.perf_counter()

            resolved_params = task_context.resolve_bindings(ready_step)

            try:
                validated_params = tool.validate_parameters(resolved_params)
                tool_result = await tool.execute(validated_params, exec_ctx)
                t_end = time.perf_counter()

                step_res = StepResult(
                    step_id=ready_step.step_id,
                    tool_name=ready_step.tool_name,
                    success=tool_result.success,
                    output=tool_result.output,
                    error=tool_result.error,
                    execution_time_ms=(t_end - t_start) * 1000.0,
                    metadata=tool_result.metadata,
                )
            except Exception as ex:
                t_end = time.perf_counter()
                step_res = StepResult(
                    step_id=ready_step.step_id,
                    tool_name=ready_step.tool_name,
                    success=False,
                    error=f"Step execution exception: {str(ex)}",
                    execution_time_ms=(t_end - t_start) * 1000.0,
                )

            step_results[ready_step.step_id] = step_res

            # 6. Step Verification (Mandatory contract)
            if step_res.success:
                try:
                    ver_report = await tool.verify(validated_params, tool_result, exec_ctx)
                    step_ver = StepVerification(
                        step_id=ready_step.step_id,
                        verified=ver_report.verified,
                        details=ver_report.details,
                        discrepancies=ver_report.discrepancies,
                    )
                except Exception as ver_ex:
                    step_ver = StepVerification(
                        step_id=ready_step.step_id,
                        verified=False,
                        details=f"Verification exception: {str(ver_ex)}",
                        discrepancies=[str(ver_ex)],
                    )
            else:
                step_ver = StepVerification(
                    step_id=ready_step.step_id,
                    verified=False,
                    details="Step failed execution; verification failed by definition.",
                    discrepancies=[step_res.error or "Execution failed"],
                )

            verification_reports[ready_step.step_id] = step_ver

            # 7. Evaluate Execution & Verification Outcome
            if step_res.success and step_ver.verified:
                ready_step.state = TaskState.COMPLETED
                completed_steps.append(ready_step.step_id)
                task_context.store_output(ready_step.step_id, step_res.output, ready_step.output_key)
            else:
                # Step failed execution or verification
                if ready_step.attempts < ready_step.max_attempts:
                    logger.info("Retrying step %s (attempt %d/%d)", ready_step.step_id, ready_step.attempts, ready_step.max_attempts)
                    ready_step.state = TaskState.READY
                    continue

                ready_step.state = TaskState.FAILED
                failed_step = ready_step.step_id
                failure_error = step_res.error or step_ver.details

                # Attempt Controlled Replanning if configured (Refinement 3 & Fix 2)
                if self.planner and plan.replan_count < plan.max_replans:
                    logger.info("Initiating controlled replan for failed step %s", ready_step.step_id)
                    replanned = self.planner.replan(
                        plan=plan,
                        failed_step_id=ready_step.step_id,
                        error=failure_error,
                        tool_registry=self.tool_registry,
                    )
                    if inspect.isawaitable(replanned):
                        replanned = await replanned

                    if replanned is not None:
                        logger.info("Plan successfully replanned; resetting failure tracking and resuming execution.")
                        plan = replanned
                        failed_step = None
                        failure_error = None
                        continue

                # If no replan was possible, cascade failure
                self._cascade_block(plan, ready_step.step_id)
                break

        # 8. Determine Overall Status
        all_completed = all(
            s.state == TaskState.COMPLETED
            for s in plan.steps.values()
            if s.state != TaskState.SKIPPED
        )

        final_status = (
            TaskState.COMPLETED
            if all_completed and not failed_step
            else TaskState.FAILED
        )

        final_output = None
        if completed_steps:
            last_step_id = completed_steps[-1]
            final_output = task_context.get_output(last_step_id)

        summary = (
            f"Multi-step task completed successfully ({len(completed_steps)}/{len(plan.steps)} steps)."
            if final_status == TaskState.COMPLETED
            else f"Multi-step task failed at step '{failed_step}': {failure_error}"
        )

        return AgentExecutionResult(
            task_id=exec_ctx.task_id,
            request=plan.user_prompt,
            status=final_status,
            summary=summary,
            plan=plan,
            completed_steps=completed_steps,
            failed_step=failed_step,
            error=failure_error,
            step_results=step_results,
            verification_reports=verification_reports,
            final_output=final_output,
        )
