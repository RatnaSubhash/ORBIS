"""
Hierarchical Task Planner for ORBIS Phase 6.
Provides deterministic task decomposition by default, with optional LLM-assisted
structured planning, DAG dependency management, and safe controlled replanning.
"""

import json
import logging
import re
from typing import Dict, List, Optional
from uuid import uuid4

from core.agent.llm.base import BaseLLMProvider
from core.agent.models import (
    AgentRequest,
    TaskPlan,
    TaskState,
    TaskStep,
    TaskType,
)
from core.agent.planner.base import BasePlanner
from core.agent.planner.validator import PlanValidator
from core.continuity.service import ContinuityService
from core.tools.registry import ToolRegistry
from core.types import RiskLevel

logger = logging.getLogger("orbis.agent.planner")


class HierarchicalPlanner(BasePlanner):
    """
    Constructs and refines hierarchical task execution plans.
    Employs deterministic decomposition by default for 100% offline predictability.
    """

    def __init__(self, llm_provider: Optional[BaseLLMProvider] = None) -> None:
        self.llm_provider = llm_provider

    async def plan(self, request: AgentRequest, tool_registry: ToolRegistry) -> TaskPlan:
        """
        Decomposes an AgentRequest into a validated TaskPlan.
        Prioritizes deterministic templates; falls back to optional LLM if provided.
        """
        prompt = request.user_prompt.strip()

        # 1. Check for Continuity Request (Refinement 5)
        if ContinuityService.is_continuity_query(prompt):
            return self._plan_continuity_task(request, tool_registry)

        lower_prompt = prompt.lower()

        # 2. Check for Web Research Pattern (Phase 7)
        if (
            ("search" in lower_prompt or "web" in lower_prompt or "research" in lower_prompt or "lookup online" in lower_prompt or "find online" in lower_prompt)
            and tool_registry.get("web_research") is not None
        ):
            return self._plan_web_research_task(request, tool_registry)

        # 3. Check for Terminal Command Pattern (Phase 7)
        if (
            ("git" in lower_prompt or "pytest" in lower_prompt or "terminal" in lower_prompt or "run test" in lower_prompt or "run the test" in lower_prompt)
            and tool_registry.get("terminal") is not None
        ):
            return self._plan_terminal_task(request, tool_registry)

        # 4. Check for File Locate + Read + Summarize Pattern
        if (
            ("readme" in lower_prompt or "file" in lower_prompt or "summary" in lower_prompt)
            and ("find" in lower_prompt or "locate" in lower_prompt or "read" in lower_prompt or "prepare" in lower_prompt)
        ):
            return self._plan_file_summary_hierarchy(request, tool_registry)

        # 5. Check for Desktop Inspection Pattern
        if "desktop" in lower_prompt or "window" in lower_prompt or "process" in lower_prompt:
            return self._plan_desktop_inspection(request, tool_registry)

        # 6. Check for Echo / Message Pattern
        if "echo" in lower_prompt or "say" in lower_prompt:
            return self._plan_echo_task(request, tool_registry)

        # 5. Optional LLM Plan Generation (Refinement 1: strictly optional)
        if self.llm_provider is not None:
            try:
                llm_plan = await self._plan_with_llm(request, tool_registry)
                if llm_plan is not None:
                    return llm_plan
            except Exception as e:
                logger.warning("LLM planning failed or was rejected: %s", e)

        # 6. Default Fallback Plan
        return self._plan_default_fallback(request, tool_registry)

    async def replan(
        self,
        plan: TaskPlan,
        failed_step_id: str,
        error: str,
        tool_registry: ToolRegistry,
    ) -> Optional[TaskPlan]:
        """
        Executes controlled replanning within strict safety boundaries (Refinement 3).
        Only uses registered tools and prevents uncontrolled recursion or risk escalation.
        """
        if plan.replan_count >= plan.max_replans:
            logger.info("Plan %s reached maximum replan limit (%d).", plan.plan_id, plan.max_replans)
            return None

        failed_step = plan.steps.get(failed_step_id)
        if not failed_step:
            return None

        # Copy existing plan steps
        new_steps: Dict[str, TaskStep] = {
            k: v.model_copy(deep=True) for k, v in plan.steps.items()
        }

        # Mark the superseded step as SKIPPED so it does not fail the all_completed check
        new_steps[failed_step_id].state = TaskState.SKIPPED
        new_steps[failed_step_id].description = (
            f"{new_steps[failed_step_id].description} [Superseded by replanned step]"
        ).strip()

        # Safe controlled replanning scenario 1: File check failed, try listing directory
        if failed_step.tool_name == "file" and failed_step.parameters.get("action") == "check_exists":
            replanned_step_id = f"step_replan_{uuid4().hex[:6]}"
            replacement_tool = tool_registry.get("file")
            if replacement_tool is None:
                return None

            replacement_step = TaskStep(
                step_id=replanned_step_id,
                parent_step_id=failed_step.parent_step_id,
                title=f"Inspect directory contents as fallback for '{failed_step.parameters.get('path')}'",
                description="List directory to find alternative file match.",
                tool_name="file",
                parameters={"action": "list_directory", "path": "."},
                dependencies=[d for d in failed_step.dependencies],
                state=TaskState.READY,
                risk_level=replacement_tool.risk_level,
                requires_approval=replacement_tool.risk_level == RiskLevel.HIGH,
            )
            new_steps[replanned_step_id] = replacement_step

            # Redirect downstream dependencies and input bindings from failed_step to replanned_step
            for step in new_steps.values():
                if failed_step_id in step.dependencies:
                    step.dependencies = [
                        replanned_step_id if d == failed_step_id else d
                        for d in step.dependencies
                    ]
                if step.input_bindings:
                    updated_bindings = {}
                    for param_k, bind_expr in step.input_bindings.items():
                        if "." in bind_expr:
                            src_step, field = bind_expr.split(".", 1)
                            if src_step == failed_step_id:
                                updated_bindings[param_k] = f"{replanned_step_id}.{field}"
                            else:
                                updated_bindings[param_k] = bind_expr
                        else:
                            updated_bindings[param_k] = bind_expr
                    step.input_bindings = updated_bindings

            new_plan = TaskPlan(
                plan_id=uuid4().hex,
                request_id=plan.request_id,
                user_prompt=plan.user_prompt,
                task_type=plan.task_type,
                root_step_ids=plan.root_step_ids,
                steps=new_steps,
                max_steps=plan.max_steps,
                max_replans=plan.max_replans,
                replan_count=plan.replan_count + 1,
            )

            # Validate replanned plan structure
            errors = PlanValidator.validate_plan(new_plan, tool_registry=tool_registry)
            if errors:
                logger.warning("Replanned plan failed validation: %s", errors)
                return None

            return new_plan

        return None

    # --------------------------------------------------------------------------
    # Deterministic Planning Templates
    # --------------------------------------------------------------------------

    def _plan_continuity_task(
        self, request: AgentRequest, tool_registry: ToolRegistry
    ) -> TaskPlan:
        """Routes continuity query to Phase 5 ContinuityTool (Refinement 5)."""
        tool = tool_registry.get_or_raise("continuity_tool")
        step_id = "step_continuity"

        step = TaskStep(
            step_id=step_id,
            title="Retrieve workspace continuity context",
            description="Queries stored ORBIS sessions, Context DNA, and Knowledge Graph.",
            tool_name=tool.name,
            parameters={"query": request.user_prompt},
            dependencies=[],
            state=TaskState.READY,
            risk_level=tool.risk_level,
            requires_approval=tool.risk_level == RiskLevel.HIGH,
        )

        plan = TaskPlan(
            request_id=request.request_id,
            user_prompt=request.user_prompt,
            task_type=TaskType.CONTINUITY,
            root_step_ids=[step_id],
            steps={step_id: step},
        )
        PlanValidator.validate_plan_or_raise(plan, tool_registry=tool_registry)
        return plan

    def _plan_file_summary_hierarchy(
        self, request: AgentRequest, tool_registry: ToolRegistry
    ) -> TaskPlan:
        """Constructs a parent-child hierarchical plan: locate -> read -> summarize."""
        target_path = "README.md" if "readme" in request.user_prompt.lower() else "sample.txt"

        file_tool = tool_registry.get_or_raise("file")
        echo_tool = tool_registry.get("echo") or tool_registry.get("echo_tool") or tool_registry.get_or_raise("echo")

        # Parent composite step
        parent_id = "task_file_summary"
        step_parent = TaskStep(
            step_id=parent_id,
            title=f"Locate, read, and summarize {target_path}",
            description="Parent composite step encompassing file discovery and summarization.",
            tool_name=echo_tool.name,
            parameters={"message": f"Starting workflow for {target_path}"},
            dependencies=[],
            state=TaskState.READY,
            risk_level=RiskLevel.LOW,
        )

        # Child Step 1: Check file exists
        step_1_id = "step_locate"
        step_1 = TaskStep(
            step_id=step_1_id,
            parent_step_id=parent_id,
            title=f"Check existence of {target_path}",
            description="Verify that the target file is present on the filesystem.",
            tool_name=file_tool.name,
            parameters={"action": "check_exists", "path": target_path},
            output_key="locate_result",
            dependencies=[parent_id],
            state=TaskState.PENDING,
            risk_level=file_tool.risk_level,
        )

        # Child Step 2: Read file contents
        step_2_id = "step_read"
        step_2 = TaskStep(
            step_id=step_2_id,
            parent_step_id=parent_id,
            title=f"Read contents of {target_path}",
            description="Reads file text within allowed workspace boundary.",
            tool_name=file_tool.name,
            parameters={"action": "read_file", "path": target_path},
            input_bindings={"path": f"{step_1_id}.path"},
            output_key="read_result",
            dependencies=[step_1_id],
            state=TaskState.PENDING,
            risk_level=file_tool.risk_level,
        )

        # Child Step 3: Produce summary
        step_3_id = "step_summarize"
        step_3 = TaskStep(
            step_id=step_3_id,
            parent_step_id=parent_id,
            title=f"Summarize contents of {target_path}",
            description="Formats and outputs the summarized content.",
            tool_name=echo_tool.name,
            parameters={"message": f"Summary of {target_path}:"},
            input_bindings={"message": f"{step_2_id}.content"},
            dependencies=[step_2_id],
            state=TaskState.PENDING,
            risk_level=echo_tool.risk_level,
        )

        steps = {
            parent_id: step_parent,
            step_1_id: step_1,
            step_2_id: step_2,
            step_3_id: step_3,
        }

        plan = TaskPlan(
            request_id=request.request_id,
            user_prompt=request.user_prompt,
            task_type=TaskType.HIERARCHICAL,
            root_step_ids=[parent_id],
            steps=steps,
        )
        PlanValidator.validate_plan_or_raise(plan, tool_registry=tool_registry)
        return plan

    def _plan_desktop_inspection(
        self, request: AgentRequest, tool_registry: ToolRegistry
    ) -> TaskPlan:
        """Constructs a plan for desktop inspection."""
        tool = tool_registry.get_or_raise("desktop_tool")
        action = "inspect_running_processes" if "process" in request.user_prompt.lower() else "inspect_active_window"
        step_id = "step_inspect_desktop"

        step = TaskStep(
            step_id=step_id,
            title=f"Desktop Inspection ({action})",
            description="Reads safe active desktop or process metadata.",
            tool_name=tool.name,
            parameters={"action": action},
            dependencies=[],
            state=TaskState.READY,
            risk_level=tool.risk_level,
        )

        plan = TaskPlan(
            request_id=request.request_id,
            user_prompt=request.user_prompt,
            task_type=TaskType.SINGLE_STEP,
            root_step_ids=[step_id],
            steps={step_id: step},
        )
        PlanValidator.validate_plan_or_raise(plan, tool_registry=tool_registry)
        return plan

    def _plan_echo_task(self, request: AgentRequest, tool_registry: ToolRegistry) -> TaskPlan:
        """Constructs a plan for echo / message printing."""
        tool = tool_registry.get("echo") or tool_registry.get("echo_tool") or tool_registry.get_or_raise("echo")
        msg = request.user_prompt.replace("echo", "").replace("say", "").strip() or "Hello from ORBIS"
        step_id = "step_echo"

        step = TaskStep(
            step_id=step_id,
            title="Echo Message",
            description="Prints message via echo tool.",
            tool_name=tool.name,
            parameters={"message": msg},
            dependencies=[],
            state=TaskState.READY,
            risk_level=tool.risk_level,
        )

        plan = TaskPlan(
            request_id=request.request_id,
            user_prompt=request.user_prompt,
            task_type=TaskType.SINGLE_STEP,
            root_step_ids=[step_id],
            steps={step_id: step},
        )
        PlanValidator.validate_plan_or_raise(plan, tool_registry=tool_registry)
        return plan

    def _plan_web_research_task(
        self, request: AgentRequest, tool_registry: ToolRegistry
    ) -> TaskPlan:
        """Constructs a plan for web research."""
        web_tool = tool_registry.get_or_raise("web_research")
        echo_tool = tool_registry.get("echo") or tool_registry.get("echo_tool")

        prompt = request.user_prompt
        lower = prompt.lower()

        # Extract search query
        query = prompt
        for prefix in (
            "search the web for", "search for", "search web for",
            "research on", "research", "lookup online for",
            "find online information about", "find online",
        ):
            if lower.startswith(prefix):
                query = prompt[len(prefix):].strip()
                break

        # Check if user asked to summarize
        wants_summary = "summarize" in lower or "summary" in lower
        if wants_summary and "and summarize" in query.lower():
            query = re.split(r"(?i)\s+and\s+summarize", query)[0].strip()

        clean_query = query.strip(" .?!'\"") or "ORBIS architecture"

        step_search_id = "step_web_research"
        step_search = TaskStep(
            step_id=step_search_id,
            title=f"Web research on '{clean_query}'",
            description="Searches web and extracts relevant readable text.",
            tool_name=web_tool.name,
            parameters={"action": "research", "query": clean_query},
            output_key="research_output",
            dependencies=[],
            state=TaskState.READY,
            risk_level=web_tool.risk_level,
        )

        steps = {step_search_id: step_search}
        root_ids = [step_search_id]

        if wants_summary and echo_tool is not None:
            step_sum_id = "step_summarize_research"
            step_sum = TaskStep(
                step_id=step_sum_id,
                title=f"Summarize research for '{clean_query}'",
                description="Summarizes extracted web research findings.",
                tool_name=echo_tool.name,
                parameters={"message": f"Summary of findings for {clean_query}:"},
                dependencies=[step_search_id],
                state=TaskState.PENDING,
                risk_level=echo_tool.risk_level,
            )
            steps[step_sum_id] = step_sum
            task_type = TaskType.SEQUENTIAL
        else:
            task_type = TaskType.SINGLE_STEP

        plan = TaskPlan(
            request_id=request.request_id,
            user_prompt=request.user_prompt,
            task_type=task_type,
            root_step_ids=root_ids,
            steps=steps,
        )
        PlanValidator.validate_plan_or_raise(plan, tool_registry=tool_registry)
        return plan

    def _plan_terminal_task(
        self, request: AgentRequest, tool_registry: ToolRegistry
    ) -> TaskPlan:
        """Constructs a plan for controlled terminal execution."""
        term_tool = tool_registry.get_or_raise("terminal")
        echo_tool = tool_registry.get("echo") or tool_registry.get("echo_tool")

        lower = request.user_prompt.lower()

        if "status" in lower and "git" in lower:
            cmd = "git"
            args = ["status", "--short"]
            title = "Check git repository status"
        elif "log" in lower and "git" in lower:
            cmd = "git"
            args = ["log", "-n", "5", "--oneline"]
            title = "Check recent git log entries"
        elif "clean" in lower and "git" in lower:
            cmd = "git"
            args = ["clean", "-fd"]
            title = "Clean untracked files (High Risk)"
        elif "test" in lower or "pytest" in lower:
            cmd = "pytest"
            args = ["-v", "tests/test_tools.py"]
            title = "Run automated test suite"
        elif "python" in lower and "version" in lower:
            cmd = "python"
            args = ["--version"]
            title = "Inspect Python runtime version"
        else:
            cmd = "git"
            args = ["--version"]
            title = "Inspect Git version"

        effective_risk = getattr(term_tool, "get_effective_risk", lambda p: term_tool.risk_level)(
            {"command": cmd, "args": args}
        )

        step_term_id = "step_terminal_cmd"
        step_term = TaskStep(
            step_id=step_term_id,
            title=title,
            description=f"Executes controlled terminal command '{cmd} {' '.join(args)}'.",
            tool_name=term_tool.name,
            parameters={"command": cmd, "args": args},
            output_key="terminal_output",
            dependencies=[],
            state=TaskState.READY,
            risk_level=effective_risk,
            requires_approval=effective_risk == RiskLevel.HIGH,
        )

        steps = {step_term_id: step_term}
        root_ids = [step_term_id]

        if ("tell me" in lower or "report" in lower or "summarize" in lower) and echo_tool is not None:
            step_report_id = "step_report_terminal"
            step_report = TaskStep(
                step_id=step_report_id,
                title="Report terminal outcome",
                description="Reports process execution output.",
                tool_name=echo_tool.name,
                parameters={"message": f"Execution result for '{cmd}':"},
                dependencies=[step_term_id],
                state=TaskState.PENDING,
                risk_level=echo_tool.risk_level,
            )
            steps[step_report_id] = step_report
            task_type = TaskType.SEQUENTIAL
        else:
            task_type = TaskType.SINGLE_STEP

        plan = TaskPlan(
            request_id=request.request_id,
            user_prompt=request.user_prompt,
            task_type=task_type,
            root_step_ids=root_ids,
            steps=steps,
        )
        PlanValidator.validate_plan_or_raise(plan, tool_registry=tool_registry)
        return plan

    def _plan_default_fallback(
        self, request: AgentRequest, tool_registry: ToolRegistry
    ) -> TaskPlan:
        """Fallback for requests that don't match explicit patterns."""
        tool = tool_registry.get("echo") or tool_registry.get("echo_tool") or tool_registry.list_tools()[0]
        step_id = "step_default"

        step = TaskStep(
            step_id=step_id,
            title="Execute default action",
            description="Fallback single step execution.",
            tool_name=tool.name,
            parameters={"message": request.user_prompt} if tool.name in ("echo", "echo_tool") else {},
            dependencies=[],
            state=TaskState.READY,
            risk_level=tool.risk_level,
        )

        plan = TaskPlan(
            request_id=request.request_id,
            user_prompt=request.user_prompt,
            task_type=TaskType.SINGLE_STEP,
            root_step_ids=[step_id],
            steps={step_id: step},
        )
        PlanValidator.validate_plan_or_raise(plan, tool_registry=tool_registry)
        return plan

    async def _plan_with_llm(
        self, request: AgentRequest, tool_registry: ToolRegistry
    ) -> Optional[TaskPlan]:
        """
        Optional structured plan generation via LLM.
        Strictly validated against schema and registered tools.
        """
        if self.llm_provider is None:
            return None

        try:
            raw_response = await self.llm_provider.generate(
                prompt=f"Create a plan for: {request.user_prompt}",
                system_prompt="Return a JSON task plan with steps and dependencies.",
            )
        except Exception as e:
            logger.warning("LLM generation exception: %s", e)
            return None

        if not raw_response:
            return None

        try:
            parsed = json.loads(raw_response)
            plan = TaskPlan.model_validate(parsed)
            PlanValidator.validate_plan_or_raise(plan, tool_registry=tool_registry)
            return plan
        except Exception as err:
            logger.warning("LLM generated invalid plan: %s", err)
            return None
