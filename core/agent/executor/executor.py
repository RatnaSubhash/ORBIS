"""
Multi-Step Task Executor for ORBIS Phase 6.
Executes hierarchical, sequential, and dependent tasks with strict step verification,
topological dependency resolution, human-in-the-loop approval gates, and bounded replanning.
"""

import inspect
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from pydantic import ValidationError
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
from core.events.models import AgentEventType
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
        event_bus: Optional[Any] = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.planner = planner
        self.max_steps = max_steps
        self.max_attempts_per_step = max_attempts_per_step
        self.max_replans = max_replans
        self.event_bus = event_bus

    def _emit_step_event(
        self,
        bus: Optional[Any],
        event_type: AgentEventType,
        task_id: str,
        correlation_id: str,
        safe_user_message: str,
        status: str,
        step_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        risk_level: Optional[RiskLevel] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Publishes a non-blocking step lifecycle event to the event bus if configured."""
        if bus is None:
            return
        try:
            from core.events.sanitizer import EventSanitizer
            event = EventSanitizer.create_safe_event(
                event_type=event_type,
                task_id=task_id,
                correlation_id=correlation_id,
                safe_user_message=safe_user_message,
                status=status,
                step_id=step_id,
                tool_name=tool_name,
                risk_level=risk_level,
                metadata=metadata or {},
            )
            bus.publish_nowait(event)
        except Exception as e:
            logger.warning("Failed to emit step event %s: %s", event_type, e)

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
        event_bus: Optional[Any] = None,
    ) -> AgentExecutionResult:
        """
        Executes a validated TaskPlan to completion or safe failure.
        """
        exec_ctx = context or ExecutionContext()
        task_context = TaskExecutionContext()
        bus = event_bus if event_bus is not None else self.event_bus

        # Validate plan structure before execution
        PlanValidator.validate_plan_or_raise(plan, tool_registry=self.tool_registry)

        if not plan.steps:
            return AgentExecutionResult(
                task_id=exec_ctx.task_id,
                request=plan.user_prompt,
                status=TaskState.COMPLETED,
                summary="Request analyzed; no tool required.",
                plan=plan,
                completed_steps=[],
                step_results={},
                verification_reports={},
            )

        return await self._execute_loop(
            plan=plan,
            exec_ctx=exec_ctx,
            task_context=task_context,
            completed_steps=[],
            step_results={},
            approval_callback=approval_callback,
            bus=bus,
        )

    async def resume(
        self,
        plan: TaskPlan,
        approved_step_id: str,
        context: Optional[ExecutionContext] = None,
        approval_callback: Optional[ApprovalCallback] = None,
        initial_step_results: Optional[Dict[str, StepResult]] = None,
        event_bus: Optional[Any] = None,
    ) -> AgentExecutionResult:
        """
        Resumes execution of a previously blocked TaskPlan from an approved step.
        Does not re-execute steps that are already COMPLETED.
        """
        exec_ctx = context or ExecutionContext(task_id=plan.request_id)
        task_context = TaskExecutionContext()
        bus = event_bus if event_bus is not None else self.event_bus

        # Seed task_context with outputs of already completed steps
        completed_step_ids = [
            sid for sid, s in plan.steps.items() if s.state == TaskState.COMPLETED
        ]
        if initial_step_results:
            for sid, sres in initial_step_results.items():
                if sid in plan.steps and plan.steps[sid].state == TaskState.COMPLETED:
                    task_context.store_output(sid, sres.output, plan.steps[sid].output_key)

        # Unblock the approved step
        if approved_step_id in plan.steps:
            target_step = plan.steps[approved_step_id]
            target_step.state = TaskState.READY
            target_step.requires_approval = False

        # Reset any cascading BLOCKED downstream steps back to PENDING
        for step in plan.steps.values():
            if step.step_id != approved_step_id and step.state == TaskState.BLOCKED:
                step.state = TaskState.PENDING

        # Validate plan structure before continuing execution
        PlanValidator.validate_plan_or_raise(plan, tool_registry=self.tool_registry)

        # Scoped approval callback strictly authorizing only approved_step_id
        def scoped_approval(step: TaskStep) -> bool:
            if approval_callback is not None:
                res = approval_callback(step)
                return bool(res)
            return step.step_id == approved_step_id

        return await self._execute_loop(
            plan=plan,
            exec_ctx=exec_ctx,
            task_context=task_context,
            completed_steps=completed_step_ids,
            step_results=dict(initial_step_results or {}),
            approval_callback=scoped_approval,
            bus=bus,
        )

    async def _execute_loop(
        self,
        plan: TaskPlan,
        exec_ctx: ExecutionContext,
        task_context: TaskExecutionContext,
        completed_steps: List[str],
        step_results: Dict[str, StepResult],
        approval_callback: Optional[ApprovalCallback] = None,
        bus: Optional[Any] = None,
    ) -> AgentExecutionResult:
        """Core topological execution and verification loop."""
        failed_step: Optional[str] = None
        failure_error: Optional[str] = None
        verification_reports: Dict[str, StepVerification] = {}

        total_executed_steps = 0
        execution_limit = min(plan.max_steps, self.max_steps)
        correlation_id = exec_ctx.metadata.get("correlation_id") or f"corr_{exec_ctx.task_id}"

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
                self._emit_step_event(
                    bus,
                    AgentEventType.STEP_FAILED,
                    task_id=exec_ctx.task_id,
                    correlation_id=correlation_id,
                    safe_user_message=failure_error,
                    status="BLOCKED",
                    step_id=ready_step.step_id,
                    tool_name=ready_step.tool_name,
                    risk_level=ready_step.risk_level,
                    metadata={"error": failure_error},
                )
                break

            # 4. Safety Boundary & Authoritative Tool Approval Check (Fix 1 & Phase 7 Dynamic Risk)
            tool = self.tool_registry.get_or_raise(ready_step.tool_name)
            effective_tool_risk = getattr(tool, "get_effective_risk", lambda p: tool.risk_level)(ready_step.parameters)
            if effective_tool_risk == RiskLevel.HIGH:
                ready_step.risk_level = RiskLevel.HIGH

            is_approval_needed = (
                ready_step.requires_approval
                or ready_step.risk_level == RiskLevel.HIGH
                or tool.risk_level == RiskLevel.HIGH
                or effective_tool_risk == RiskLevel.HIGH
            )
            if is_approval_needed:
                ready_step.state = TaskState.BLOCKED_PENDING_APPROVAL
                approval_msg = ready_step.approval_prompt or f"Execution halted: step '{ready_step.step_id}' ({ready_step.tool_name}) requires approval."
                self._emit_step_event(
                    bus,
                    AgentEventType.APPROVAL_REQUIRED,
                    task_id=exec_ctx.task_id,
                    correlation_id=correlation_id,
                    safe_user_message=approval_msg,
                    status="BLOCKED_PENDING_APPROVAL",
                    step_id=ready_step.step_id,
                    tool_name=ready_step.tool_name,
                    risk_level=ready_step.risk_level,
                    metadata={"tool_name": ready_step.tool_name, "parameters": ready_step.parameters},
                )
                approved = await self._handle_approval(ready_step, approval_callback)

                if approved:
                    ready_step.state = TaskState.READY
                else:
                    failed_step = ready_step.step_id
                    failure_error = ready_step.approval_prompt or f"Step '{ready_step.step_id}' requires explicit approval before execution."
                    self._cascade_block(plan, ready_step.step_id)
                    return AgentExecutionResult(
                        task_id=exec_ctx.task_id,
                        request=plan.user_prompt,
                        status=TaskState.BLOCKED_PENDING_APPROVAL,
                        summary=approval_msg,
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

            self._emit_step_event(
                bus,
                AgentEventType.STEP_STARTED,
                task_id=exec_ctx.task_id,
                correlation_id=correlation_id,
                safe_user_message=f"Starting step '{ready_step.step_id}': {ready_step.title or ready_step.tool_name}.",
                status="RUNNING",
                step_id=ready_step.step_id,
                tool_name=ready_step.tool_name,
                risk_level=ready_step.risk_level,
                metadata={"title": ready_step.title, "attempt": ready_step.attempts},
            )

            resolved_params = task_context.resolve_bindings(ready_step)

            try:
                validated_params = tool.validate_parameters(resolved_params)
            except ValidationError as val_ex:
                t_end = time.perf_counter()
                step_res = StepResult(
                    step_id=ready_step.step_id,
                    tool_name=ready_step.tool_name,
                    success=False,
                    error=f"Parameter validation failed: {str(val_ex)}",
                    execution_time_ms=(t_end - t_start) * 1000.0,
                )
                step_results[ready_step.step_id] = step_res
                ready_step.state = TaskState.FAILED
                failed_step = ready_step.step_id
                failure_error = step_res.error
                self._emit_step_event(
                    bus,
                    AgentEventType.STEP_FAILED,
                    task_id=exec_ctx.task_id,
                    correlation_id=correlation_id,
                    safe_user_message=f"Step '{ready_step.step_id}' parameter validation failed.",
                    status="FAILED",
                    step_id=ready_step.step_id,
                    tool_name=ready_step.tool_name,
                    risk_level=ready_step.risk_level,
                    metadata={"error": step_res.error},
                )
                self._cascade_block(plan, ready_step.step_id)
                break

            try:
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
            self._emit_step_event(
                bus,
                AgentEventType.VERIFICATION_STARTED,
                task_id=exec_ctx.task_id,
                correlation_id=correlation_id,
                safe_user_message=f"Verifying step '{ready_step.step_id}' execution.",
                status="VERIFYING",
                step_id=ready_step.step_id,
                tool_name=ready_step.tool_name,
                risk_level=ready_step.risk_level,
            )

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

            self._emit_step_event(
                bus,
                AgentEventType.VERIFICATION_COMPLETED,
                task_id=exec_ctx.task_id,
                correlation_id=correlation_id,
                safe_user_message=f"Verification for step '{ready_step.step_id}' completed (verified={step_ver.verified}).",
                status="VERIFIED" if step_ver.verified else "VERIFICATION_FAILED",
                step_id=ready_step.step_id,
                tool_name=ready_step.tool_name,
                risk_level=ready_step.risk_level,
                metadata={"verified": step_ver.verified, "details": step_ver.details},
            )

            # 7. Evaluate Execution & Verification Outcome
            if step_res.success and step_ver.verified:
                ready_step.state = TaskState.COMPLETED
                completed_steps.append(ready_step.step_id)
                task_context.store_output(ready_step.step_id, step_res.output, ready_step.output_key)
                self._emit_step_event(
                    bus,
                    AgentEventType.STEP_COMPLETED,
                    task_id=exec_ctx.task_id,
                    correlation_id=correlation_id,
                    safe_user_message=f"Step '{ready_step.step_id}' completed successfully.",
                    status="COMPLETED",
                    step_id=ready_step.step_id,
                    tool_name=ready_step.tool_name,
                    risk_level=ready_step.risk_level,
                    metadata={"execution_time_ms": step_res.execution_time_ms},
                )
            else:
                # Step failed execution or verification
                if ready_step.attempts < ready_step.max_attempts:
                    logger.info("Retrying step %s (attempt %d/%d)", ready_step.step_id, ready_step.attempts, ready_step.max_attempts)
                    ready_step.state = TaskState.READY
                    continue

                ready_step.state = TaskState.FAILED
                failed_step = ready_step.step_id
                failure_error = step_res.error or step_ver.details

                self._emit_step_event(
                    bus,
                    AgentEventType.STEP_FAILED,
                    task_id=exec_ctx.task_id,
                    correlation_id=correlation_id,
                    safe_user_message=f"Step '{ready_step.step_id}' failed: {failure_error}.",
                    status="FAILED",
                    step_id=ready_step.step_id,
                    tool_name=ready_step.tool_name,
                    risk_level=ready_step.risk_level,
                    metadata={"error": failure_error},
                )

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
