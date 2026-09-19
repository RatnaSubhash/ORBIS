"""
Unit, integration, and live Windows verification tests for ORBIS System Control (Volume and Audio).
Preserves authoritative capability routing boundaries and guarantees non-destructive live testing.
"""

import sys
import threading
from typing import Any, List
from fastapi.testclient import TestClient
import pytest

from core.agent.models import AgentRequest, TaskState, TaskType
from core.agent.orchestrator import AgentOrchestrator
from core.agent.planner.classifier import (
    CapabilityClass,
    classify_capability,
    extract_system_control_metadata,
)
from core.agent.planner.hierarchical import HierarchicalPlanner
from core.agent.planner.validator import PlanValidator
from core.gateway.app import create_app
from core.tools.registry import ToolRegistry, create_default_tool_registry
from core.tools.system_control_tool import (
    MockAudioEndpoint,
    SystemControlParameters,
    SystemControlTool,
    WindowsAudioEndpoint,
)
from core.types import ExecutionContext, RiskLevel, StatusEvent


# ---------------------------------------------------------------------------
# 1. Linguistic Capability Classification Tests
# ---------------------------------------------------------------------------
class TestSystemControlClassifier:
    """Validates capability classification for system control, freshness, and geometry."""

    @pytest.mark.parametrize(
        "prompt",
        [
            "Increase volume to 90%",
            "Set volume to 50%",
            "Please turn down the volume to 20 percent",
            "Mute the computer",
            "Unmute system audio",
            "What is the current volume?",
            "volume 80",
            "turn up the volume",
            "mute",
            "unmute",
            "check volume",
        ],
    )
    def test_system_control_classification(self, prompt: str) -> None:
        cap = classify_capability(prompt)
        assert cap == CapabilityClass.SYSTEM_CONTROL, f"Failed to classify '{prompt}' as SYSTEM_CONTROL"

    @pytest.mark.parametrize(
        "prompt",
        [
            "What is the volume of a sphere?",
            "Calculate the volume of a cylinder with radius 5 and height 10",
            "What is the formula for the volume of a cone?",
            "volume of a cube",
        ],
    )
    def test_geometric_volume_exclusion(self, prompt: str) -> None:
        cap = classify_capability(prompt)
        assert cap != CapabilityClass.SYSTEM_CONTROL, f"Geometric volume query '{prompt}' wrongly classified as SYSTEM_CONTROL"
        assert cap in (CapabilityClass.GENERAL_INFORMATION, CapabilityClass.TECHNICAL_EXPLANATION)

    @pytest.mark.parametrize(
        "prompt",
        [
            "Who won IPL Championship in 2026?",
            "What is the news today?",
            "What happened recently in AI?",
            "What is the current price of Bitcoin?",
            "latest release of Python",
            "recent news about space exploration",
        ],
    )
    def test_freshness_classification_broadened(self, prompt: str) -> None:
        cap = classify_capability(prompt)
        assert cap == CapabilityClass.FRESH_INFORMATION, f"Freshness query '{prompt}' not classified as FRESH_INFORMATION"


# ---------------------------------------------------------------------------
# 2. Metadata and Parameter Extraction Tests
# ---------------------------------------------------------------------------
class TestSystemControlMetadataExtraction:
    """Validates parameter extraction and clarification triggers for system control."""

    def test_explicit_volume_percentage(self) -> None:
        action, level, clarification = extract_system_control_metadata("Increase volume to 90%")
        assert action == "set_volume"
        assert level == 90
        assert clarification is None

        action, level, clarification = extract_system_control_metadata("Set volume to 50%")
        assert action == "set_volume"
        assert level == 50
        assert clarification is None

        action, level, clarification = extract_system_control_metadata("Change volume to 75 percent")
        assert action == "set_volume"
        assert level == 75
        assert clarification is None

    def test_direct_number_assignment(self) -> None:
        action, level, clarification = extract_system_control_metadata("volume 80")
        assert action == "set_volume"
        assert level == 80
        assert clarification is None

        action, level, clarification = extract_system_control_metadata("Set the volume to 20")
        assert action == "set_volume"
        assert level == 20
        assert clarification is None

    def test_mute_and_unmute(self) -> None:
        action, level, clarification = extract_system_control_metadata("Mute the computer")
        assert action == "mute"
        assert level is None
        assert clarification is None

        action, level, clarification = extract_system_control_metadata("Unmute system audio")
        assert action == "unmute"
        assert level is None
        assert clarification is None

    def test_get_volume(self) -> None:
        action, level, clarification = extract_system_control_metadata("What is the current volume?")
        assert action == "get_volume"
        assert level is None
        assert clarification is None

        action, level, clarification = extract_system_control_metadata("check volume")
        assert action == "get_volume"
        assert level is None
        assert clarification is None

    def test_relative_adjustment_requires_clarification(self) -> None:
        action, level, clarification = extract_system_control_metadata("Increase volume")
        assert action == "set_volume"
        assert level is None
        assert clarification is not None
        assert "0-100%" in clarification

        action, level, clarification = extract_system_control_metadata("Turn down volume")
        assert action == "set_volume"
        assert level is None
        assert clarification is not None
        assert "0-100%" in clarification

    def test_out_of_bounds_requires_clarification(self) -> None:
        action, level, clarification = extract_system_control_metadata("Set volume to 150%")
        assert action == "set_volume"
        assert level is None
        assert "between 0 and 100" in clarification

        action, level, clarification = extract_system_control_metadata("Set volume to -10%")
        assert action == "set_volume"
        assert level is None
        assert "between 0 and 100" in clarification


# ---------------------------------------------------------------------------
# 3. Tool Execution and Verification Tests (Mock Endpoint)
# ---------------------------------------------------------------------------
class TestSystemControlToolMock:
    """Validates SystemControlTool execution, parameters, and verification with MockAudioEndpoint."""

    @pytest.fixture
    def mock_endpoint(self) -> MockAudioEndpoint:
        return MockAudioEndpoint(initial_level=40, initial_mute=False)

    @pytest.fixture
    def tool(self, mock_endpoint: MockAudioEndpoint) -> SystemControlTool:
        return SystemControlTool(audio_endpoint=mock_endpoint)

    @pytest.fixture
    def context(self) -> ExecutionContext:
        return ExecutionContext(request_id="test_req", trace_id="test_trace")

    @pytest.mark.asyncio
    async def test_get_volume(self, tool: SystemControlTool, context: ExecutionContext) -> None:
        params = SystemControlParameters(action="get_volume")
        result = await tool.execute(params, context)
        assert result.success is True
        assert result.output["level"] == 40
        assert result.output["muted"] is False

        ver = await tool.verify(params, result, context)
        assert ver.verified is True

    @pytest.mark.asyncio
    async def test_set_volume(
        self, tool: SystemControlTool, mock_endpoint: MockAudioEndpoint, context: ExecutionContext
    ) -> None:
        params = SystemControlParameters(action="set_volume", level=90)
        result = await tool.execute(params, context)
        assert result.success is True
        assert mock_endpoint.level == 90
        assert result.output["level"] == 90

        ver = await tool.verify(params, result, context)
        assert ver.verified is True

    @pytest.mark.asyncio
    async def test_set_volume_verification_failure_if_mismatch(
        self, tool: SystemControlTool, mock_endpoint: MockAudioEndpoint, context: ExecutionContext
    ) -> None:
        params = SystemControlParameters(action="set_volume", level=90)
        result = await tool.execute(params, context)
        assert result.success is True

        # Simulate hardware endpoint failing to apply or mismatching beyond tolerance
        mock_endpoint.level = 80
        ver = await tool.verify(params, result, context)
        assert ver.verified is False
        assert "exceeds" in ver.details

    @pytest.mark.asyncio
    async def test_mute_and_unmute(
        self, tool: SystemControlTool, mock_endpoint: MockAudioEndpoint, context: ExecutionContext
    ) -> None:
        # Mute
        params_mute = SystemControlParameters(action="mute")
        res_mute = await tool.execute(params_mute, context)
        assert res_mute.success is True
        assert mock_endpoint.muted is True
        ver_mute = await tool.verify(params_mute, res_mute, context)
        assert ver_mute.verified is True

        # Unmute
        params_unmute = SystemControlParameters(action="unmute")
        res_unmute = await tool.execute(params_unmute, context)
        assert res_unmute.success is True
        assert mock_endpoint.muted is False
        ver_unmute = await tool.verify(params_unmute, res_unmute, context)
        assert ver_unmute.verified is True

    def test_schema_rejections(self, tool: SystemControlTool) -> None:
        with pytest.raises(Exception):
            SystemControlParameters(action="set_volume", level=-5)

        with pytest.raises(Exception):
            SystemControlParameters(action="set_volume", level=105)

        with pytest.raises(Exception):
            SystemControlParameters(action="eject_disk")

    def test_risk_level_is_low(self, tool: SystemControlTool) -> None:
        assert tool.risk_level == RiskLevel.LOW


# ---------------------------------------------------------------------------
# 4. Hierarchical Planner System Control Routing Tests
# ---------------------------------------------------------------------------
class TestHierarchicalPlannerSystemControl:
    """Validates deterministic task decomposition and authoritative boundary preservation."""

    @pytest.mark.asyncio
    async def test_plan_increase_volume_to_90(self) -> None:
        planner = HierarchicalPlanner()
        registry = create_default_tool_registry()

        plan = await planner.plan("Increase volume to 90%", tool_registry=registry)
        assert plan.task_type == TaskType.SINGLE_STEP
        assert len(plan.steps) == 1
        assert "step_system_control" in plan.steps

        step = plan.steps["step_system_control"]
        assert step.tool_name == "system_control"
        assert step.parameters == {"action": "set_volume", "level": 90}
        assert step.risk_level == RiskLevel.LOW
        assert step.requires_approval is False

    @pytest.mark.asyncio
    async def test_plan_relative_volume_clarification(self) -> None:
        planner = HierarchicalPlanner()
        registry = create_default_tool_registry()

        plan = await planner.plan("Increase volume", tool_registry=registry)
        assert plan.task_type == TaskType.SINGLE_STEP
        assert len(plan.steps) == 1
        assert "step_clarification" in plan.steps

        step = plan.steps["step_clarification"]
        assert step.tool_name == "echo"
        assert "0-100%" in step.parameters["message"]

    @pytest.mark.asyncio
    async def test_authoritative_boundary_on_unknown(self) -> None:
        """
        Preserve authoritative capability-routing boundary (User Requirement 1):
        When classifier returns UNKNOWN_AMBIGUOUS, the system must NOT allow the LLM
        or planner to arbitrarily pick tools or bypass canonical safety boundaries.
        """
        planner = HierarchicalPlanner()
        registry = create_default_tool_registry()

        # Gibberish/unrecognized query
        plan = await planner.plan("xyzzy foobar blorp 12345", tool_registry=registry)
        assert plan.task_type == TaskType.SINGLE_STEP
        step = list(plan.steps.values())[0]
        # Must fall back to safe conversational echo guidance, NOT tool execution
        assert step.tool_name == "echo"
        assert "cannot determine a specific action" in step.parameters["message"]


# ---------------------------------------------------------------------------
# 5. End-to-End Orchestrator Execution Test
# ---------------------------------------------------------------------------
class TestOrchestratorSystemControlE2E:
    """Validates the full vertical slice: plan -> execute -> verify -> event bus -> final answer."""

    @pytest.mark.asyncio
    async def test_e2e_increase_volume_to_90(self) -> None:
        import asyncio
        from core.events.bus import EventBus
        from core.events.models import AgentEvent, AgentEventType

        mock_endpoint = MockAudioEndpoint(initial_level=30, initial_mute=False)
        tool = SystemControlTool(audio_endpoint=mock_endpoint)

        registry = create_default_tool_registry()
        # Replace system_control with our mock-backed tool instance
        registry.unregister("system_control")
        registry.register(tool)

        bus = EventBus()
        modern_events: List[AgentEvent] = []
        bus.subscribe(lambda e: modern_events.append(e))

        orchestrator = AgentOrchestrator(tool_registry=registry, event_bus=bus)
        status_events: List[StatusEvent] = []

        async def status_collector(event: StatusEvent) -> None:
            status_events.append(event)

        orchestrator.add_event_listener(status_collector)

        result = await orchestrator.run_task(user_request="Increase volume to 90%")

        # 1. Validate final result and friendly text
        assert result.status == TaskState.COMPLETED
        assert result.plan.steps["step_system_control"].state == TaskState.COMPLETED
        assert result.summary == "Master volume set to 90%."
        assert mock_endpoint.level == 90

        # 2. Validate exact event sequence
        await asyncio.sleep(0.01)
        modern_types = [e.event_type for e in modern_events]
        assert AgentEventType.TASK_STARTED in modern_types
        assert AgentEventType.PLAN_CREATED in modern_types
        assert AgentEventType.STEP_STARTED in modern_types
        assert AgentEventType.STEP_COMPLETED in modern_types
        assert AgentEventType.TASK_COMPLETED in modern_types

        # Verify step details
        step_events = [e for e in modern_events if e.event_type == AgentEventType.STEP_STARTED]
        assert len(step_events) == 1
        assert step_events[0].tool_name == "system_control"
        assert step_events[0].step_id == "step_system_control"


# ---------------------------------------------------------------------------
# 6. Live Windows Audio Safe Mutation Test (Requirement 2)
# ---------------------------------------------------------------------------
class TestLiveWindowsAudio:
    """
    Live test against Windows Core Audio WASAPI.
    Strictly follows User Requirement 2:
    - Record initial volume and mute state
    - Perform test
    - Verify result
    - Restore original volume/mute state in finally block
    - Guarantee system volume is not left changed.
    """

    @pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows OS")
    @pytest.mark.asyncio
    async def test_live_windows_volume_mutation_with_restore(self) -> None:
        try:
            endpoint = WindowsAudioEndpoint()
            initial_vol = endpoint.get_volume()
            initial_mute = endpoint.get_mute()
        except Exception as init_err:
            pytest.skip(f"Windows audio endpoint not available on this host: {init_err}")

        # Choose a safe test volume different from the current volume
        test_vol = 45 if initial_vol != 45 else 55

        tool = SystemControlTool(audio_endpoint=endpoint)
        context = ExecutionContext(request_id="live_test", trace_id="live_trace")

        try:
            # 1. Execute live volume mutation
            params = SystemControlParameters(action="set_volume", level=test_vol)
            result = await tool.execute(params, context)
            assert result.success is True, f"Live set_volume failed: {result.error}"

            # 2. Verify result with native endpoint
            ver = await tool.verify(params, result, context)
            assert ver.verified is True, f"Live volume verification failed: {ver.details}"
            current_vol = endpoint.get_volume()
            assert abs(current_vol - test_vol) <= tool.VOLUME_TOLERANCE_PERCENT

        finally:
            # 3. RESTORE GUARANTEE (User Requirement 2)
            # Restore original volume
            endpoint.set_volume(initial_vol)
            # Restore original mute
            endpoint.set_mute(initial_mute)

            # 4. Verify system volume and mute are restored
            restored_vol = endpoint.get_volume()
            restored_mute = endpoint.get_mute()
            assert abs(restored_vol - initial_vol) <= tool.VOLUME_TOLERANCE_PERCENT, (
                f"Failed to restore volume: original was {initial_vol}%, but endpoint is at {restored_vol}%"
            )
            assert restored_mute == initial_mute, (
                f"Failed to restore mute: original was {initial_mute}, but endpoint is {restored_mute}"
            )


# ---------------------------------------------------------------------------
# 7. Asynchronous FastAPI / Gateway Execution Tests (Requirement 6)
# ---------------------------------------------------------------------------
class TestSystemControlGatewayAsync:
    """
    Validates that SystemControlTool and WindowsAudioEndpoint execute seamlessly
    from asynchronous FastAPI / Uvicorn thread pools and coroutines without COM apartment crashes.
    """

    @pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows OS")
    def test_windows_audio_endpoint_thread_safety_without_prior_coinitialize(self) -> None:
        """Verifies WindowsAudioEndpoint succeeds in an uninitialized thread without CoInitialize error."""
        endpoint = WindowsAudioEndpoint()
        result_holder = {}
        error_holder = {}

        def thread_target() -> None:
            try:
                vol = endpoint.get_volume()
                result_holder["volume"] = vol
                result_holder["mute"] = endpoint.get_mute()
            except Exception as e:
                error_holder["error"] = e

        t = threading.Thread(target=thread_target)
        t.start()
        t.join(timeout=5.0)

        assert "error" not in error_holder, f"Endpoint call failed on thread: {error_holder.get('error')}"
        assert "volume" in result_holder
        assert 0 <= result_holder["volume"] <= 100

    @pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows OS")
    def test_fastapi_gateway_e2e_volume_mutation_with_restore(self) -> None:
        """
        Validates real end-to-end task submission through the FastAPI Gateway
        for 'Please volume to 90%' without COM thread apartment errors.
        Guarantees exact volume and mute state restoration.
        """
        endpoint = WindowsAudioEndpoint()
        initial_vol = endpoint.get_volume()
        initial_mute = endpoint.get_mute()

        app = create_app()

        try:
            with TestClient(app) as client:
                resp = client.post(
                    "/api/tasks",
                    json={"prompt": "Please volume to 90%", "sync": True},
                )
                assert resp.status_code in (200, 202)
                task_id = resp.json()["task_id"]

                st_resp = client.get(f"/api/tasks/{task_id}")
                st = st_resp.json()
                assert st["status"] == "COMPLETED"
                assert "90%" in st["summary"]
                assert "CoInitialize" not in st.get("summary", "")

                # Verify actual hardware volume was mutated to ~90%
                current_vol = endpoint.get_volume()
                assert abs(current_vol - 90) <= 2, f"Expected 90% +/- 2%, got {current_vol}%"

                # Verify events
                ev_resp = client.get(f"/api/tasks/{task_id}/events")
                ev_types = [e["event_type"] for e in ev_resp.json().get("events", [])]
                assert "TASK_STARTED" in ev_types
                assert "PLAN_CREATED" in ev_types
                assert "STEP_STARTED" in ev_types
                assert "STEP_COMPLETED" in ev_types
                assert "TASK_COMPLETED" in ev_types
        finally:
            # RESTORE GUARANTEE
            endpoint.set_volume(initial_vol)
            endpoint.set_mute(initial_mute)
            assert abs(endpoint.get_volume() - initial_vol) <= 2
            assert endpoint.get_mute() == initial_mute

    @pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows OS")
    def test_fastapi_gateway_e2e_get_volume(self) -> None:
        """Validates query volume through the FastAPI Gateway for 'What is the current volume?'."""
        app = create_app()
        with TestClient(app) as client:
            resp = client.post(
                "/api/tasks",
                json={"prompt": "What is the current volume?", "sync": True},
            )
            assert resp.status_code in (200, 202)
            task_id = resp.json()["task_id"]

            st_resp = client.get(f"/api/tasks/{task_id}")
            st = st_resp.json()
            assert st["status"] == "COMPLETED"
            assert "Current master volume is" in st["summary"]
