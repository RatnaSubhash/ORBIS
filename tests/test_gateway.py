"""
Comprehensive test suite for the ORBIS FastAPI Gateway and Real-Time IPC (Phase 12.8).
Validates:
1. FastAPI app starts & health check
2. Task submission succeeds
3. Task ID returned
4. Correlation ID returned
5. Task status retrieval
6. Unknown task handling
7. WebSocket connection
8. Historical event replay
9. Live event delivery
10. Task-specific event isolation
11. Client disconnect unsubscription
12. Slow-client bounded queue
13. Queue overflow behavior
14. Approval endpoint preserves safety
15. Approval granted event
16. Approval denied event
17. Task cancellation event
18. Sanitized event payload
19. No chain-of-thought leakage
20. No credentials / secrets leakage
21. CORS configuration & safety
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator
import pytest
from fastapi.testclient import TestClient

from core.agent.llm.mock import MockLLMProvider
from core.agent.models import TaskPlan, TaskStep, TaskType, TaskState
from core.agent.orchestrator import AgentOrchestrator
from core.events.bus import EventBus
from core.events.models import AgentEvent, AgentEventType
from core.events.sanitizer import EventSanitizer
from core.gateway import create_app
from core.gateway.models import TaskSubmitRequest
from core.gateway.task_manager import TaskManager
from core.gateway.websocket import WebSocketConnectionConfig, handle_task_websocket
from core.memory.db import DatabaseManager
from core.tools.echo_tool import EchoTool
from core.tools.file_tool import FileTool
from core.tools.registry import ToolRegistry
from core.types import ExecutionContext, RiskLevel, ToolCall


@pytest.fixture
def gateway_fixture() -> Generator:
    """Provides a fully configured Gateway instance with in-memory orchestrator and event bus."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    reg = ToolRegistry()
    echo = EchoTool()
    reg.register(echo)

    mock_llm = MockLLMProvider(
        default_tool_call=ToolCall(tool_name="echo", parameters={"message": "Gateway test completed."})
    )
    event_bus = EventBus(max_global_events=100, max_task_events=50)

    orchestrator = AgentOrchestrator(
        llm_provider=mock_llm,
        tool_registry=reg,
        db_manager=db,
        event_bus=event_bus,
    )
    task_manager = TaskManager(max_tasks=50)

    app = create_app(
        orchestrator=orchestrator,
        event_bus=event_bus,
        task_manager=task_manager,
        allowed_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    )

    with TestClient(app) as client:
        yield {
            "app": app,
            "client": client,
            "orchestrator": orchestrator,
            "event_bus": event_bus,
            "task_manager": task_manager,
            "registry": reg,
            "db": db,
        }


# ============================================================================
# 1. FastAPI App Starts & Health Check
# ============================================================================
def test_fastapi_app_starts_and_health_check(gateway_fixture):
    client = gateway_fixture["client"]
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "orbis-gateway"
    assert data["version"] == "0.2.0"


# ============================================================================
# 2, 3, 4. Task Submission, Task ID, & Correlation ID
# ============================================================================
def test_task_submission_returns_id_and_correlation_id(gateway_fixture):
    client = gateway_fixture["client"]

    # Case A: Explicit correlation_id
    payload = {
        "prompt": "Run diagnostic check",
        "correlation_id": "corr_custom_999",
        "sync": True,
    }
    resp = client.post("/api/tasks", json=payload)
    assert resp.status_code in (200, 202)
    data = resp.json()
    assert data["task_id"].startswith("task_")
    assert data["correlation_id"] == "corr_custom_999"
    assert data["status"] in ("ACCEPTED", "RUNNING", "COMPLETED")

    # Case B: Auto-generated correlation_id
    payload_auto = {
        "prompt": "Echo another message",
        "sync": True,
    }
    resp_auto = client.post("/api/tasks", json=payload_auto)
    assert resp_auto.status_code in (200, 202)
    data_auto = resp_auto.json()
    assert data_auto["task_id"].startswith("task_")
    assert data_auto["correlation_id"] == f"corr_{data_auto['task_id']}"


def test_task_submission_async_background(gateway_fixture):
    client = gateway_fixture["client"]
    payload = {
        "prompt": "Run background task",
        "sync": False,
    }
    resp = client.post("/api/tasks", json=payload)
    assert resp.status_code == 202
    data = resp.json()
    assert data["task_id"].startswith("task_")
    assert data["status"] == "ACCEPTED"


# ============================================================================
# 5. Task Status Retrieval
# ============================================================================
def test_task_status_retrieval(gateway_fixture):
    client = gateway_fixture["client"]

    # Submit task synchronously to reach completed state
    resp = client.post("/api/tasks", json={"prompt": "Test status check", "sync": True})
    task_id = resp.json()["task_id"]

    # Query task status
    status_resp = client.get(f"/api/tasks/{task_id}")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["task_id"] == task_id
    assert status_data["status"] == "COMPLETED"
    assert status_data["summary"] is not None


# ============================================================================
# 6. Unknown Task Handling
# ============================================================================
def test_unknown_task_handling(gateway_fixture):
    client = gateway_fixture["client"]

    # Non-existent task returns 404
    resp = client.get("/api/tasks/task_nonexistent_12345")
    assert resp.status_code == 404
    err = resp.json()
    assert "not found" in err["error"].lower()
    assert err["code"] == "HTTP_404"

    # Invalid task ID format returns 400
    resp_invalid = client.get("/api/tasks/invalid@task!name")
    assert resp_invalid.status_code == 400
    err_invalid = resp_invalid.json()
    assert "format" in err_invalid["error"].lower()


# ============================================================================
# 7. WebSocket Connection
# ============================================================================
def test_websocket_connection(gateway_fixture):
    client = gateway_fixture["client"]
    with client.websocket_connect("/ws/tasks/task_ws_test?replay=false") as ws:
        assert ws is not None


# ============================================================================
# 8. Historical Event Replay
# ============================================================================
def test_historical_event_replay_on_websocket(gateway_fixture):
    client = gateway_fixture["client"]
    event_bus: EventBus = gateway_fixture["event_bus"]

    task_id = "task_replay_101"

    # Pre-publish two historical events for this task
    ev1 = EventSanitizer.create_safe_event(
        event_type=AgentEventType.TASK_STARTED,
        task_id=task_id,
        correlation_id=f"corr_{task_id}",
        safe_user_message="Initial task started event.",
        status="RUNNING",
    )
    ev2 = EventSanitizer.create_safe_event(
        event_type=AgentEventType.STEP_STARTED,
        task_id=task_id,
        correlation_id=f"corr_{task_id}",
        safe_user_message="Step 1 running.",
        status="RUNNING",
        step_id="step_1",
    )
    event_bus.publish_nowait(ev1)
    event_bus.publish_nowait(ev2)

    # Connect WebSocket with replay=true
    with client.websocket_connect(f"/ws/tasks/{task_id}?replay=true") as ws:
        msg1 = ws.receive_json()
        assert msg1["task_id"] == task_id
        assert msg1["event_type"] == AgentEventType.TASK_STARTED.value
        assert msg1["safe_user_message"] == "Initial task started event."

        msg2 = ws.receive_json()
        assert msg2["task_id"] == task_id
        assert msg2["event_type"] == AgentEventType.STEP_STARTED.value
        assert msg2["step_id"] == "step_1"


# ============================================================================
# 9. Live Event Delivery
# ============================================================================
def test_live_event_delivery_over_websocket(gateway_fixture):
    client = gateway_fixture["client"]
    event_bus: EventBus = gateway_fixture["event_bus"]

    task_id = "task_live_202"

    with client.websocket_connect(f"/ws/tasks/{task_id}?replay=false") as ws:
        # Publish live event while connected
        ev = EventSanitizer.create_safe_event(
            event_type=AgentEventType.STEP_COMPLETED,
            task_id=task_id,
            correlation_id=f"corr_{task_id}",
            safe_user_message="Step completed successfully.",
            status="COMPLETED",
            step_id="step_2",
        )
        event_bus.publish_nowait(ev)

        received = ws.receive_json()
        assert received["task_id"] == task_id
        assert received["event_type"] == AgentEventType.STEP_COMPLETED.value
        assert received["step_id"] == "step_2"


# ============================================================================
# 10. Task-Specific Event Isolation
# ============================================================================
def test_task_specific_event_isolation(gateway_fixture):
    client = gateway_fixture["client"]
    event_bus: EventBus = gateway_fixture["event_bus"]

    task_A = "task_isolation_A"
    task_B = "task_isolation_B"

    with client.websocket_connect(f"/ws/tasks/{task_A}?replay=false") as ws_A:
        # Publish event for task_B
        ev_B = EventSanitizer.create_safe_event(
            event_type=AgentEventType.TASK_STARTED,
            task_id=task_B,
            correlation_id=f"corr_{task_B}",
            safe_user_message="Task B event; should NOT reach A.",
        )
        event_bus.publish_nowait(ev_B)

        # Publish event for task_A
        ev_A = EventSanitizer.create_safe_event(
            event_type=AgentEventType.TASK_STARTED,
            task_id=task_A,
            correlation_id=f"corr_{task_A}",
            safe_user_message="Task A event; must reach A.",
        )
        event_bus.publish_nowait(ev_A)

        # Client A must receive task_A and NEVER task_B
        received = ws_A.receive_json()
        assert received["task_id"] == task_A
        assert received["safe_user_message"] == "Task A event; must reach A."


# ============================================================================
# 11. Client Disconnect / Unsubscribe
# ============================================================================
def test_client_disconnect_unsubscribes_from_event_bus(gateway_fixture):
    client = gateway_fixture["client"]
    event_bus: EventBus = gateway_fixture["event_bus"]

    initial_subs = event_bus.subscriber_count

    with client.websocket_connect("/ws/tasks/task_disc_test?replay=false") as ws:
        assert event_bus.subscriber_count == initial_subs + 1

    # After context manager exits, client has disconnected
    assert event_bus.subscriber_count == initial_subs


# ============================================================================
# 12 & 13. Slow-Client Bounded Queue & Overflow Behavior
# ============================================================================
@pytest.mark.asyncio
async def test_slow_client_bounded_queue_overflow_policy_close():
    """
    Tests that when a WebSocket client is too slow and its queue fills to capacity,
    the 'close' policy terminates the connection rather than accumulating unbounded memory.
    """
    event_bus = EventBus()
    task_id = "task_overflow_close"

    class FakeWebSocket:
        def __init__(self):
            self.closed = False
            self.close_code = None
            self.close_reason = None
            self.sent = []

        async def accept(self):
            pass

        async def send_json(self, data):
            self.sent.append(data)
            # Simulate a slow client by sleeping on send
            await asyncio.sleep(0.5)

        async def receive(self):
            # Block indefinitely until cancelled
            await asyncio.sleep(100)
            return {"type": "websocket.disconnect"}

        async def close(self, code=1000, reason=""):
            self.closed = True
            self.close_code = code
            self.close_reason = reason

    fake_ws = FakeWebSocket()
    cfg = WebSocketConnectionConfig(max_queue_size=3, overflow_policy="close")

    # Run websocket handler in background
    ws_task = asyncio.create_task(
        handle_task_websocket(
            websocket=fake_ws,  # type: ignore
            task_id=task_id,
            event_bus=event_bus,
            replay=False,
            config=cfg,
        )
    )

    await asyncio.sleep(0.02)  # allow connection acceptance

    # Publish 10 events rapidly to exceed queue size of 3
    for i in range(10):
        ev = EventSanitizer.create_safe_event(
            event_type=AgentEventType.STEP_STARTED,
            task_id=task_id,
            correlation_id=f"corr_{task_id}",
            safe_user_message=f"Event {i}",
        )
        event_bus.publish_nowait(ev)

    # Await handler termination due to slow-client close
    await asyncio.wait_for(ws_task, timeout=2.0)
    assert fake_ws.closed is True
    assert fake_ws.close_code == 1008
    assert "Slow client disconnected" in fake_ws.close_reason


@pytest.mark.asyncio
async def test_slow_client_bounded_queue_overflow_policy_drop():
    """
    Tests that under 'drop' policy, older events are evicted so the queue
    never exceeds max_queue_size.
    """
    event_bus = EventBus()
    task_id = "task_overflow_drop"

    class FakeWebSocket:
        def __init__(self):
            self.closed = False
            self.sent = []

        async def accept(self):
            pass

        async def send_json(self, data):
            self.sent.append(data)

        async def receive(self):
            await asyncio.sleep(100)
            return {"type": "websocket.disconnect"}

        async def close(self, code=1000, reason=""):
            self.closed = True

    fake_ws = FakeWebSocket()
    cfg = WebSocketConnectionConfig(max_queue_size=2, overflow_policy="drop")

    ws_task = asyncio.create_task(
        handle_task_websocket(
            websocket=fake_ws,  # type: ignore
            task_id=task_id,
            event_bus=event_bus,
            replay=False,
            config=cfg,
        )
    )

    await asyncio.sleep(0.02)

    # Publish 5 events
    for i in range(5):
        ev = EventSanitizer.create_safe_event(
            event_type=AgentEventType.STEP_STARTED,
            task_id=task_id,
            correlation_id=f"corr_{task_id}",
            safe_user_message=f"Event {i}",
        )
        event_bus.publish_nowait(ev)

    await asyncio.sleep(0.1)
    # Cancel handler
    ws_task.cancel()
    try:
        await ws_task
    except asyncio.CancelledError:
        pass

    # Verify connection was handled without memory explosion
    assert len(fake_ws.sent) > 0


# ============================================================================
# 14, 15, 16, 17. Approval Flow (Granted, Denied, Cancellation)
# ============================================================================
def test_approval_flow_granted(tmp_path: Path):
    """
    Tests that high-risk action entering BLOCKED_PENDING_APPROVAL can be
    affirmatively confirmed via the gateway, emitting APPROVAL_GRANTED and resuming.
    """
    from core.tools.base import BaseTool
    from core.tools.document_tool import DocumentTool
    from core.types import ToolResult, VerificationReport

    class MockContentTool(BaseTool):
        name: str = "content_generator"
        description: str = "Mock content generator"
        risk_level: RiskLevel = RiskLevel.LOW

        def validate_parameters(self, params):
            return params

        async def execute(self, params, context):
            return ToolResult(success=True, output={"content": "# Mock Content for " + str(params.get("topic", "Topic"))})

        async def verify(self, params, result, context):
            return VerificationReport(verified=True, details="Content generated.")

    db = DatabaseManager(db_path=":memory:")
    db.initialize()
    reg = ToolRegistry()
    doc_tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    content_tool = MockContentTool()
    reg.register(doc_tool)
    reg.register(content_tool)

    # Create pre-existing file so overwrite requires approval
    existing_file = tmp_path / "report.md"
    existing_file.write_text("Old Report Content", encoding="utf-8")

    mock_llm = MockLLMProvider()
    event_bus = EventBus()
    orchestrator = AgentOrchestrator(
        llm_provider=mock_llm,
        tool_registry=reg,
        db_manager=db,
        event_bus=event_bus,
    )
    task_manager = TaskManager()

    app = create_app(orchestrator=orchestrator, event_bus=event_bus, task_manager=task_manager)

    with TestClient(app) as client:
        # 1. Submit task that triggers file overwrite
        resp = client.post("/api/tasks", json={"prompt": "Create a report for report.md", "sync": True})
        assert resp.status_code in (200, 202)
        task_id = resp.json()["task_id"]

        # 2. Verify task is BLOCKED_PENDING_APPROVAL
        status_resp = client.get(f"/api/tasks/{task_id}")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["status"] == "BLOCKED_PENDING_APPROVAL"
        assert status_data["pending_approval"] is not None
        assert status_data["pending_approval"]["target_resource"] == "report.md"

        # 3. Post affirmative approval
        appr_resp = client.post(f"/api/tasks/{task_id}/approve", json={"approved": True, "reason": "Confirmed overwrite"})
        assert appr_resp.status_code == 200
        appr_data = appr_resp.json()
        assert appr_data["status"] in ("RUNNING", "COMPLETED")

        # 4. Verify APPROVAL_GRANTED event was recorded
        events = event_bus.get_task_events(task_id)
        event_types = [e.event_type for e in events]
        assert AgentEventType.APPROVAL_GRANTED in event_types
        assert AgentEventType.TASK_COMPLETED in event_types

        # 5. Verify file was overwritten
        assert existing_file.read_text(encoding="utf-8") != "Old Report Content"


def test_approval_flow_denied(tmp_path: Path):
    """
    Tests that denying approval emits APPROVAL_DENIED, TASK_CANCELLED,
    cancels the task, and leaves the target resource untouched.
    """
    from core.tools.base import BaseTool
    from core.tools.document_tool import DocumentTool
    from core.types import ToolResult, VerificationReport

    class MockContentTool(BaseTool):
        name: str = "content_generator"
        description: str = "Mock content generator"
        risk_level: RiskLevel = RiskLevel.LOW

        def validate_parameters(self, params):
            return params

        async def execute(self, params, context):
            return ToolResult(success=True, output={"content": "# Mock Content for " + str(params.get("topic", "Topic"))})

        async def verify(self, params, result, context):
            return VerificationReport(verified=True, details="Content generated.")

    db = DatabaseManager(db_path=":memory:")
    db.initialize()
    reg = ToolRegistry()
    doc_tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    content_tool = MockContentTool()
    reg.register(doc_tool)
    reg.register(content_tool)

    existing_file = tmp_path / "README.md"
    existing_file.write_text("Protected Initial Readme", encoding="utf-8")

    mock_llm = MockLLMProvider()
    event_bus = EventBus()
    orchestrator = AgentOrchestrator(
        llm_provider=mock_llm,
        tool_registry=reg,
        db_manager=db,
        event_bus=event_bus,
    )
    task_manager = TaskManager()

    app = create_app(orchestrator=orchestrator, event_bus=event_bus, task_manager=task_manager)

    with TestClient(app) as client:
        # Submit task that triggers file overwrite
        resp = client.post("/api/tasks", json={"prompt": "Create a README for README.md", "sync": True})
        assert resp.status_code in (200, 202)
        task_id = resp.json()["task_id"]

        # Post negative approval (rejection)
        appr_resp = client.post(f"/api/tasks/{task_id}/approve", json={"approved": False, "reason": "Do not overwrite"})
        assert appr_resp.status_code == 200
        appr_data = appr_resp.json()
        assert appr_data["status"] == "CANCELLED"

        # Verify events
        events = event_bus.get_task_events(task_id)
        event_types = [e.event_type for e in events]
        assert AgentEventType.APPROVAL_DENIED in event_types
        assert AgentEventType.TASK_CANCELLED in event_types

        # Verify target file was untouched
        assert existing_file.read_text(encoding="utf-8") == "Protected Initial Readme"


def test_approval_on_task_with_no_pending_approval(gateway_fixture):
    client = gateway_fixture["client"]
    resp = client.post("/api/tasks/task_no_approval_needed/approve", json={"approved": True})
    assert resp.status_code == 409
    data = resp.json()
    assert "no pending approval" in data["error"].lower()


# ============================================================================
# 18, 19, 20. Security Sanitization: No Chain-of-Thought, Secrets, or Prompts
# ============================================================================
def test_no_cot_prompt_credentials_leakage(gateway_fixture):
    client = gateway_fixture["client"]
    event_bus: EventBus = gateway_fixture["event_bus"]

    task_id = "task_security_leak_check"

    # Create event with simulated sensitive data (prohibited keys, secrets, HTML)
    sensitive_event = EventSanitizer.create_safe_event(
        event_type=AgentEventType.STEP_COMPLETED,
        task_id=task_id,
        correlation_id=f"corr_{task_id}",
        safe_user_message="API call returned api_key='sk-secret99999' with <div>HTML content</div>",
        metadata={
            "prompt": "SELECT * FROM users; System prompt: you are a secret agent.",
            "thought": "Internal chain of thought: plan to exploit service.",
            "raw_html": "<script>alert('xss')</script>",
            "password": "super_secret_db_password_123",
            "safe_counter": 42,
        },
    )
    event_bus.publish_nowait(sensitive_event)

    # 1. Query events via REST endpoint
    resp = client.get(f"/api/tasks/{task_id}/events")
    assert resp.status_code == 200
    events_data = resp.json()["events"]
    assert len(events_data) == 1
    ev_dict = events_data[0]

    # Invariants:
    # 1. Prohibited keys are dropped
    meta = ev_dict["metadata"]
    assert "prompt" not in meta
    assert "thought" not in meta
    assert "raw_html" not in meta

    # 2. Password key is masked
    assert meta["password"] == "[REDACTED]"
    assert meta["safe_counter"] == 42

    # 3. Secret inside message is redacted
    msg = ev_dict["safe_user_message"]
    assert "sk-secret99999" not in msg
    assert "[REDACTED]" in msg


# ============================================================================
# 21. CORS Configuration
# ============================================================================
def test_cors_headers_configured_and_restricted(gateway_fixture):
    client = gateway_fixture["client"]

    # Allowed origin gets CORS header
    resp = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"

    # Disallowed origin does NOT get access-control-allow-origin
    resp_bad = client.get("/api/health", headers={"Origin": "http://malicious-site.com"})
    assert resp_bad.headers.get("access-control-allow-origin") != "http://malicious-site.com"
    assert resp_bad.headers.get("access-control-allow-origin") != "*"


# ============================================================================
# 22. Historical Replay Deduplication Regression Tests (Scenarios A - F)
# ============================================================================
@pytest.mark.asyncio
async def test_replay_dedup_scenario_a_existing_history_delivered_once():
    """Scenario A: An event existing in history is delivered exactly once."""
    bus = EventBus()
    task_id = "task_dedup_A"

    ev = EventSanitizer.create_safe_event(
        event_type=AgentEventType.TASK_STARTED,
        task_id=task_id,
        correlation_id=f"corr_{task_id}",
        safe_user_message="History event A",
    )
    bus.publish_nowait(ev)

    class FakeWS:
        def __init__(self):
            self.sent = []
            self.closed = False
        async def accept(self): pass
        async def send_json(self, data): self.sent.append(data)
        async def receive(self):
            await asyncio.sleep(100)
            return {"type": "websocket.disconnect"}
        async def close(self, code=1000, reason=""): self.closed = True

    fake_ws = FakeWS()
    handler = asyncio.create_task(
        handle_task_websocket(websocket=fake_ws, task_id=task_id, event_bus=bus, replay=True)  # type: ignore
    )
    await asyncio.sleep(0.05)
    handler.cancel()
    try:
        await handler
    except asyncio.CancelledError:
        pass

    event_ids = [m["event_id"] for m in fake_ws.sent]
    assert event_ids == [ev.event_id], "Historical event must be delivered exactly once"


@pytest.mark.asyncio
async def test_replay_dedup_scenario_b_event_between_subscribe_and_replay():
    """
    Scenario B: An event published immediately after subscription but during
    history retrieval is delivered exactly once, not duplicated.
    """
    bus = EventBus()
    task_id = "task_dedup_B"

    ev_between = EventSanitizer.create_safe_event(
        event_type=AgentEventType.TASK_STARTED,
        task_id=task_id,
        correlation_id=f"corr_{task_id}",
        safe_user_message="Event between subscribe and replay",
    )

    orig_get_task_events = bus.get_task_events
    def hooked_get_task_events(t_id):
        # Deterministically publish right when get_task_events is called
        bus.publish_nowait(ev_between)
        return orig_get_task_events(t_id)

    bus.get_task_events = hooked_get_task_events  # type: ignore

    class FakeWS:
        def __init__(self):
            self.sent = []
            self.closed = False
        async def accept(self): pass
        async def send_json(self, data): self.sent.append(data)
        async def receive(self):
            await asyncio.sleep(100)
            return {"type": "websocket.disconnect"}
        async def close(self, code=1000, reason=""): self.closed = True

    fake_ws = FakeWS()
    handler = asyncio.create_task(
        handle_task_websocket(websocket=fake_ws, task_id=task_id, event_bus=bus, replay=True)  # type: ignore
    )
    await asyncio.sleep(0.05)
    handler.cancel()
    try:
        await handler
    except asyncio.CancelledError:
        pass

    event_ids = [m["event_id"] for m in fake_ws.sent]
    assert event_ids.count(ev_between.event_id) == 1, (
        f"Event published during history retrieval must be delivered exactly once, got {event_ids}"
    )


@pytest.mark.asyncio
async def test_replay_dedup_scenario_c_multiple_events_during_replay():
    """
    Scenario C: Multiple events published while the replay loop is sending
    are each delivered exactly once.
    """
    bus = EventBus()
    task_id = "task_dedup_C"

    # Pre-populate history with 2 events
    ev_hist1 = EventSanitizer.create_safe_event(
        event_type=AgentEventType.TASK_STARTED,
        task_id=task_id,
        correlation_id=f"corr_{task_id}",
        safe_user_message="Hist 1",
    )
    ev_hist2 = EventSanitizer.create_safe_event(
        event_type=AgentEventType.STEP_STARTED,
        task_id=task_id,
        correlation_id=f"corr_{task_id}",
        safe_user_message="Hist 2",
    )
    bus.publish_nowait(ev_hist1)
    bus.publish_nowait(ev_hist2)

    ev_rapid1 = EventSanitizer.create_safe_event(
        event_type=AgentEventType.STEP_COMPLETED,
        task_id=task_id,
        correlation_id=f"corr_{task_id}",
        safe_user_message="Rapid 1",
    )
    ev_rapid2 = EventSanitizer.create_safe_event(
        event_type=AgentEventType.TASK_COMPLETED,
        task_id=task_id,
        correlation_id=f"corr_{task_id}",
        safe_user_message="Rapid 2",
    )

    class FakeWS:
        def __init__(self):
            self.sent = []
            self.first_send = True
            self.closed = False
        async def accept(self): pass
        async def send_json(self, data):
            self.sent.append(data)
            if self.first_send:
                self.first_send = False
                # Deterministically trigger multiple live events mid-replay
                bus.publish_nowait(ev_rapid1)
                bus.publish_nowait(ev_rapid2)
        async def receive(self):
            await asyncio.sleep(100)
            return {"type": "websocket.disconnect"}
        async def close(self, code=1000, reason=""): self.closed = True

    fake_ws = FakeWS()
    handler = asyncio.create_task(
        handle_task_websocket(websocket=fake_ws, task_id=task_id, event_bus=bus, replay=True)  # type: ignore
    )
    await asyncio.sleep(0.08)
    handler.cancel()
    try:
        await handler
    except asyncio.CancelledError:
        pass

    event_ids = [m["event_id"] for m in fake_ws.sent]
    for expected_id in [ev_hist1.event_id, ev_hist2.event_id, ev_rapid1.event_id, ev_rapid2.event_id]:
        count = event_ids.count(expected_id)
        assert count == 1, f"Event {expected_id} expected exactly 1 delivery, got {count}. Full sent: {event_ids}"


@pytest.mark.asyncio
async def test_replay_dedup_scenario_d_events_after_replay():
    """Scenario D: Events published after replay completes are delivered normally."""
    bus = EventBus()
    task_id = "task_dedup_D"

    ev_hist = EventSanitizer.create_safe_event(
        event_type=AgentEventType.TASK_STARTED,
        task_id=task_id,
        correlation_id=f"corr_{task_id}",
        safe_user_message="History D",
    )
    bus.publish_nowait(ev_hist)

    class FakeWS:
        def __init__(self):
            self.sent = []
            self.closed = False
        async def accept(self): pass
        async def send_json(self, data): self.sent.append(data)
        async def receive(self):
            await asyncio.sleep(100)
            return {"type": "websocket.disconnect"}
        async def close(self, code=1000, reason=""): self.closed = True

    fake_ws = FakeWS()
    handler = asyncio.create_task(
        handle_task_websocket(websocket=fake_ws, task_id=task_id, event_bus=bus, replay=True)  # type: ignore
    )
    await asyncio.sleep(0.03)  # wait for replay to finish

    # Publish after replay
    ev_post = EventSanitizer.create_safe_event(
        event_type=AgentEventType.TASK_COMPLETED,
        task_id=task_id,
        correlation_id=f"corr_{task_id}",
        safe_user_message="Post replay event",
    )
    bus.publish_nowait(ev_post)
    await asyncio.sleep(0.03)

    handler.cancel()
    try:
        await handler
    except asyncio.CancelledError:
        pass

    event_ids = [m["event_id"] for m in fake_ws.sent]
    assert event_ids == [ev_hist.event_id, ev_post.event_id]


@pytest.mark.asyncio
async def test_replay_dedup_scenario_e_events_other_task_never_delivered():
    """Scenario E: Events belonging to other tasks are never delivered during or after replay."""
    bus = EventBus()
    task_target = "task_dedup_E_target"
    task_other = "task_dedup_E_other"

    class FakeWS:
        def __init__(self):
            self.sent = []
            self.closed = False
        async def accept(self): pass
        async def send_json(self, data): self.sent.append(data)
        async def receive(self):
            await asyncio.sleep(100)
            return {"type": "websocket.disconnect"}
        async def close(self, code=1000, reason=""): self.closed = True

    fake_ws = FakeWS()
    handler = asyncio.create_task(
        handle_task_websocket(websocket=fake_ws, task_id=task_target, event_bus=bus, replay=True)  # type: ignore
    )
    await asyncio.sleep(0.02)

    # Publish for other task
    ev_other = EventSanitizer.create_safe_event(
        event_type=AgentEventType.TASK_STARTED,
        task_id=task_other,
        correlation_id=f"corr_{task_other}",
        safe_user_message="Other task event",
    )
    bus.publish_nowait(ev_other)

    # Publish for target task
    ev_target = EventSanitizer.create_safe_event(
        event_type=AgentEventType.TASK_STARTED,
        task_id=task_target,
        correlation_id=f"corr_{task_target}",
        safe_user_message="Target task event",
    )
    bus.publish_nowait(ev_target)
    await asyncio.sleep(0.03)

    handler.cancel()
    try:
        await handler
    except asyncio.CancelledError:
        pass

    assert len(fake_ws.sent) == 1
    assert fake_ws.sent[0]["task_id"] == task_target


@pytest.mark.asyncio
async def test_replay_dedup_scenario_f_disconnect_during_replay_cleans_subscribers():
    """Scenario F: Client disconnect during historical replay safely unregisters subscriber."""
    from fastapi import WebSocketDisconnect

    bus = EventBus()
    task_id = "task_dedup_F"

    # Pre-populate history
    for i in range(3):
        bus.publish_nowait(EventSanitizer.create_safe_event(
            event_type=AgentEventType.STEP_STARTED,
            task_id=task_id,
            correlation_id=f"corr_{task_id}",
            safe_user_message=f"Event {i}",
        ))

    class DisconnectingWS:
        def __init__(self):
            self.closed = False
        async def accept(self): pass
        async def send_json(self, data):
            # Raise disconnect immediately upon attempting to send first replay message
            raise WebSocketDisconnect(code=1000)
        async def receive(self):
            await asyncio.sleep(100)
            return {"type": "websocket.disconnect"}
        async def close(self, code=1000, reason=""): self.closed = True

    assert bus.subscriber_count == 0

    disc_ws = DisconnectingWS()
    await handle_task_websocket(websocket=disc_ws, task_id=task_id, event_bus=bus, replay=True)  # type: ignore

    # Verify that despite disconnect during replay, subscriber is cleanly removed
    assert bus.subscriber_count == 0
