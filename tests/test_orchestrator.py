"""
Integration tests for Minimal Agent Shell / AgentOrchestrator.
Validates the complete execution pipeline, status event sequences, and failure recovery.
"""

from typing import List
import pytest

from core.agent.llm.mock import MockLLMProvider
from core.agent.orchestrator import AgentOrchestrator
from core.memory.db import DatabaseManager
from core.tools.base import BaseTool
from core.tools.echo_tool import EchoParameters, EchoTool
from core.tools.registry import ToolRegistry
from core.types import (
    ExecutionContext,
    RiskLevel,
    StatusEvent,
    StatusEventType,
    ToolCall,
    ToolResult,
    VerificationReport,
)


@pytest.fixture
def test_setup():
    """Sets up an in-memory DB, registry with EchoTool, and event collector."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    registry = ToolRegistry()
    registry.register(EchoTool())

    events: List[StatusEvent] = []

    async def listener(event: StatusEvent) -> None:
        events.append(event)

    return db, registry, events, listener


@pytest.mark.asyncio
async def test_orchestrator_happy_path(test_setup) -> None:
    """Verifies complete end-to-end pipeline: Request -> Execute -> Verify -> Completed."""
    db, registry, events, listener = test_setup

    mock_call = ToolCall(tool_name="echo", parameters={"message": "hello agent"})
    llm = MockLLMProvider(default_tool_call=mock_call)

    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=registry, db_manager=db)
    orchestrator.add_event_listener(listener)

    context = ExecutionContext(task_id="task_happy")
    response = await orchestrator.run("Echo message 'hello agent'", context=context)

    # 1. Assert final response
    assert response.status == StatusEventType.COMPLETED
    assert response.tool_result is not None
    assert response.tool_result.output == "hello agent"
    assert response.verification is not None
    assert response.verification.verified is True

    # 2. Assert status event sequence
    event_types = [e.event_type for e in events]
    assert event_types == [
        StatusEventType.UNDERSTANDING_REQUEST,
        StatusEventType.CALLING_TOOL,
        StatusEventType.VERIFYING,
        StatusEventType.COMPLETED,
    ]

    # 3. Assert database audit log
    logs = db.get_audit_logs(task_id="task_happy")
    assert len(logs) == 1
    assert logs[0]["action_name"] == "echo"
    assert logs[0]["verified"] == 1


@pytest.mark.asyncio
async def test_orchestrator_no_tool_required(test_setup) -> None:
    """Verifies that requests not requiring a tool complete gracefully."""
    db, registry, events, listener = test_setup

    llm = MockLLMProvider(default_tool_call=None)
    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=registry, db_manager=db)
    orchestrator.add_event_listener(listener)

    response = await orchestrator.run("What is 2 + 2?")
    assert response.status == StatusEventType.COMPLETED
    assert "no tool required" in response.summary

    event_types = [e.event_type for e in events]
    assert event_types == [
        StatusEventType.UNDERSTANDING_REQUEST,
        StatusEventType.COMPLETED,
    ]


@pytest.mark.asyncio
async def test_orchestrator_unregistered_tool(test_setup) -> None:
    """Verifies handling when the LLM selects a non-existent tool."""
    db, registry, events, listener = test_setup

    mock_call = ToolCall(tool_name="missing_tool", parameters={})
    llm = MockLLMProvider(default_tool_call=mock_call)

    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=registry, db_manager=db)
    orchestrator.add_event_listener(listener)

    response = await orchestrator.run("Do something with missing_tool")
    assert response.status == StatusEventType.FAILED
    assert "not registered" in response.error

    event_types = [e.event_type for e in events]
    assert event_types == [
        StatusEventType.UNDERSTANDING_REQUEST,
        StatusEventType.FAILED,
    ]


@pytest.mark.asyncio
async def test_orchestrator_parameter_validation_failure(test_setup) -> None:
    """Verifies handling when the tool parameters fail Pydantic validation."""
    db, registry, events, listener = test_setup

    # EchoTool requires 'message' string, passing empty dict
    mock_call = ToolCall(tool_name="echo", parameters={})
    llm = MockLLMProvider(default_tool_call=mock_call)

    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=registry, db_manager=db)
    orchestrator.add_event_listener(listener)

    response = await orchestrator.run("Echo nothing")
    assert response.status == StatusEventType.FAILED
    assert "Parameter validation failed" in response.error

    event_types = [e.event_type for e in events]
    assert event_types == [
        StatusEventType.UNDERSTANDING_REQUEST,
        StatusEventType.FAILED,
    ]


class FailingExecutionTool(BaseTool):
    """Test tool that fails during execute()."""
    name = "failing_exec"
    description = "A tool that always fails execution."
    risk_level = RiskLevel.LOW
    parameters_schema = EchoParameters

    async def execute(self, params, context):
        return ToolResult(success=False, error="Simulated execution crash")

    async def verify(self, params, result, context):
        return VerificationReport(verified=False, details="Should not reach here")


@pytest.mark.asyncio
async def test_orchestrator_execution_failure(test_setup) -> None:
    """Verifies handling when tool execution returns success=False."""
    db, registry, events, listener = test_setup
    registry.register(FailingExecutionTool())

    mock_call = ToolCall(tool_name="failing_exec", parameters={"message": "fail"})
    llm = MockLLMProvider(default_tool_call=mock_call)

    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=registry, db_manager=db)
    orchestrator.add_event_listener(listener)

    response = await orchestrator.run("Run failing tool")
    assert response.status == StatusEventType.FAILED
    assert "Simulated execution crash" in response.error

    event_types = [e.event_type for e in events]
    assert event_types == [
        StatusEventType.UNDERSTANDING_REQUEST,
        StatusEventType.CALLING_TOOL,
        StatusEventType.FAILED,
    ]


class FailingVerificationTool(BaseTool):
    """Test tool that executes successfully but fails verification."""
    name = "failing_verify"
    description = "A tool that fails verification."
    risk_level = RiskLevel.LOW
    parameters_schema = EchoParameters

    async def execute(self, params, context):
        return ToolResult(success=True, output="fake output")

    async def verify(self, params, result, context):
        return VerificationReport(
            verified=False,
            details="Discrepancy detected in test verification.",
            discrepancies=["State mismatch detected"],
        )


@pytest.mark.asyncio
async def test_orchestrator_verification_failure(test_setup) -> None:
    """Verifies handling when tool verification fails."""
    db, registry, events, listener = test_setup
    registry.register(FailingVerificationTool())

    mock_call = ToolCall(tool_name="failing_verify", parameters={"message": "verify_fail"})
    llm = MockLLMProvider(default_tool_call=mock_call)

    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=registry, db_manager=db)
    orchestrator.add_event_listener(listener)

    response = await orchestrator.run("Run unverified tool")
    assert response.status == StatusEventType.FAILED
    assert "Verification failed" in response.error

    event_types = [e.event_type for e in events]
    assert event_types == [
        StatusEventType.UNDERSTANDING_REQUEST,
        StatusEventType.CALLING_TOOL,
        StatusEventType.VERIFYING,
        StatusEventType.FAILED,
    ]


@pytest.mark.asyncio
async def test_orchestrator_llm_exception_handling(test_setup) -> None:
    """Verifies handling when LLM provider raises an unexpected exception."""
    db, registry, events, listener = test_setup

    llm = MockLLMProvider(should_fail=True, failure_error_message="LLM unreachable")
    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=registry, db_manager=db)
    orchestrator.add_event_listener(listener)

    response = await orchestrator.run("Trigger LLM error")
    assert response.status == StatusEventType.FAILED
    assert "LLM unreachable" in response.error

    event_types = [e.event_type for e in events]
    assert event_types == [
        StatusEventType.UNDERSTANDING_REQUEST,
        StatusEventType.FAILED,
    ]
