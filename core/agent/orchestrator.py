"""
Minimal Agent Shell / Orchestrator for ORBIS Phase 1.
Implements the single-step execution pipeline:
User Request -> UNDERSTANDING_REQUEST -> Tool Selection -> CALLING_TOOL ->
Tool Execution -> VERIFYING -> COMPLETED / FAILED.
"""

from datetime import datetime, timedelta, timezone
from enum import Enum
import inspect
import logging
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional
from pydantic import ValidationError

from core.agent.llm.base import BaseLLMProvider
from core.agent.models import PendingApproval, TaskState
from core.memory.db import DatabaseManager
from core.tools.base import BaseTool
from core.tools.registry import ToolRegistry
from core.types import (
    AgentResponse,
    ExecutionContext,
    RiskLevel,
    StatusEvent,
    StatusEventType,
    ToolCall,
)

logger = logging.getLogger("orbis.agent.orchestrator")


EventListener = Callable[[StatusEvent], Awaitable[None]]


class AnswerMode(str, Enum):
    """Classification of user query intent to tailor synthesis depth and format."""
    FACTUAL = "FACTUAL"
    EXPLANATION_COMPARISON = "EXPLANATION_COMPARISON"
    RESEARCH_SUMMARY = "RESEARCH_SUMMARY"


def classify_answer_mode(prompt: str) -> AnswerMode:
    """
    Classifies a user query into an AnswerMode to tailor response synthesis length and structure:
    - FACTUAL: Direct scalar answers (version, price, office holder, date, quantity). 1-2 sentences.
    - EXPLANATION_COMPARISON: Conceptual explanations or comparisons (TCP vs UDP, how X works).
      Must cover all essential dimensions rather than stopping after the first concept.
    - RESEARCH_SUMMARY: Multi-sentence synthesis of research findings on a broad topic.
    """
    if not prompt:
        return AnswerMode.FACTUAL

    lower = prompt.lower().strip()

    # 1. Check for Research Summary mode
    summary_patterns = [
        r"\b(?:summarize|summary|overview|briefing|roundup|digest)\b",
        r"\bresearch\b.*\b(?:summary|overview|findings)\b",
    ]
    if any(re.search(pat, lower) for pat in summary_patterns):
        return AnswerMode.RESEARCH_SUMMARY

    # 2. Check for Comparison mode
    comparison_patterns = [
        r"\b(?:difference|differences|versus|vs\.?|compare|comparison|tradeoffs|trade-offs)\b",
        r"\bhow\s+does\s+.+\s+differ\s+from\b",
        r"\bpros\s+and\s+cons\b",
        r"\badvantages\s+and\s+disadvantages\b",
    ]
    if any(re.search(pat, lower) for pat in comparison_patterns):
        return AnswerMode.EXPLANATION_COMPARISON

    # 3. Check for Explanation patterns ("how does X work", "explain X", "why does/is X")
    explanation_patterns = [
        r"\bexplain\b",
        r"\bhow\s+(?:does|do|works?|can)\b",
        r"\bwhy\s+(?:is|are|do|does|did)\b",
    ]
    if any(re.search(pat, lower) for pat in explanation_patterns):
        return AnswerMode.EXPLANATION_COMPARISON

    # 4. Check for specific Factual signals (strong signals for 1-2 sentence direct answers)
    factual_patterns = [
        r"\b(?:latest|current)\s+(?:version|release|update)\b",
        r"\b(?:price|cost|fee|pricing|how\s+much)\b",
        r"\bwho\s+is\s+(?:the\s+)?(?:current\s+)?(?:chief\s+minister|prime\s+minister|president|ceo|cto|cfo|governor|mayor)\b",
        r"\b(?:when\s+was|release\s+date|launch\s+date|founded\s+in)\b",
        r"\bwhere\s+is\b",
        r"\bcapital\s+of\b",
    ]
    if any(re.search(pat, lower) for pat in factual_patterns):
        return AnswerMode.FACTUAL

    # Conceptual "What is X" check:
    what_is_match = re.match(r"^what\s+(?:is|are)\s+(?:the\s+)?([a-zA-Z0-9\s]+?)[?.]?$", lower)
    if what_is_match:
        target = what_is_match.group(1).strip()
        scalar_attributes = {"version", "release", "price", "cost", "date", "time", "capital", "population", "height", "age", "name", "rate", "distance"}
        if any(attr in target for attr in scalar_attributes):
            return AnswerMode.FACTUAL
        return AnswerMode.EXPLANATION_COMPARISON

    return AnswerMode.FACTUAL


def get_synthesis_system_prompt(mode: AnswerMode = AnswerMode.FACTUAL) -> str:
    """Returns the hardened research synthesis system prompt tailored to the specified AnswerMode."""
    base_rules = (
        "You are the research answer synthesis engine for ORBIS, an intelligent voice assistant.\n"
        "Your task is to synthesize a direct, accurate, factual answer to the user's question using ONLY the provided search evidence.\n\n"
        "MANDATORY GROUNDING RULES:\n"
        "1. ANSWER ONLY FROM SUPPLIED RESEARCH EVIDENCE:\n"
        "   Do not use outside pre-training knowledge or assumptions. Rely exclusively on the facts stated in the provided search evidence.\n"
        "2. DIRECTLY ANSWER THE USER'S EXACT QUESTION:\n"
        "   Directly address the specific entity, property, and scope requested by the user: Do NOT substitute generic information or other tiers (such as 5GB free storage) or answer a related but different fact.\n"
        "3. PREFER EXPLICIT SUPPORT AND CURRENT EVIDENCE:\n"
        "   - Prefer evidence that explicitly and directly supports the requested fact over tangential mentions.\n"
        "   - For questions with signals such as 'current', 'latest', 'today', 'now', 'who is the current', 'incumbent', 'price', 'cost', 'version', or 'release', prioritize evidence that explicitly states the current, active, or latest value.\n"
        "4. HANDLE CONFLICTING SOURCES:\n"
        "   If multiple sources in the evidence conflict or state different values, do not arbitrarily choose one. Explicitly report the conflict and state the uncertainty based on the conflicting sources.\n"
        "5. HANDLE INSUFFICIENT EVIDENCE:\n"
        "   If the supplied search evidence does not contain the requested fact or entity, you MUST explicitly state:\n"
        "   \"Unable to verify the answer from available web sources for '{query}'.\"\n"
        "   NEVER invent, extrapolate, or guess a missing price, person, date, version, or other factual value.\n"
        "6. NO CONVERSATIONAL FILLER:\n"
        "   NEVER begin with conversational filler such as \"Let's explore\", \"Welcome to\", \"Sure, I can help with that\", \"Based on my research\", or similar preambles. Start directly with the factual answer or source attribution.\n"
        "7. SOURCE ATTRIBUTION:\n"
        "   Preserve attribution to the source domain (e.g., \"According to apple.com...\").\n"
    )

    if mode == AnswerMode.EXPLANATION_COMPARISON:
        mode_rules = (
            "8. ANSWER MODE: EXPLANATION / COMPARISON (COMPLETE YET CONCISE):\n"
            "   - Provide a complete, balanced, and coherent explanation or comparison.\n"
            "   - CRITICAL: Do NOT stop after explaining only one concept or side.\n"
            "   - For comparisons (e.g. TCP vs UDP): YOU MUST cover the essential distinguishing dimensions across BOTH concepts:\n"
            "     * Connection model (connection-oriented vs connectionless)\n"
            "     * Reliability & packet ordering (guaranteed delivery and sequencing vs best-effort with no delivery guarantees)\n"
            "     * Speed & overhead (heavier handshake and error recovery vs low-latency, lightweight transmission)\n"
            "     * Typical use cases (e.g., web/email/file transfer for TCP vs live streaming/gaming/DNS/VoIP for UDP)\n"
            "   - For conceptual explanations: Clearly describe what the concept is, how it functions, and its core purpose without cutting off prematurely.\n"
            "   - Format: Complete yet concise for voice (typically 3 to 5 clear sentences / 1 structured paragraph).\n"
        )
    elif mode == AnswerMode.RESEARCH_SUMMARY:
        mode_rules = (
            "8. ANSWER MODE: RESEARCH SUMMARY (GROUNDED SYNTHESIS):\n"
            "   - Synthesize the supplied search evidence into a coherent, informative summary of the requested topic.\n"
            "   - Directly reflect the key findings from the search evidence rather than generic boilerplate.\n"
            "   - Attribute key facts to their source domains.\n"
            "   - Format: Concise synthesis for voice (typically 3 to 5 clear sentences).\n"
        )
    else:  # FACTUAL
        mode_rules = (
            "8. ANSWER MODE: FACTUAL (CONCISE 1-2 SENTENCES):\n"
            "   - Directly state the specific factual value (e.g. price, version, date, office holder).\n"
            "   - Keep simple factual answers concise (1 to 2 clear sentences) suitable for quick text-to-speech vocalization.\n"
            "   - For prices: state the exact plan/tier requested. Do NOT substitute generic or baseline free tiers (such as 5GB free storage).\n"
            "   - For office holders: name the current active incumbent. Do NOT include biographical etymology or predecessor history.\n"
        )

    return base_rules + "\n" + mode_rules


RESEARCH_SYNTHESIS_SYSTEM_PROMPT = get_synthesis_system_prompt(AnswerMode.FACTUAL)


class AgentOrchestrator:
    """
    Single-step Agent Shell for ORBIS Phase 1.
    Coordinates intent understanding, tool selection, execution, and verification
    while broadcasting safe user-visible status events.
    """

    def __init__(
        self,
        llm_provider: BaseLLMProvider,
        tool_registry: ToolRegistry,
        db_manager: Optional[DatabaseManager] = None,
        continuity_service: Optional[Any] = None,
        intent_engine: Optional[Any] = None,
        restoration_engine: Optional[Any] = None,
        planner: Optional[Any] = None,
        executor: Optional[Any] = None,
        voice_service: Optional[Any] = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.tool_registry = tool_registry
        self.db_manager = db_manager
        self.continuity_service = continuity_service
        self.intent_engine = intent_engine
        self.restoration_engine = restoration_engine
        self.planner = planner
        self.executor = executor
        self.voice_service = voice_service
        self._pending_approval: Optional[PendingApproval] = None
        self._listeners: List[EventListener] = []

    def add_event_listener(self, listener: EventListener) -> None:
        """Subscribes an async callback to receive status events."""
        self._listeners.append(listener)

    async def _emit_event(
        self,
        task_id: str,
        event_type: StatusEventType,
        summary: str,
        details: Optional[dict] = None,
        risk_level: Optional[RiskLevel] = None,
    ) -> StatusEvent:
        """Constructs and broadcasts a safe, user-visible status event."""
        event = StatusEvent(
            task_id=task_id,
            event_type=event_type,
            summary=summary,
            details=details or {},
            risk_level=risk_level,
        )
        for listener in self._listeners:
            try:
                await listener(event)
            except Exception as e:
                logger.warning("Event listener raised exception: %s", e)
        return event

    def check_safety(
        self, tool: BaseTool, parameters: Any, context: ExecutionContext
    ) -> bool:
        """
        Safety Boundary Gate for tool calls.
        Validates risk level and provides the attachment point for the future policy engine.
        In Phase 1, establishes that all tool invocations are inspected before execution.
        """
        # Phase 1 safety baseline: Ensure tool is valid and risk level is known
        if tool.risk_level not in (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH):
            return False
        return True

    async def run(
        self, user_request: str, context: Optional[ExecutionContext] = None
    ) -> AgentResponse:
        """
        Executes a single-step agent turn for the given user request.
        """
        ctx = context or ExecutionContext()
        task_id = ctx.task_id

        # 1. UNDERSTANDING_REQUEST
        await self._emit_event(
            task_id=task_id,
            event_type=StatusEventType.UNDERSTANDING_REQUEST,
            summary="Analyzing request intent and selecting appropriate tool.",
            details={"user_request": user_request},
        )

        try:
            tool_call = await self.llm_provider.select_tool(
                user_request=user_request,
                tools=self.tool_registry.list_tools(),
            )
        except Exception as e:
            error_msg = f"LLM failed during tool selection: {str(e)}"
            await self._emit_event(
                task_id=task_id,
                event_type=StatusEventType.FAILED,
                summary=error_msg,
            )
            return AgentResponse(
                task_id=task_id,
                status=StatusEventType.FAILED,
                summary="Tool selection failed.",
                error=error_msg,
            )

        # If no tool is needed or selected
        if tool_call is None:
            summary = "Request analyzed; no tool required."
            await self._emit_event(
                task_id=task_id,
                event_type=StatusEventType.COMPLETED,
                summary=summary,
            )
            return AgentResponse(
                task_id=task_id,
                status=StatusEventType.COMPLETED,
                summary=summary,
            )

        # 2. Tool Lookup in Registry
        tool = self.tool_registry.get(tool_call.tool_name)
        if tool is None:
            error_msg = f"Selected tool '{tool_call.tool_name}' is not registered."
            await self._emit_event(
                task_id=task_id,
                event_type=StatusEventType.FAILED,
                summary=error_msg,
            )
            return AgentResponse(
                task_id=task_id,
                status=StatusEventType.FAILED,
                summary="Tool resolution failed.",
                tool_call=tool_call,
                error=error_msg,
            )

        # 3. Safety Boundary & Parameter Validation
        if not self.check_safety(tool, tool_call.parameters, ctx):
            error_msg = f"Tool '{tool.name}' failed safety boundary check."
            await self._emit_event(
                task_id=task_id,
                event_type=StatusEventType.FAILED,
                summary=error_msg,
                risk_level=tool.risk_level,
            )
            return AgentResponse(
                task_id=task_id,
                status=StatusEventType.FAILED,
                summary="Safety boundary violation.",
                tool_call=tool_call,
                error=error_msg,
            )

        try:
            validated_params = tool.validate_parameters(tool_call.parameters)
        except ValidationError as val_err:
            error_msg = f"Parameter validation failed for tool '{tool.name}': {str(val_err)}"
            await self._emit_event(
                task_id=task_id,
                event_type=StatusEventType.FAILED,
                summary=error_msg,
                details={"errors": val_err.errors()},
                risk_level=tool.risk_level,
            )
            return AgentResponse(
                task_id=task_id,
                status=StatusEventType.FAILED,
                summary="Invalid tool parameters.",
                tool_call=tool_call,
                error=error_msg,
            )

        # 4. CALLING_TOOL
        await self._emit_event(
            task_id=task_id,
            event_type=StatusEventType.CALLING_TOOL,
            summary=f"Executing tool '{tool.name}'.",
            details={
                "tool_name": tool.name,
                "risk_level": tool.risk_level.value,
                "parameters": tool_call.parameters,
            },
            risk_level=tool.risk_level,
        )

        try:
            tool_result = await tool.execute(validated_params, ctx)
        except Exception as e:
            error_msg = f"Unexpected error during tool execution: {str(e)}"
            await self._emit_event(
                task_id=task_id,
                event_type=StatusEventType.FAILED,
                summary=error_msg,
                risk_level=tool.risk_level,
            )
            return AgentResponse(
                task_id=task_id,
                status=StatusEventType.FAILED,
                summary="Tool execution error.",
                tool_call=tool_call,
                error=error_msg,
            )

        if not tool_result.success:
            error_msg = tool_result.error or f"Tool '{tool.name}' returned success=False."
            await self._emit_event(
                task_id=task_id,
                event_type=StatusEventType.FAILED,
                summary=error_msg,
                risk_level=tool.risk_level,
            )
            return AgentResponse(
                task_id=task_id,
                status=StatusEventType.FAILED,
                summary="Tool execution unsuccessful.",
                tool_call=tool_call,
                tool_result=tool_result,
                error=error_msg,
            )

        # 5. VERIFYING
        await self._emit_event(
            task_id=task_id,
            event_type=StatusEventType.VERIFYING,
            summary=f"Verifying execution results for tool '{tool.name}'.",
            details={"tool_name": tool.name},
            risk_level=tool.risk_level,
        )

        try:
            verification = await tool.verify(validated_params, tool_result, ctx)
        except Exception as e:
            error_msg = f"Unexpected error during tool verification: {str(e)}"
            await self._emit_event(
                task_id=task_id,
                event_type=StatusEventType.FAILED,
                summary=error_msg,
                risk_level=tool.risk_level,
            )
            return AgentResponse(
                task_id=task_id,
                status=StatusEventType.FAILED,
                summary="Tool verification error.",
                tool_call=tool_call,
                tool_result=tool_result,
                error=error_msg,
            )

        if not verification.verified:
            error_msg = f"Verification failed: {verification.details}"
            await self._emit_event(
                task_id=task_id,
                event_type=StatusEventType.FAILED,
                summary=error_msg,
                details={"discrepancies": verification.discrepancies},
                risk_level=tool.risk_level,
            )
            return AgentResponse(
                task_id=task_id,
                status=StatusEventType.FAILED,
                summary="Tool verification failed.",
                tool_call=tool_call,
                tool_result=tool_result,
                verification=verification,
                error=error_msg,
            )

        # 6. Audit Logging in SQLite
        if self.db_manager:
            try:
                self.db_manager.log_action(
                    task_id=task_id,
                    action_name=tool.name,
                    risk_level=tool.risk_level.value,
                    parameters=tool_call.parameters,
                    result={"output": tool_result.output, "metadata": tool_result.metadata},
                    verified=verification.verified,
                )
            except Exception as e:
                logger.warning("Failed to record audit log: %s", e)

        # 7. COMPLETED
        final_summary = f"Tool '{tool.name}' executed and verified successfully."

        # Synthesize research answer if web research was executed and LLM provider is present
        if (
            tool.name == "web_research"
            and isinstance(tool_result.output, dict)
            and tool_result.output.get("action") == "research"
            and self.llm_provider is not None
        ):
            synthesized = await self.synthesize_research_answer(
                user_request=user_request,
                research_output=tool_result.output,
            )
            if synthesized:
                tool_result.output["text"] = synthesized
                tool_result.output["summary"] = synthesized
                tool_result.output["synthesized_answer"] = synthesized
                final_summary = synthesized

        await self._emit_event(
            task_id=task_id,
            event_type=StatusEventType.COMPLETED,
            summary=final_summary,
            details={"output": tool_result.output},
            risk_level=tool.risk_level,
        )

        return AgentResponse(
            task_id=task_id,
            status=StatusEventType.COMPLETED,
            summary=final_summary,
            tool_call=tool_call,
            tool_result=tool_result,
            verification=verification,
        )

    async def run_continuity(self, query: str) -> Any:
        """
        Phase 5 Vertical-Slice Helper:
        Directly executes a continuity query via ContinuityService.
        Preserves existing single-step agent pipeline without redesigning tool selection.
        """
        from core.continuity.service import ContinuityService

        service = self.continuity_service
        if service is None:
            if self.db_manager is not None:
                service = ContinuityService(db=self.db_manager)
            else:
                from core.memory.db import DatabaseManager
                temp_db = DatabaseManager(db_path=":memory:")
                temp_db.initialize()
                service = ContinuityService(db=temp_db)

        return service.execute_query(query)

    async def run_intent_continuity(self, query: str) -> Any:
        """
        Phase 9 Vertical-Slice Helper:
        Directly executes an intent continuity query via IntentContinuityEngine.
        Infers candidate goals, task states, unfinished work, and candidate next actions.
        """
        from core.ice.engine import IntentContinuityEngine

        engine = self.intent_engine
        if engine is None:
            if self.db_manager is not None:
                engine = IntentContinuityEngine(db=self.db_manager, llm_provider=self.llm_provider)
            else:
                from core.memory.db import DatabaseManager
                temp_db = DatabaseManager(db_path=":memory:")
                temp_db.initialize()
                engine = IntentContinuityEngine(db=temp_db, llm_provider=self.llm_provider)

        return await engine.infer_intent(query)

    async def synthesize_research_answer(
        self,
        user_request: str,
        research_output: Dict[str, Any],
        answer_mode: Optional[AnswerMode] = None,
    ) -> str:
        """
        Synthesizes a grounded answer directly addressing the user's question
        using only the packaged search evidence. Dynamically adapts length and structure
        based on AnswerMode (FACTUAL: 1-2 sentences; EXPLANATION_COMPARISON: complete comparison;
        RESEARCH_SUMMARY: multi-sentence synthesis).
        """
        if self.llm_provider is None:
            return research_output.get("summary") or "Research completed."

        mode = answer_mode or classify_answer_mode(user_request)
        system_prompt = get_synthesis_system_prompt(mode)

        evidence_text = research_output.get("extracted_text") or research_output.get("evidence") or ""
        search_results = research_output.get("search_results") or []

        if not evidence_text and search_results:
            blocks = []
            for idx, r in enumerate(search_results, 1):
                title = r.get("title", "")
                url = r.get("url", "")
                domain = r.get("source_domain", "")
                snippet = r.get("snippet", "")
                blocks.append(f"[Source {idx}]: {title} ({domain})\nURL: {url}\nSnippet: {snippet}")
            evidence_text = "\n\n".join(blocks)

        prompt = (
            f"User Question: {user_request}\n\n"
            f"=== SEARCH EVIDENCE ===\n"
            f"{evidence_text}\n"
            f"=== END SEARCH EVIDENCE ===\n\n"
            f"Answer the user's question directly, accurately, and concisely based strictly on the search evidence above."
        )

        try:
            answer = await self.llm_provider.generate(
                prompt=prompt,
                system_prompt=system_prompt,
            )
            cleaned = answer.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
                cleaned = re.sub(r"\n?```$", "", cleaned).strip()
            return cleaned or research_output.get("summary", "")
        except Exception as e:
            logger.warning("LLM research synthesis failed: %s; using packaged summary.", e)
            return research_output.get("summary") or "Unable to verify the answer from available web sources."

    async def run_task(
        self,
        user_request: str,
        context: Optional[ExecutionContext] = None,
        approval_callback: Optional[Any] = None,
    ) -> Any:
        """
        Phase 6 Multi-Step Execution Path:
        Decomposes a request into a hierarchical TaskPlan and executes it
        via TaskExecutor with step-by-step verification, safety checks, and approval gates.
        """
        from core.agent.executor.executor import TaskExecutor
        from core.agent.models import AgentExecutionResult, AgentRequest
        from core.agent.planner.hierarchical import HierarchicalPlanner
        from core.voice.normalizer import VoiceNormalizer

        # 1. Check for Pending Approval
        if self._pending_approval is not None:
            now = datetime.now(timezone.utc)
            if now > self._pending_approval.expires_at:
                logger.info("Pending approval for task %s expired.", self._pending_approval.task_id)
                self._pending_approval = None
            else:
                pending = self._pending_approval
                lower_req = user_request.strip().lower()

                is_aff = VoiceNormalizer.is_affirmative(lower_req)
                is_neg = VoiceNormalizer.is_negative(lower_req) or any(
                    p in lower_req for p in ("don't overwrite", "dont overwrite", "do not overwrite", "cancel", "no")
                )

                if not is_aff and not is_neg:
                    if re.search(r"\b(?:yes|overwrite(?:\s+it)?|proceed|confirm|go\s+ahead|do\s+it)\b", lower_req):
                        is_aff = True

                if is_aff and not is_neg:
                    logger.info("User granted affirmative approval for pending step %s (%s).", pending.blocked_step_id, pending.tool_name)
                    self._pending_approval = None
                    return await self._resume_pending_plan(
                        pending=pending,
                        user_request=user_request,
                        context=context,
                    )
                elif is_neg:
                    logger.info("User cancelled pending action for step %s.", pending.blocked_step_id)
                    self._pending_approval = None
                    target_name = pending.target_resource or "file"
                    cancellation_msg = f"Action cancelled. '{target_name}' was not modified."
                    return AgentExecutionResult(
                        task_id=pending.task_id,
                        request=user_request,
                        status=TaskState.CANCELLED,
                        summary=cancellation_msg,
                        plan=pending.plan,
                    )
                else:
                    # Unrelated request (context switch): Invalidate pending approval and proceed normally
                    logger.info("Unrelated request received; invalidating pending approval for step %s.", pending.blocked_step_id)
                    self._pending_approval = None

        ctx = context or ExecutionContext()
        planner = self.planner or HierarchicalPlanner(llm_provider=self.llm_provider)
        executor = self.executor or TaskExecutor(
            tool_registry=self.tool_registry,
            planner=planner,
        )

        request = AgentRequest(
            request_id=ctx.task_id,
            user_prompt=user_request,
            context=ctx,
        )

        plan_res = planner.plan(request, self.tool_registry)
        if inspect.isawaitable(plan_res):
            plan = await plan_res
        else:
            plan = plan_res

        exec_res = await executor.execute(
            plan=plan,
            context=ctx,
            approval_callback=approval_callback,
        )

        # Retain pending approval if task halted in BLOCKED_PENDING_APPROVAL
        if getattr(exec_res, "status", None) == TaskState.BLOCKED_PENDING_APPROVAL and exec_res.plan and exec_res.failed_step:
            step = exec_res.plan.steps.get(exec_res.failed_step)
            ttl_seconds = 120.0
            target_res = None
            if step and step.parameters:
                target_res = step.parameters.get("file_path") or step.parameters.get("path")
            self._pending_approval = PendingApproval(
                task_id=exec_res.task_id,
                plan=exec_res.plan,
                blocked_step_id=exec_res.failed_step,
                action_description=exec_res.summary,
                tool_name=step.tool_name if step else "unknown",
                target_resource=target_res,
                completed_steps=list(exec_res.completed_steps),
                step_results=dict(exec_res.step_results),
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
            )
        elif getattr(exec_res, "status", None) == TaskState.COMPLETED:
            self._pending_approval = None

        # Synthesize research answer if web research was executed and LLM provider is present
        if getattr(exec_res, "status", None) == TaskState.COMPLETED and self.llm_provider is not None:
            research_step_output = None
            if isinstance(getattr(exec_res, "final_output", None), dict) and exec_res.final_output.get("action") == "research":
                research_step_output = exec_res.final_output
            elif hasattr(exec_res, "step_results") and "step_web_research" in exec_res.step_results:
                step_res = exec_res.step_results["step_web_research"]
                if isinstance(step_res.output, dict) and step_res.output.get("action") == "research":
                    research_step_output = step_res.output

            if research_step_output is not None:
                synthesized = await self.synthesize_research_answer(
                    user_request=user_request,
                    research_output=research_step_output,
                )
                if synthesized:
                    if isinstance(exec_res.final_output, dict):
                        exec_res.final_output["text"] = synthesized
                        exec_res.final_output["summary"] = synthesized
                        exec_res.final_output["synthesized_answer"] = synthesized
                    else:
                        exec_res.final_output = synthesized
                    exec_res.summary = synthesized

        return exec_res

    async def _resume_pending_plan(
        self,
        pending: PendingApproval,
        user_request: str,
        context: Optional[ExecutionContext] = None,
    ) -> Any:
        """
        Resumes execution of a previously blocked TaskPlan after affirmative user approval.
        Updates blocked step parameters (e.g. overwrite=True), ensures completed steps
        are not re-run, and executes to completion.
        """
        from core.agent.executor.executor import TaskExecutor
        from core.agent.models import AgentExecutionResult, TaskState
        from core.agent.planner.hierarchical import HierarchicalPlanner

        ctx = context or ExecutionContext(task_id=pending.task_id)
        planner = self.planner or HierarchicalPlanner(llm_provider=self.llm_provider)
        executor = self.executor or TaskExecutor(
            tool_registry=self.tool_registry,
            planner=planner,
        )

        plan = pending.plan
        blocked_step = plan.steps.get(pending.blocked_step_id)
        if not blocked_step:
            logger.error("Blocked step %s not found in pending plan.", pending.blocked_step_id)
            return AgentExecutionResult(
                task_id=pending.task_id,
                request=user_request,
                status=TaskState.FAILED,
                summary=f"Could not resume: step '{pending.blocked_step_id}' no longer in plan.",
                plan=plan,
            )

        # For document overwrite: modify only the blocked step's overwrite parameter to True
        if blocked_step.tool_name == "document":
            blocked_step.parameters["overwrite"] = True

        exec_res = await executor.resume(
            plan=plan,
            approved_step_id=pending.blocked_step_id,
            context=ctx,
            initial_step_results=pending.step_results,
        )

        # Retain pending approval if a subsequent step halted for approval
        if getattr(exec_res, "status", None) == TaskState.BLOCKED_PENDING_APPROVAL and exec_res.plan and exec_res.failed_step:
            next_step = exec_res.plan.steps.get(exec_res.failed_step)
            ttl_seconds = 120.0
            target_res = None
            if next_step and next_step.parameters:
                target_res = next_step.parameters.get("file_path") or next_step.parameters.get("path")
            self._pending_approval = PendingApproval(
                task_id=exec_res.task_id,
                plan=exec_res.plan,
                blocked_step_id=exec_res.failed_step,
                action_description=exec_res.summary,
                tool_name=next_step.tool_name if next_step else "unknown",
                target_resource=target_res,
                completed_steps=list(exec_res.completed_steps),
                step_results=dict(exec_res.step_results),
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
            )
        elif getattr(exec_res, "status", None) == TaskState.COMPLETED:
            self._pending_approval = None

        return exec_res

    async def run_restoration(
        self,
        query: str,
        execute: bool = False,
        approval_callback: Optional[Any] = None,
    ) -> Any:
        """
        Phase 10 Workspace Restoration Helper:
        Creates a preflight-validated declarative RestorationPlan from ICE evidence,
        and optionally executes it within safety and approval boundaries.
        """
        from core.restoration.engine import RestorationEngine

        engine = self.restoration_engine
        if engine is None:
            if self.db_manager is not None:
                engine = RestorationEngine(db=self.db_manager)
            else:
                from core.memory.db import DatabaseManager
                temp_db = DatabaseManager(db_path=":memory:")
                temp_db.initialize()
                engine = RestorationEngine(db=temp_db)

        plan, ice_result, message = await engine.create_restoration_plan(query)
        if not execute or not plan:
            return {"plan": plan, "ice_result": ice_result, "message": message}

        return await engine.execute_restoration_plan(plan, approval_callback=approval_callback)

    async def run_voice_interaction(
        self,
        audio: Any,
        approval_callback: Optional[Any] = None,
        speak_response: bool = True,
    ) -> Any:
        """
        Phase 11 Voice Interface Helper:
        Processes user speech through Push-to-Talk voice lifecycle, routing it into
        the authoritative AgentOrchestrator planning/safety/execution path.
        """
        service = self.voice_service
        if service is None:
            from core.voice.service import VoiceService
            service = VoiceService(orchestrator=self)

        return await service.process_spoken_request(
            audio=audio,
            approval_callback=approval_callback,
            speak_response=speak_response,
        )
