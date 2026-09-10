"""
Minimal Agent Shell / Orchestrator for ORBIS Phase 1.
Implements the single-step execution pipeline:
User Request -> UNDERSTANDING_REQUEST -> Tool Selection -> CALLING_TOOL ->
Tool Execution -> VERIFYING -> COMPLETED / FAILED.
"""

import inspect
import logging
from typing import Any, Awaitable, Callable, List, Optional
from pydantic import ValidationError

from core.agent.llm.base import BaseLLMProvider
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
        from core.agent.models import AgentRequest
        from core.agent.planner.hierarchical import HierarchicalPlanner

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

        return await executor.execute(
            plan=plan,
            context=ctx,
            approval_callback=approval_callback,
        )

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
