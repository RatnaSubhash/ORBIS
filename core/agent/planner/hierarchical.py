"""
Hierarchical Task Planner for ORBIS Phase 6.
Provides deterministic task decomposition by default, with optional LLM-assisted
structured planning, DAG dependency management, and safe controlled replanning.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
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


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """
    Defensively extracts and parses a JSON object from text.
    Handles:
    - Pure JSON
    - Markdown code fences (```json ... ``` or ``` ... ```)
    - Conversational preamble or postscript surrounding a JSON object
    Returns a dictionary if successfully parsed, or None if invalid.
    """
    if not text or not text.strip():
        return None

    cleaned = text.strip()

    # Fast path 1: direct parse
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    # Fast path 2: stripped backticks or markdown fences
    candidate = cleaned
    if candidate.startswith("```json"):
        candidate = candidate[7:]
    elif candidate.startswith("```"):
        candidate = candidate[3:]
    if candidate.endswith("```"):
        candidate = candidate[:-3]
    candidate = candidate.strip("` \t\r\n")

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    # Balanced bracket extraction for preamble / postscript text
    depth = 0
    in_string = False
    escape = False
    start_idx = -1

    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                if depth == 0:
                    start_idx = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start_idx != -1:
                        sub = text[start_idx : i + 1]
                        try:
                            parsed = json.loads(sub)
                            if isinstance(parsed, dict):
                                return parsed
                        except Exception:
                            pass

    # Fallback: first '{' to last '}'
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        sub = text[first_brace : last_brace + 1]
        try:
            parsed = json.loads(sub)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    return None


def is_ambiguous_or_malformed_query(prompt: str) -> Tuple[bool, Optional[str]]:
    """
    Deterministically detects malformed or ambiguous voice transcripts
    (e.g., repeated question prefixes, stuttered phrases, broken sentence fragments,
    or nonsensical adjective entity targets in office-holder queries)
    that should trigger a clarification response rather than guessing or web research.
    Returns (is_ambiguous, clarification_message).
    """
    if not prompt or not prompt.strip():
        return True, "I didn't hear a query. Could you please repeat your request?"

    cleaned = prompt.strip()
    lower = cleaned.lower()

    # 1. Repeated question prefixes / stuttering at query start
    # e.g. "who is who is", "what is what is", "tell me tell me", "search for search for", "where is where is"
    repeated_prefix_pattern = re.compile(
        r"^(who\s+is\s+who\s+is|what\s+is\s+what\s+is|where\s+is\s+where\s+is|"
        r"tell\s+me\s+tell\s+me|search\s+for\s+search\s+for|how\s+much\s+is\s+how\s+much\s+is)\b",
        re.IGNORECASE,
    )
    if repeated_prefix_pattern.search(lower):
        return True, "I heard a repeated phrase in your request. Could you please clarify what you would like to know?"

    # 2. Incomplete queries ending abruptly with a trailing preposition or article
    stripped_punct = lower.rstrip(".?! \t\r\n")
    trailing_dangling_pattern = re.compile(
        r"\b(?:of|for|about|in|at|to|by|from|between|with|the|a|an)\s*$",
        re.IGNORECASE,
    )
    has_question_starter = bool(
        re.search(r"^(who|what|where|when|which|how|tell\s+me|search|find|current|latest)\b", stripped_punct)
    )
    if has_question_starter and trailing_dangling_pattern.search(stripped_punct):
        return True, "Your question seems incomplete. Could you please specify what entity or topic you are asking about?"

    # 3. Malformed office targets with musical/adjective terms (e.g., "chief minister of the melodic")
    office_adjective_pattern = re.compile(
        r"\b(?:chief\s+minister|prime\s+minister|president|governor|mayor)\s+of\s+(?:the\s+)?(melodic|rhythmic|harmonic|musical|lyrical|symphonic|loud|quiet|tasty|fragrant|blue|green|yellow)\b",
        re.IGNORECASE,
    )
    if office_adjective_pattern.search(lower):
        return True, "I could not identify the state, region, or organization in your question. Could you please clarify who you are asking about?"

    # 4. Bare tautological queries e.g. "who is who", "what is what"
    bare_tautology = re.compile(r"^(?:who\s+is\s+who|what\s+is\s+what)\s*[.?!]?$", re.IGNORECASE)
    if bare_tautology.search(lower):
        return True, "Could you please specify the exact person or topic you would like to know about?"

    return False, None


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

        # 1. Check for Intent Continuity Request (Phase 9 ICE)
        if ContinuityService.is_intent_query(prompt) and (
            tool_registry.get("intent_continuity") is not None
            or tool_registry.get("intent_continuity_tool") is not None
        ):
            return self._plan_intent_continuity_task(request, tool_registry)

        # 2. Check for Descriptive Continuity Request (Phase 5)
        if ContinuityService.is_continuity_query(prompt):
            return self._plan_continuity_task(request, tool_registry)

        lower_prompt = prompt.lower()

        # Check for Workspace Restoration Request (Phase 10)
        if (
            ("restore" in lower_prompt or "reopen" in lower_prompt)
            and ("workspace" in lower_prompt or "windows" in lower_prompt or "layout" in lower_prompt or "session" in lower_prompt or "apps" in lower_prompt)
            and tool_registry.get("restoration_tool") is not None
        ):
            return self._plan_restoration_task(request, tool_registry)

        # Ambiguity Check: Do not guess or search when transcript contains obviously malformed or ambiguous entity
        is_ambiguous, clarification_msg = is_ambiguous_or_malformed_query(prompt)
        if is_ambiguous and clarification_msg:
            return self._plan_clarification_task(request, tool_registry, clarification_msg)

        # Document creation intent signals
        has_creation_verb = any(
            v in lower_prompt
            for v in ("create", "write", "generate", "make", "save", "produce", "compile", "draft", "prepare")
        )
        has_doc_target = any(
            t in lower_prompt
            for t in (
                "report", "document", "readme", "markdown", ".md", ".txt", "text file",
                "file", "study notes", "notes", "recommendations", "recommendation"
            )
        )
        has_doc_creation_intent = has_creation_verb and has_doc_target

        has_research_signals = any(
            r in lower_prompt
            for r in (
                "search", "research", "find online", "lookup online", "lookup", "explore",
                "investigate", "compare", "find three", "find 3", "find some", "find good",
                "good computer network", "good project ideas"
            )
        ) or bool(re.search(r"\bfind\s+(?:\w+\s+)*(?:ideas|options|projects|topics|information)\b", lower_prompt))

        # 2. Check for Research + Document Creation Pipeline (Phase 8 Multi-Step Pipeline)
        if (
            has_doc_creation_intent
            and has_research_signals
            and tool_registry.get("web_research") is not None
            and tool_registry.get("content_generator") is not None
            and tool_registry.get("document") is not None
        ):
            return self._plan_research_and_document_task(request, tool_registry)

        # 3. Check for Direct Document Creation Pipeline (Phase 8 Document Generation)
        if (
            has_doc_creation_intent
            and tool_registry.get("content_generator") is not None
            and tool_registry.get("document") is not None
        ):
            return self._plan_document_creation_task(request, tool_registry)

        # 4. Check for Web Research, Freshness/Temporal Query, or Office-Holder Pattern (Phase 7 & Phase 11.5)
        has_explicit_web = (
            "search" in lower_prompt
            or "web" in lower_prompt
            or "research" in lower_prompt
            or "lookup online" in lower_prompt
            or "find online" in lower_prompt
        )
        has_temporal_signal = bool(
            re.search(r"\b(latest|current|today|recently|news|current version|currently)\b", lower_prompt)
        )
        has_office_holder_signal = bool(
            re.search(
                r"\bwho(?:'s|\s+is)\s+(?:the\s+)?(?:\w+\s+)*(minister|president|ceo|cto|cfo|governor|mayor|chancellor)\b",
                lower_prompt,
            )
        )
        is_local_system_query = any(
            term in lower_prompt
            for term in ("git", "pytest", "terminal", "directory", "folder", "desktop", "window", "process")
        )

        if (
            (has_explicit_web or ((has_temporal_signal or has_office_holder_signal) and not is_local_system_query))
            and tool_registry.get("web_research") is not None
        ):
            return self._plan_web_research_task(request, tool_registry)

        # 5. Check for Terminal Command Pattern (Phase 7)
        if (
            ("git" in lower_prompt or "pytest" in lower_prompt or "terminal" in lower_prompt or "run test" in lower_prompt or "run the test" in lower_prompt)
            and tool_registry.get("terminal") is not None
        ):
            return self._plan_terminal_task(request, tool_registry)

        # 6. Check for Workspace File Listing Pattern (Issue B)
        if (
            ("file" in lower_prompt or "directory" in lower_prompt or "folder" in lower_prompt)
            and ("check" in lower_prompt or "list" in lower_prompt or "show" in lower_prompt or "see" in lower_prompt or "view" in lower_prompt or "what" in lower_prompt)
            and ("workspace" in lower_prompt or "project" in lower_prompt or "repo" in lower_prompt or "here" in lower_prompt or "root" in lower_prompt)
            and tool_registry.get("file") is not None
        ):
            return self._plan_workspace_file_list(request, tool_registry)

        # 7. Check for File Locate + Read + Summarize Pattern
        # Strictly for reading/summarizing an existing file, never when creating a file
        if (
            ("readme" in lower_prompt or "file" in lower_prompt or "summary" in lower_prompt)
            and ("find" in lower_prompt or "locate" in lower_prompt or "read" in lower_prompt or "prepare" in lower_prompt)
            and not has_creation_verb
            and tool_registry.get("file") is not None
        ):
            return self._plan_file_summary_hierarchy(request, tool_registry)

        # 8. Check for Desktop Inspection Pattern (Issue C)
        if (
            ("desktop" in lower_prompt or "window" in lower_prompt or "process" in lower_prompt or "application" in lower_prompt or "app am i" in lower_prompt or "what am i using" in lower_prompt or "active window" in lower_prompt)
            and (tool_registry.get("desktop") is not None or tool_registry.get("desktop_tool") is not None)
        ):
            return self._plan_desktop_inspection(request, tool_registry)

        # 9. Check for Echo / Message Pattern
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

    def _plan_restoration_task(
        self, request: AgentRequest, tool_registry: ToolRegistry
    ) -> TaskPlan:
        """Routes workspace restoration query to Phase 10 RestorationTool."""
        tool = (
            tool_registry.get("restoration_tool")
            or tool_registry.get_or_raise("restoration_tool")
        )
        step_id = "step_workspace_restoration"

        step = TaskStep(
            step_id=step_id,
            title="Generate and validate workspace restoration plan",
            description="Queries Intent Continuity Engine evidence to reconstruct multi-application windows and layout.",
            tool_name=tool.name,
            parameters={"query": request.user_prompt, "execute": False},
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

    def _plan_intent_continuity_task(
        self, request: AgentRequest, tool_registry: ToolRegistry
    ) -> TaskPlan:
        """Routes intent query to Phase 9 IntentContinuityTool."""
        tool = (
            tool_registry.get("intent_continuity")
            or tool_registry.get("intent_continuity_tool")
            or tool_registry.get_or_raise("intent_continuity")
        )
        step_id = "step_intent_continuity"

        step = TaskStep(
            step_id=step_id,
            title="Infer workspace intent, unfinished work, and next actions",
            description="Queries Intent Continuity Engine (ICE) to infer candidate goals, unfinished work, and recommendations.",
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

    def _plan_workspace_file_list(
        self, request: AgentRequest, tool_registry: ToolRegistry
    ) -> TaskPlan:
        """Constructs a single-step plan to list files in the workspace root."""
        file_tool = tool_registry.get_or_raise("file")
        step_id = "step_list_workspace_files"
        step = TaskStep(
            step_id=step_id,
            title="List Workspace Files",
            description="Inspects directory contents of authorized workspace root.",
            tool_name=file_tool.name,
            parameters={"action": "list_directory", "path": "."},
            dependencies=[],
            state=TaskState.READY,
            risk_level=file_tool.risk_level,
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

    def _plan_file_summary_hierarchy(
        self, request: AgentRequest, tool_registry: ToolRegistry
    ) -> TaskPlan:
        """Constructs a parent-child hierarchical plan: locate -> read -> summarize."""
        # Extract explicit filename from prompt, or default to README.md
        file_match = re.search(r"\b[\w\-./\\]+\.(?:md|txt|py|json|toml|yaml|log)\b", request.user_prompt, re.IGNORECASE)
        if file_match:
            target_path = file_match.group(0).strip("'\"")
        elif "readme" in request.user_prompt.lower():
            target_path = "README.md"
        else:
            target_path = "README.md"

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
        tool = (
            tool_registry.get("desktop")
            or tool_registry.get("desktop_tool")
            or tool_registry.get_or_raise("desktop_tool")
        )
        lower = request.user_prompt.lower()
        action = "capture_snapshot" if "process" in lower or "snapshot" in lower else "get_active_window"
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

    def _plan_clarification_task(
        self, request: AgentRequest, tool_registry: ToolRegistry, message: str
    ) -> TaskPlan:
        """Constructs an immediate clarification plan for ambiguous or malformed queries."""
        echo_tool = tool_registry.get("echo") or tool_registry.get("echo_tool")
        if echo_tool is None:
            raise ValueError(
                "Clarification required for request, but no safe echo tool is registered in the tool registry."
            )

        step_id = "step_clarification"
        step = TaskStep(
            step_id=step_id,
            title="Request Clarification",
            description="Requests user clarification for ambiguous or malformed input.",
            tool_name=echo_tool.name,
            parameters={"message": message},
            dependencies=[],
            state=TaskState.READY,
            risk_level=echo_tool.risk_level,
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
        prefixes = sorted(
            [
                "search the web for and summarize",
                "search the web for",
                "search for",
                "search web for",
                "research on",
                "research",
                "lookup online for",
                "find online information about",
                "find online",
                "what is the",
                "what are the",
                "tell me about",
                "tell me the",
                "tell me",
                "what is",
                "what are",
                "who is the",
                "who is",
                "do you know",
            ],
            key=len,
            reverse=True,
        )
        for prefix in prefixes:
            if lower.startswith(prefix):
                query = prompt[len(prefix):].strip()
                break

        # Check if user asked to summarize
        wants_summary = "summarize" in lower or "summary" in lower
        if wants_summary:
            # Strip trailing summary/report directives from search query e.g. "and create a short summary", "and summarize"
            query = re.sub(
                r"(?i)\s+and\s+(?:create|write|give|provide|make)?\s*(?:a\s+)?(?:short\s+)?(?:summary|overview|report|briefing).*",
                "",
                query,
            )
            query = re.sub(r"(?i)\s+and\s+summarize.*", "", query)
            query = re.sub(r"(?i)\s+in\s+short.*", "", query)

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
        echo_tool = tool_registry.get("echo") or tool_registry.get("echo_tool")
        if echo_tool is None:
            raise ValueError(
                "No suitable tool found to satisfy request and no safe echo fallback is registered in the tool registry."
            )

        step_id = "step_default"
        step = TaskStep(
            step_id=step_id,
            title="Execute default action",
            description="Fallback single step execution.",
            tool_name=echo_tool.name,
            parameters={"message": request.user_prompt},
            dependencies=[],
            state=TaskState.READY,
            risk_level=echo_tool.risk_level,
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

    def _extract_document_metadata(self, prompt: str) -> tuple[str, str, str, str]:
        """
        Extracts (topic, content_type, format, filename) from user prompt.
        """
        lower = prompt.lower()

        # Determine format
        if any(k in lower for k in (".txt", "plain text", "text file", "text document")):
            doc_format = "text"
            default_ext = ".txt"
        else:
            doc_format = "markdown"
            default_ext = ".md"

        # Determine content type and default name
        if "readme" in lower:
            content_type = "readme"
            default_name = "README.md"
        elif "recommend" in lower or "recommendation" in lower or "recommendations" in lower:
            content_type = "document"
            default_name = f"recommendations{default_ext}"
        elif "study notes" in lower or "notes" in lower:
            content_type = "document"
            default_name = f"study_notes{default_ext}"
        elif "summary" in lower or "summarize" in lower:
            content_type = "summary"
            default_name = f"summary{default_ext}"
        elif "report" in lower:
            content_type = "report"
            default_name = f"report{default_ext}"
        else:
            content_type = "document"
            default_name = f"document{default_ext}"

        # Look for explicit filename in prompt
        match = re.search(r"\b[\w\-./\\]+\.(?:md|txt)\b", prompt, re.IGNORECASE)
        if match:
            filename = match.group(0).strip("'\"")
        else:
            filename = default_name

        # Extract clean topic
        clean_topic = prompt

        # 1. Strip trailing document creation clauses preceded by conjunction/punctuation
        clean_topic = re.sub(
            r"(?i)(?:,\s*|\s+and\s+|\s*;\s*)(?:create|write|generate|make|save)\s+(?:a\s+)?(?:[\w\-]+\s+)?(?:file|document|report|readme|markdown|notes|study\s+notes|recommendations|text).*$",
            "",
            clean_topic,
        )
        # 2. Strip trailing summarization clauses like ", summarize it" or "and summarize it"
        clean_topic = re.sub(r"(?i)(?:,\s*|\s+and\s+|\s*;\s*)summarize\s+it.*$", "", clean_topic)

        # 3. Strip leading action verbs
        clean_topic = re.sub(
            r"(?i)^(?:find|search\s+(?:the\s+web\s+)?for|research|explore|investigate|lookup)\s+",
            "",
            clean_topic,
        )
        clean_topic = re.sub(
            r"(?i)^(?:create|write|generate|make)\s+(?:a\s+)?(?:readme\s+for|report\s+on|report\s+about|document\s+about|document\s+for|notes\s+on|study\s+notes\s+on)\s+",
            "",
            clean_topic,
        )
        clean_topic = clean_topic.strip(" .,;?!'\"") or "ORBIS Topic"

        return clean_topic, content_type, doc_format, filename

    def _plan_research_and_document_task(
        self, request: AgentRequest, tool_registry: ToolRegistry
    ) -> TaskPlan:
        """Constructs a multi-step DAG: web_research -> content_generator -> document."""
        web_tool = tool_registry.get_or_raise("web_research")
        content_tool = tool_registry.get_or_raise("content_generator")
        doc_tool = tool_registry.get_or_raise("document")

        topic, content_type, doc_format, filename = self._extract_document_metadata(request.user_prompt)

        # Clean search query by removing comparison directives if present
        search_query = re.sub(r"(?i)[,;]?\s*(?:and\s+)?compare\s+them.*$", "", topic).strip()
        search_query = search_query or topic

        # Step 1: Web Research
        step_search_id = "step_web_research"
        step_search = TaskStep(
            step_id=step_search_id,
            title=f"Web research on '{search_query}'",
            description="Searches web and extracts relevant readable text.",
            tool_name=web_tool.name,
            parameters={"action": "research", "query": search_query},
            output_key="research_output",
            dependencies=[],
            state=TaskState.READY,
            risk_level=web_tool.risk_level,
        )

        # Step 2: Content Generation
        step_content_id = "step_generate_content"
        step_content = TaskStep(
            step_id=step_content_id,
            title=f"Synthesize {content_type} on '{topic}'",
            description=f"Synthesizes structured {doc_format} content incorporating research findings.",
            tool_name=content_tool.name,
            parameters={
                "topic": topic,
                "content_type": content_type,
                "format": doc_format,
            },
            input_bindings={"context_data": f"{step_search_id}.extracted_text"},
            output_key="content_output",
            dependencies=[step_search_id],
            state=TaskState.PENDING,
            risk_level=content_tool.risk_level,
        )

        # Step 3: Document Persistence
        step_doc_id = "step_create_document"
        step_doc_params = {
            "file_path": filename,
            "content": "",
            "format": doc_format,
            "overwrite": "overwrite" in request.user_prompt.lower(),
        }
        target_exists = False
        try:
            target_exists = doc_tool.resolve_safe_path(filename).exists()
        except Exception:
            pass

        effective_risk = getattr(doc_tool, "get_effective_risk", lambda p: doc_tool.risk_level)(step_doc_params)
        requires_approval = False
        approval_prompt = None
        if target_exists or effective_risk == RiskLevel.HIGH:
            effective_risk = RiskLevel.HIGH
            requires_approval = True
            approval_prompt = f"{filename} already exists in your workspace. Do you want me to overwrite it?"

        step_doc = TaskStep(
            step_id=step_doc_id,
            title=f"Persist {doc_format} document to '{filename}'",
            description=f"Overwrites existing file '{filename}' after human confirmation." if target_exists else "Writes document to disk within authorized workspace boundary.",
            tool_name=doc_tool.name,
            parameters=step_doc_params,
            input_bindings={"content": f"{step_content_id}.content"},
            output_key="document_output",
            dependencies=[step_content_id],
            state=TaskState.PENDING,
            risk_level=effective_risk,
            requires_approval=requires_approval,
            approval_prompt=approval_prompt,
        )

        steps = {
            step_search_id: step_search,
            step_content_id: step_content,
            step_doc_id: step_doc,
        }

        plan = TaskPlan(
            request_id=request.request_id,
            user_prompt=request.user_prompt,
            task_type=TaskType.SEQUENTIAL,
            root_step_ids=[step_search_id],
            steps=steps,
        )
        PlanValidator.validate_plan_or_raise(plan, tool_registry=tool_registry)
        return plan

    def _plan_document_creation_task(
        self, request: AgentRequest, tool_registry: ToolRegistry
    ) -> TaskPlan:
        """Constructs a 2-step DAG: content_generator -> document."""
        content_tool = tool_registry.get_or_raise("content_generator")
        doc_tool = tool_registry.get_or_raise("document")

        topic, content_type, doc_format, filename = self._extract_document_metadata(request.user_prompt)

        # Step 1: Content Generation
        step_content_id = "step_generate_content"
        step_content = TaskStep(
            step_id=step_content_id,
            title=f"Synthesize {content_type} on '{topic}'",
            description=f"Synthesizes structured {doc_format} content in memory.",
            tool_name=content_tool.name,
            parameters={
                "topic": topic,
                "content_type": content_type,
                "format": doc_format,
            },
            output_key="content_output",
            dependencies=[],
            state=TaskState.READY,
            risk_level=content_tool.risk_level,
        )

        # Step 2: Document Persistence
        step_doc_id = "step_create_document"
        step_doc_params = {
            "file_path": filename,
            "content": "",
            "format": doc_format,
            "overwrite": "overwrite" in request.user_prompt.lower(),
        }
        target_exists = False
        try:
            target_exists = doc_tool.resolve_safe_path(filename).exists()
        except Exception:
            pass

        effective_risk = getattr(doc_tool, "get_effective_risk", lambda p: doc_tool.risk_level)(step_doc_params)
        requires_approval = False
        approval_prompt = None
        if target_exists or effective_risk == RiskLevel.HIGH:
            effective_risk = RiskLevel.HIGH
            requires_approval = True
            approval_prompt = f"{filename} already exists in your workspace. Do you want me to overwrite it?"

        step_doc = TaskStep(
            step_id=step_doc_id,
            title=f"Persist {doc_format} document to '{filename}'",
            description=f"Overwrites existing file '{filename}' after human confirmation." if target_exists else "Writes document to disk within authorized workspace boundary.",
            tool_name=doc_tool.name,
            parameters=step_doc_params,
            input_bindings={"content": f"{step_content_id}.content"},
            output_key="document_output",
            dependencies=[step_content_id],
            state=TaskState.PENDING,
            risk_level=effective_risk,
            requires_approval=requires_approval,
            approval_prompt=approval_prompt,
        )

        steps = {
            step_content_id: step_content,
            step_doc_id: step_doc,
        }

        plan = TaskPlan(
            request_id=request.request_id,
            user_prompt=request.user_prompt,
            task_type=TaskType.SEQUENTIAL,
            root_step_ids=[step_content_id],
            steps=steps,
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

        tools_catalog = [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.get_schema_json(),
            }
            for t in tool_registry.list_tools()
        ]

        system_prompt = (
            "You are the hierarchical task planning engine of ORBIS, an autonomous desktop agent.\n"
            "Given a user prompt and available tools, create a structured execution plan.\n"
            "You MUST respond ONLY with a valid JSON object strictly conforming to the TaskPlan schema.\n"
            "Do NOT output markdown code blocks, conversational filler, or chain-of-thought.\n"
            "\n"
            "PLANNING & TOOL SELECTION RULES:\n"
            "1. For simple conversational questions, direct answers, greetings, or explanations (e.g., 'What is AI?', 'Explain computer networks', 'What can you do?'), use the 'echo' tool with the direct, concise answer in the 'message' parameter. Do NOT use 'content_generator' or 'document' unless the user explicitly requested creating a document, file, or report.\n"
            "2. For questions asking for current, latest, or real-time information, or current office holders / leadership roles (e.g., 'latest news', 'current version of X', 'Who is the Chief Minister/President/CEO of Y?'), use the 'web_research' tool with action 'research'. Do NOT invent current facts or office holders from memory.\n"
            "3. CRITICAL GROUNDING & AMBIGUITY RULES:\n"
            "   - If the user prompt is ambiguous, nonsensical, unclear, or asks about unknown or unverified entities/versions (e.g., 'Do you know Raja version?', 'What is the city where you are famous for?'), do NOT invent, assume, or hallucinate facts or version numbers.\n"
            "   - Instead, use the 'echo' tool with a clarifying question or clearly state that the request is unclear or that available information is insufficient to answer (e.g., 'I am not sure what Raja refers to. Could you clarify which software or project you mean?').\n"
            "\n"
            "TaskPlan JSON Schema:\n"
            "{\n"
            f'  "request_id": "{request.request_id}",\n'
            f'  "user_prompt": "{request.user_prompt}",\n'
            '  "task_type": "sequential",\n'
            '  "root_step_ids": ["step1"],\n'
            '  "steps": {\n'
            '    "step1": {\n'
            '      "step_id": "step1",\n'
            '      "tool_name": "<registered_tool_name>",\n'
            '      "parameters": {<key_value_pairs>},\n'
            '      "dependencies": []\n'
            '    }\n'
            '  }\n'
            "}"
        )

        user_prompt = (
            f"Available Registered Tools:\n{json.dumps(tools_catalog, indent=2)}\n\n"
            f"User Prompt: {request.user_prompt}\n\n"
            "JSON Response:"
        )

        try:
            raw_response = await self.llm_provider.generate(
                prompt=user_prompt,
                system_prompt=system_prompt,
                format="json",
            )
        except TypeError:
            # Backward-compatibility fallback for providers that do not accept format keyword argument
            try:
                raw_response = await self.llm_provider.generate(
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                )
            except Exception as e:
                logger.warning("LLM generation exception: %s", e)
                return None
        except Exception as e:
            logger.warning("LLM generation exception: %s", e)
            return None

        if not raw_response:
            return None

        try:
            parsed = extract_json_object(raw_response)
            if not parsed:
                logger.warning(
                    "LLM response did not contain a valid JSON object: %s",
                    raw_response[:100],
                )
                return None

            # Defensively normalize casing and default fields for TaskPlan schema
            if isinstance(parsed.get("task_type"), str):
                parsed["task_type"] = parsed["task_type"].upper()
            if not parsed.get("user_prompt"):
                parsed["user_prompt"] = request.user_prompt
            if not parsed.get("request_id"):
                parsed["request_id"] = request.request_id

            if isinstance(parsed.get("steps"), dict):
                for sid, sdata in parsed["steps"].items():
                    if isinstance(sdata, dict):
                        if not sdata.get("title"):
                            sdata["title"] = sdata.get("step_id", str(sid))
                        if isinstance(sdata.get("state"), str):
                            sdata["state"] = sdata["state"].upper()

            plan = TaskPlan.model_validate(parsed)
            PlanValidator.validate_plan_or_raise(plan, tool_registry=tool_registry)
            return plan
        except Exception as err:
            logger.warning("LLM generated invalid plan: %s", err)
            return None
