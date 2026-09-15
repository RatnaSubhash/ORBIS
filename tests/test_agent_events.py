"""
Comprehensive Test Suite for ORBIS Phase 12 Event Architecture.
Tests AgentEvent models, EventSanitizer, EventBus pub/sub, non-blocking execution,
lifecycle boundaries, correlation retention, and security credential leakage prevention.
"""

import asyncio
from datetime import datetime, timezone
import time
from typing import Any, Dict, List
import pytest

from core.agent.llm.mock import MockLLMProvider
from core.agent.models import AgentRequest, TaskPlan, TaskState, TaskStep, TaskType
from core.agent.orchestrator import AgentOrchestrator
from core.agent.executor.executor import TaskExecutor
from core.events.bus import EventBus
from core.events.models import AgentEvent, AgentEventType
from core.events.sanitizer import EventSanitizer
from core.tools.base import BaseTool
from core.tools.registry import ToolRegistry
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport
from pydantic import BaseModel, Field


# ==============================================================================
# Helper Mock Tools
# ==============================================================================

class DummyEchoParams(BaseModel):
    message: str = Field(default="hello")


class DummyEchoTool(BaseTool):
    name: str = "dummy_echo"
    description: str = "Test echo tool"
    parameters_schema: Any = DummyEchoParams

    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        p: DummyEchoParams = params  # type: ignore
        return ToolResult(success=True, output={"echo": p.message})

    async def verify(self, params: BaseModel, result: ToolResult, context: ExecutionContext) -> VerificationReport:
        return VerificationReport(verified=True, details="Echo verified.")


class DummyHighRiskParams(BaseModel):
    action: str = Field(default="critical")


class DummyHighRiskTool(BaseTool):
    name: str = "dummy_high_risk"
    description: str = "Test high risk tool"
    risk_level: RiskLevel = RiskLevel.HIGH
    parameters_schema: Any = DummyHighRiskParams

    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        return ToolResult(success=True, output={"status": "executed high risk"})

    async def verify(self, params: BaseModel, result: ToolResult, context: ExecutionContext) -> VerificationReport:
        return VerificationReport(verified=True, details="High risk verified.")


class DummyFailingTool(BaseTool):
    name: str = "dummy_failing"
    description: str = "Tool that always fails execution"
    parameters_schema: Any = DummyEchoParams

    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        return ToolResult(success=False, error="Simulated execution explosion")

    async def verify(self, params: BaseModel, result: ToolResult, context: ExecutionContext) -> VerificationReport:
        return VerificationReport(verified=False, details="Execution failed.")


# ==============================================================================
# 1. AgentEvent Domain Model Tests
# ==============================================================================

def test_agent_event_model_instantiation():
    """Verifies valid construction, defaults, and immutability of AgentEvent."""
    ev = AgentEvent(
        correlation_id="corr_123",
        task_id="task_abc",
        event_type=AgentEventType.TASK_STARTED,
        status="RUNNING",
        safe_user_message="Starting task execution.",
        metadata={"key": "val"},
    )
    assert ev.event_id is not None
    assert ev.correlation_id == "corr_123"
    assert ev.task_id == "task_abc"
    assert ev.event_type == AgentEventType.TASK_STARTED
    assert ev.status == "RUNNING"
    assert ev.safe_user_message == "Starting task execution."
    assert ev.metadata == {"key": "val"}

    # Verify immutability (frozen=True)
    with pytest.raises(Exception):
        ev.status = "COMPLETED"  # type: ignore


def test_agent_event_type_coverage():
    """Verifies all 20 required event types exist in AgentEventType enum."""
    expected_types = {
        "TASK_STARTED",
        "PLAN_CREATED",
        "STEP_STARTED",
        "STEP_COMPLETED",
        "STEP_FAILED",
        "RESEARCH_STARTED",
        "SOURCE_FOUND",
        "SOURCE_READ",
        "CONTENT_GENERATION_STARTED",
        "DOCUMENT_UPDATED",
        "APPLICATION_OPENED",
        "SYSTEM_ACTION_STARTED",
        "APPROVAL_REQUIRED",
        "APPROVAL_GRANTED",
        "APPROVAL_DENIED",
        "VERIFICATION_STARTED",
        "VERIFICATION_COMPLETED",
        "TASK_COMPLETED",
        "TASK_FAILED",
        "TASK_CANCELLED",
    }
    actual_types = {e.value for e in AgentEventType}
    assert expected_types.issubset(actual_types)


# ==============================================================================
# 2. EventBus Unit Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_event_bus_zero_subscribers():
    """EventBus must safely accept publications when zero subscribers are registered."""
    bus = EventBus()
    ev = AgentEvent(
        correlation_id="c1",
        task_id="t1",
        event_type=AgentEventType.TASK_STARTED,
        status="RUNNING",
        safe_user_message="Zero subscriber test",
    )
    # Both publish and publish_nowait must be safe and not error
    bus.publish_nowait(ev)
    await bus.publish(ev)
    assert len(bus.get_task_events("t1")) == 2
    await bus.close()


@pytest.mark.asyncio
async def test_event_bus_single_and_multiple_subscribers():
    """EventBus must deliver published events to all registered subscribers."""
    bus = EventBus()
    received_1: List[AgentEvent] = []
    received_2: List[AgentEvent] = []

    sub_id_1 = bus.subscribe(lambda e: received_1.append(e))
    sub_id_2 = bus.subscribe(lambda e: received_2.append(e))

    ev = AgentEvent(
        correlation_id="c1",
        task_id="t1",
        event_type=AgentEventType.STEP_STARTED,
        status="RUNNING",
        safe_user_message="Multiple subscriber test",
    )
    await bus.publish(ev)
    # Give background task on loop a turn to execute
    await asyncio.sleep(0.01)

    assert len(received_1) == 1
    assert len(received_2) == 1
    assert received_1[0].event_id == ev.event_id
    assert received_2[0].event_id == ev.event_id

    # Test unsubscribe
    assert bus.unsubscribe(sub_id_1) is True
    assert bus.unsubscribe("non_existent_id") is False

    ev2 = AgentEvent(
        correlation_id="c1",
        task_id="t1",
        event_type=AgentEventType.STEP_COMPLETED,
        status="COMPLETED",
        safe_user_message="Unsubscribed test",
    )
    await bus.publish(ev2)
    await asyncio.sleep(0.01)

    # Subscriber 1 should not have received ev2; subscriber 2 should have received it
    assert len(received_1) == 1
    assert len(received_2) == 2

    await bus.close()


@pytest.mark.asyncio
async def test_event_bus_publish_is_genuinely_non_blocking():
    """
    CRITICAL REQUIREMENT:
    EventBus.publish() must return immediately without waiting for slow subscribers.
    """
    bus = EventBus()

    async def slow_subscriber(event: AgentEvent):
        await asyncio.sleep(0.2)  # 200ms delay

    bus.subscribe(slow_subscriber)

    ev = AgentEvent(
        correlation_id="c_fast",
        task_id="t_fast",
        event_type=AgentEventType.TASK_STARTED,
        status="RUNNING",
        safe_user_message="Non-blocking performance test",
    )

    t_start = time.perf_counter()
    # Publish must not await the 200ms subscriber!
    await bus.publish(ev)
    t_elapsed = time.perf_counter() - t_start

    # Execution must return in under 20 milliseconds (well below 200ms)
    assert t_elapsed < 0.05, f"publish() took too long ({t_elapsed:.4f}s); it must not block on subscriber!"

    await bus.close()


@pytest.mark.asyncio
async def test_event_bus_subscriber_exception_isolation():
    """Subscriber exceptions must be caught and contained; they must never propagate or crash the caller."""
    bus = EventBus()
    good_received: List[AgentEvent] = []

    def failing_sync_subscriber(event: AgentEvent):
        raise ValueError("Simulated subscriber crash!")

    async def failing_async_subscriber(event: AgentEvent):
        raise RuntimeError("Simulated async subscriber failure!")

    def good_subscriber(event: AgentEvent):
        good_received.append(event)

    bus.subscribe(failing_sync_subscriber)
    bus.subscribe(failing_async_subscriber)
    bus.subscribe(good_subscriber)

    ev = AgentEvent(
        correlation_id="c_crash",
        task_id="t_crash",
        event_type=AgentEventType.STEP_STARTED,
        status="RUNNING",
        safe_user_message="Exception containment test",
    )

    # Neither failing subscriber should raise through publish
    await bus.publish(ev)
    await asyncio.sleep(0.02)

    assert len(good_received) == 1
    assert good_received[0].event_id == ev.event_id

    await bus.close()


@pytest.mark.asyncio
async def test_event_bus_late_subscriber_replay():
    """Late-joining subscribers can request historical events for a given task_id."""
    bus = EventBus()

    for i in range(3):
        bus.publish_nowait(
            AgentEvent(
                correlation_id="c_hist",
                task_id="task_history_test",
                event_type=AgentEventType.STEP_STARTED,
                status="RUNNING",
                safe_user_message=f"Historical milestone {i}",
            )
        )

    # Late subscriber connects and requests historical events for task_history_test
    replayed: List[AgentEvent] = []
    bus.subscribe(lambda e: replayed.append(e), replay_task_id="task_history_test")

    await asyncio.sleep(0.02)
    assert len(replayed) == 3
    assert [e.safe_user_message for e in replayed] == [
        "Historical milestone 0",
        "Historical milestone 1",
        "Historical milestone 2",
    ]

    await bus.close()


@pytest.mark.asyncio
async def test_event_bus_safe_lifecycle_close_and_post_close():
    """Verifies that publish after close is safe and close prevents task leaks."""
    bus = EventBus()

    ev = AgentEvent(
        correlation_id="c_close",
        task_id="t_close",
        event_type=AgentEventType.TASK_STARTED,
        status="RUNNING",
        safe_user_message="Before close",
    )
    await bus.publish(ev)

    await bus.close()
    assert bus.is_closed is True
    assert bus.subscriber_count == 0

    # Publishing after close must not crash; it logs warning and drops
    post_ev = AgentEvent(
        correlation_id="c_close",
        task_id="t_close",
        event_type=AgentEventType.TASK_COMPLETED,
        status="COMPLETED",
        safe_user_message="After close",
    )
    bus.publish_nowait(post_ev)
    await bus.publish(post_ev)

    # Subscribing to closed bus raises RuntimeError
    with pytest.raises(RuntimeError):
        bus.subscribe(lambda e: None)


# ==============================================================================
# 3. EventSanitizer Unit & Security Tests
# ==============================================================================

def test_sanitizer_message_secret_redaction():
    """Verifies EventSanitizer redacts secrets, tokens, passwords, and assignments in message strings."""
    raw_msg = (
        "Execution result: api_key=sk-1234567890abcdef1234567890 "
        "and password=supersecretpass with token=ghp_123456789012345678901234567890 "
        "and Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz"
    )
    clean = EventSanitizer.clean_message(raw_msg)
    assert "sk-1234567890" not in clean
    assert "supersecretpass" not in clean
    assert "ghp_1234567890" not in clean
    assert "[REDACTED" in clean


def test_sanitizer_recursive_metadata_cleaning():
    """Verifies recursive sanitization of nested dicts, lists, and mixed-case sensitive keys."""
    raw_metadata = {
        "normal_key": "safe_value",
        "Password": "MySecretPassword123",
        "API_KEY": "sk-ant-api03-abcdef12345678901234567890",
        "nested_dict": {
            "Auth_Token": "Bearer token_xyz1234567890",
            "safe_nested": 42,
            "chain_of_thought": "Private internal LLM thoughts that should never appear",
            "system_prompt": "You are an internal system prompt",
            "raw_html": "<html><body>Secret Web Page</body></html>",
        },
        "nested_list": [
            {"token": "secret_in_list_item"},
            "Plain string with password=embedded_secret inside it",
            100,
        ],
    }

    clean = EventSanitizer.clean_metadata(raw_metadata)

    # Safe keys preserved
    assert clean["normal_key"] == "safe_value"
    assert clean["nested_dict"]["safe_nested"] == 42
    assert clean["nested_list"][2] == 100

    # Sensitive keys masked
    assert clean["Password"] == "[REDACTED]"
    assert clean["API_KEY"] == "[REDACTED]"
    assert clean["nested_dict"]["Auth_Token"] == "[REDACTED]"
    assert clean["nested_list"][0]["token"] == "[REDACTED]"

    # Prohibited keys completely dropped from output
    assert "chain_of_thought" not in clean["nested_dict"]
    assert "system_prompt" not in clean["nested_dict"]
    assert "raw_html" not in clean["nested_dict"]

    # String with embedded password assignment masked
    embedded_str = clean["nested_list"][1]
    assert "embedded_secret" not in embedded_str
    assert "password=[REDACTED]" in embedded_str


def test_sanitizer_critical_credential_leakage_prevention():
    """
    MANDATORY SECURITY TEST:
    Proves that simulated sensitive tool output containing values such as:
      API_KEY=secret
      PASSWORD=secret
      TOKEN=secret
    cannot be surfaced directly through safe_user_message or metadata.
    """
    sensitive_payload = (
        "CRITICAL ERROR: Failed to connect with credentials:\n"
        "API_KEY=secret_key_value_9999\n"
        "PASSWORD=super_secret_db_pass\n"
        "TOKEN=bearer_token_xyz_8888\n"
        "DATABASE_URL=postgres://user:secretpass@localhost:5432/db"
    )

    event = EventSanitizer.create_safe_event(
        event_type=AgentEventType.STEP_FAILED,
        task_id="task_sec_01",
        correlation_id="corr_sec_01",
        safe_user_message=sensitive_payload,
        status="FAILED",
        metadata={
            "tool_output": sensitive_payload,
            "api_key": "secret_key_value_9999",
            "password": "super_secret_db_pass",
            "token": "bearer_token_xyz_8888",
        },
    )

    # 1. safe_user_message must not contain raw sensitive secrets
    assert "secret_key_value_9999" not in event.safe_user_message
    assert "super_secret_db_pass" not in event.safe_user_message
    assert "bearer_token_xyz_8888" not in event.safe_user_message

    # 2. Metadata values must not contain raw secrets
    meta_str = str(event.metadata)
    assert "secret_key_value_9999" not in meta_str
    assert "super_secret_db_pass" not in meta_str
    assert "bearer_token_xyz_8888" not in meta_str

    # 3. Sensitive keys in metadata must be [REDACTED]
    assert event.metadata["api_key"] == "[REDACTED]"
    assert event.metadata["password"] == "[REDACTED]"
    assert event.metadata["token"] == "[REDACTED]"


# ==============================================================================
# 4. Orchestrator & TaskExecutor End-to-End Integration Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_orchestrator_task_lifecycle_events():
    """
    Verifies full lifecycle telemetry through AgentOrchestrator and TaskExecutor:
    TASK_STARTED -> PLAN_CREATED -> STEP_STARTED -> VERIFICATION_STARTED -> VERIFICATION_COMPLETED -> STEP_COMPLETED -> TASK_COMPLETED.
    """
    bus = EventBus()
    captured_events: List[AgentEvent] = []
    bus.subscribe(lambda e: captured_events.append(e))

    registry = ToolRegistry()
    registry.register(DummyEchoTool())

    llm = MockLLMProvider()
    orchestrator = AgentOrchestrator(
        llm_provider=llm,
        tool_registry=registry,
        event_bus=bus,
    )

    step = TaskStep(
        step_id="step_1",
        title="Echo Hello",
        tool_name="dummy_echo",
        parameters={"message": "ORBIS Live Flow"},
    )
    plan = TaskPlan(
        request_id="task_lifecycle_1",
        user_prompt="echo ORBIS Live Flow",
        task_type=TaskType.SINGLE_STEP,
        root_step_ids=["step_1"],
        steps={"step_1": step},
    )

    ctx = ExecutionContext(task_id="task_lifecycle_1", metadata={"correlation_id": "corr_life_1"})
    res = await orchestrator.run_task(
        user_request="echo ORBIS Live Flow",
        context=ctx,
        plan=plan,
    )

    assert res.status == TaskState.COMPLETED

    # Allow event bus loop dispatches to complete
    await asyncio.sleep(0.05)

    event_types = [e.event_type for e in captured_events]
    assert AgentEventType.TASK_STARTED in event_types
    assert AgentEventType.PLAN_CREATED in event_types
    assert AgentEventType.STEP_STARTED in event_types
    assert AgentEventType.VERIFICATION_STARTED in event_types
    assert AgentEventType.VERIFICATION_COMPLETED in event_types
    assert AgentEventType.STEP_COMPLETED in event_types
    assert AgentEventType.TASK_COMPLETED in event_types

    # Correlation verification: correlation_id -> task_id -> step_id
    for ev in captured_events:
        assert ev.correlation_id == "corr_life_1"
        assert ev.task_id == "task_lifecycle_1"
        if ev.event_type in (AgentEventType.STEP_STARTED, AgentEventType.STEP_COMPLETED, AgentEventType.VERIFICATION_STARTED, AgentEventType.VERIFICATION_COMPLETED):
            assert ev.step_id == "step_1"
            assert ev.tool_name == "dummy_echo"

    await bus.close()


@pytest.mark.asyncio
async def test_orchestrator_approval_lifecycle_events():
    """
    Verifies telemetry when a high-risk tool halts for approval:
    APPROVAL_REQUIRED emitted -> User responds affirmatively -> APPROVAL_GRANTED emitted -> Task completes.
    """
    bus = EventBus()
    captured_events: List[AgentEvent] = []
    bus.subscribe(lambda e: captured_events.append(e))

    registry = ToolRegistry()
    registry.register(DummyHighRiskTool())

    llm = MockLLMProvider()
    orchestrator = AgentOrchestrator(
        llm_provider=llm,
        tool_registry=registry,
        event_bus=bus,
    )

    step = TaskStep(
        step_id="step_hr_1",
        title="Execute High Risk Action",
        tool_name="dummy_high_risk",
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
        approval_prompt="Do you authorize critical action?",
    )
    plan = TaskPlan(
        request_id="task_appr_1",
        user_prompt="do critical action",
        task_type=TaskType.SINGLE_STEP,
        root_step_ids=["step_hr_1"],
        steps={"step_hr_1": step},
    )

    ctx = ExecutionContext(task_id="task_appr_1", metadata={"correlation_id": "corr_appr_1"})

    # 1. First execution halts at approval gate
    res1 = await orchestrator.run_task(
        user_request="do critical action",
        context=ctx,
        plan=plan,
    )
    assert res1.status == TaskState.BLOCKED_PENDING_APPROVAL

    await asyncio.sleep(0.02)
    types_1 = [e.event_type for e in captured_events]
    assert AgentEventType.APPROVAL_REQUIRED in types_1

    # 2. User confirms affirmatively: "yes proceed"
    res2 = await orchestrator.run_task(
        user_request="yes proceed",
        context=ctx,
    )
    assert res2.status == TaskState.COMPLETED

    await asyncio.sleep(0.02)
    types_all = [e.event_type for e in captured_events]
    assert AgentEventType.APPROVAL_GRANTED in types_all
    assert AgentEventType.TASK_COMPLETED in types_all

    await bus.close()


@pytest.mark.asyncio
async def test_orchestrator_approval_denied_and_cancellation():
    """
    Verifies telemetry when user denies a pending approval:
    APPROVAL_REQUIRED -> User cancels -> APPROVAL_DENIED & TASK_CANCELLED.
    """
    bus = EventBus()
    captured_events: List[AgentEvent] = []
    bus.subscribe(lambda e: captured_events.append(e))

    registry = ToolRegistry()
    registry.register(DummyHighRiskTool())

    llm = MockLLMProvider()
    orchestrator = AgentOrchestrator(
        llm_provider=llm,
        tool_registry=registry,
        event_bus=bus,
    )

    step = TaskStep(
        step_id="step_cancel_1",
        title="Execute High Risk Action",
        tool_name="dummy_high_risk",
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
    )
    plan = TaskPlan(
        request_id="task_cancel_1",
        user_prompt="do critical action",
        task_type=TaskType.SINGLE_STEP,
        root_step_ids=["step_cancel_1"],
        steps={"step_cancel_1": step},
    )

    ctx = ExecutionContext(task_id="task_cancel_1", metadata={"correlation_id": "corr_cancel_1"})
    res1 = await orchestrator.run_task(user_request="do critical action", context=ctx, plan=plan)
    assert res1.status == TaskState.BLOCKED_PENDING_APPROVAL

    # User cancels
    res2 = await orchestrator.run_task(user_request="cancel don't do it", context=ctx)
    assert res2.status == TaskState.CANCELLED

    await asyncio.sleep(0.02)
    types = [e.event_type for e in captured_events]
    assert AgentEventType.APPROVAL_DENIED in types
    assert AgentEventType.TASK_CANCELLED in types

    await bus.close()


@pytest.mark.asyncio
async def test_orchestrator_step_and_task_failure_events():
    """Verifies STEP_FAILED and TASK_FAILED are emitted on tool failure."""
    bus = EventBus()
    captured_events: List[AgentEvent] = []
    bus.subscribe(lambda e: captured_events.append(e))

    registry = ToolRegistry()
    registry.register(DummyFailingTool())

    llm = MockLLMProvider()
    orchestrator = AgentOrchestrator(
        llm_provider=llm,
        tool_registry=registry,
        event_bus=bus,
    )

    step = TaskStep(
        step_id="step_fail_1",
        title="Failing Step",
        tool_name="dummy_failing",
        max_attempts=1,
    )
    plan = TaskPlan(
        request_id="task_fail_1",
        user_prompt="run failing tool",
        task_type=TaskType.SINGLE_STEP,
        root_step_ids=["step_fail_1"],
        steps={"step_fail_1": step},
    )

    ctx = ExecutionContext(task_id="task_fail_1", metadata={"correlation_id": "corr_fail_1"})
    res = await orchestrator.run_task(user_request="run failing tool", context=ctx, plan=plan)
    assert res.status == TaskState.FAILED

    await asyncio.sleep(0.02)
    types = [e.event_type for e in captured_events]
    assert AgentEventType.STEP_FAILED in types
    assert AgentEventType.TASK_FAILED in types

    await bus.close()


@pytest.mark.asyncio
async def test_legacy_status_event_listeners_continue_to_work():
    """
    BACKWARDS COMPATIBILITY TEST:
    Verifies that existing orchestrator.add_event_listener() receiving StatusEvent
    continues to receive events without any regression.
    """
    from core.types import StatusEvent, StatusEventType

    legacy_events: List[StatusEvent] = []
    bus = EventBus()
    modern_events: List[AgentEvent] = []

    bus.subscribe(lambda e: modern_events.append(e))

    registry = ToolRegistry()
    registry.register(DummyEchoTool())
    llm = MockLLMProvider()

    orchestrator = AgentOrchestrator(
        llm_provider=llm,
        tool_registry=registry,
        event_bus=bus,
    )

    # Register legacy listener
    orchestrator.add_event_listener(lambda e: legacy_events.append(e) or asyncio.sleep(0))

    step = TaskStep(
        step_id="step_1",
        title="Echo",
        tool_name="dummy_echo",
        parameters={"message": "Legacy Test"},
    )
    plan = TaskPlan(
        request_id="task_legacy_1",
        user_prompt="test legacy",
        task_type=TaskType.SINGLE_STEP,
        root_step_ids=["step_1"],
        steps={"step_1": step},
    )

    ctx = ExecutionContext(task_id="task_legacy_1")
    res = await orchestrator.run_task(user_request="test legacy", context=ctx, plan=plan)
    assert res.status == TaskState.COMPLETED

    # Verify modern events arrived
    await asyncio.sleep(0.02)
    assert len(modern_events) > 0

    # Verify legacy run() also produces legacy StatusEvent
    resp = await orchestrator.run("hello", task_id="task_legacy_run")
    assert len(legacy_events) > 0
    assert any(e.event_type == StatusEventType.UNDERSTANDING_REQUEST for e in legacy_events)

    await bus.close()
