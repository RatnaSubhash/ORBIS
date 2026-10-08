"""
Regression test suite for ORBIS production workspace observation integration.
Proves:
1. Production gateway startup starts the observer.
2. Gateway shutdown cleanly stops the observer.
3. Production ContinuityTool uses persistent SQLite DB, not :memory:.
4. Workspace events are recorded.
5. Recorded events can become sessions and Context DNA.
6. Continuity queries retrieve recorded in-progress and historical context.
7. Continuity queries with no data return an honest insufficient-context response.
8. Existing system-control routing still works without regression.
9. On-demand segmentation is idempotent and does not create duplicate sessions.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from core.continuity.service import ContinuityService
from core.memory.db import DatabaseManager
from core.observation.mock import MockWorkspaceObserver
from core.observation.service import WorkspaceObservationService
from core.gateway.app import create_app
from core.types import WorkspaceEvent, WorkspaceEventType


def test_production_gateway_lifespan_starts_and_stops_observer(tmp_path: Path) -> None:
    """Verifies that FastAPI lifespan startup starts the observer and shutdown stops it cleanly."""
    db_path = str(tmp_path / "test_lifecycle.db")
    db = DatabaseManager(db_path=db_path)
    db.initialize()

    mock_observer = MockWorkspaceObserver()
    obs_svc = WorkspaceObservationService(
        observer=mock_observer,
        db_manager=db,
        polling_interval=0.05,
    )

    app = create_app(db_manager=db, observation_service=obs_svc)

    assert obs_svc.is_running is False

    with TestClient(app):
        # Inside lifespan context: observer must be running
        assert app.state.observation_service.is_running is True

    # After lifespan exit: observer must be stopped cleanly
    assert obs_svc.is_running is False


def test_production_continuity_tool_uses_persistent_db(tmp_path: Path) -> None:
    """Verifies that ContinuityTool in the tool registry uses the configured persistent DB, not :memory:."""
    db_path = str(tmp_path / "test_persistent.db")
    db = DatabaseManager(db_path=db_path)
    db.initialize()

    app = create_app(db_manager=db)

    tool_registry = app.state.orchestrator.tool_registry
    continuity_tool = tool_registry.get("continuity_tool")

    assert continuity_tool is not None
    assert continuity_tool.service is not None
    assert continuity_tool.service.db.db_path == db_path
    assert continuity_tool.service.db.db_path != ":memory:"


@pytest.mark.asyncio
async def test_workspace_events_recorded_and_flushed_to_sessions_on_shutdown(tmp_path: Path) -> None:
    """Verifies events are persisted to SQLite and trailing buffer is flushed to Session + ContextDNA on shutdown."""
    db_path = str(tmp_path / "test_events.db")
    db = DatabaseManager(db_path=db_path)
    db.initialize()

    mock_observer = MockWorkspaceObserver()
    mock_observer.set_active_window(
        title="main.py - ORBIS - Visual Studio Code",
        process_name="Code.exe",
        process_id=1234,
        executable_path="C:\\Users\\user\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe",
    )

    obs_svc = WorkspaceObservationService(
        observer=mock_observer,
        db_manager=db,
        polling_interval=0.05,
    )

    app = create_app(db_manager=db, observation_service=obs_svc)

    with TestClient(app):
        # Allow the background observer loop to poll
        await asyncio.sleep(0.12)

        # Verify event was recorded to SQLite
        events = db.get_workspace_events()
        assert len(events) >= 1
        assert any(e.process_name == "Code.exe" for e in events)

        # Simulate second window
        mock_observer.set_active_window(
            title="FastAPI Documentation - Google Chrome",
            process_name="chrome.exe",
            process_id=5678,
            executable_path="C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        )
        events_after = []
        for _ in range(10):
            await asyncio.sleep(0.05)
            events_after = db.get_workspace_events()
            if len(events_after) >= 2:
                break

        assert len(events_after) >= 2
        assert any(e.process_name == "chrome.exe" for e in events_after)

    # Upon clean shutdown, trailing session buffer is finalized and persisted
    sessions = db.get_sessions()
    assert len(sessions) >= 1
    session = sessions[0]
    assert "Code.exe" in session.applications
    assert "chrome.exe" in session.applications

    # Verify Context DNA was synthesized and stored
    dna = db.get_context_dna(session.session_id)
    assert dna is not None
    assert dna.session_id == session.session_id


def test_continuity_queries_retrieve_in_progress_context(tmp_path: Path) -> None:
    """Verifies continuity queries retrieve active in-progress workspace events before 15-min gap."""
    db_path = str(tmp_path / "test_in_progress.db")
    db = DatabaseManager(db_path=db_path)
    db.initialize()

    t0 = datetime.now(timezone.utc) - timedelta(minutes=2)
    e1 = WorkspaceEvent(
        timestamp=t0,
        event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
        process_name="Code.exe",
        window_title="main.py - ORBIS",
        resource_path="c:\\Projects\\ORBIS\\main.py",
    )
    e2 = WorkspaceEvent(
        timestamp=t0 + timedelta(seconds=30),
        event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
        process_name="chrome.exe",
        window_title="Python Documentation - Chrome",
        resource_path="https://docs.python.org",
    )
    db.record_workspace_event(e1)
    db.record_workspace_event(e2)

    service = ContinuityService(db=db)
    response = service.execute_query("What was I working on?")

    assert response.has_sufficient_context is True
    assert "Code.exe" in response.active_applications
    assert "chrome.exe" in response.active_applications
    assert "Code.exe" in response.grounded_summary


def test_continuity_with_no_data_gives_honest_response(tmp_path: Path) -> None:
    """Verifies that querying continuity with zero recorded data returns the honest insufficient-context message."""
    db_path = str(tmp_path / "test_empty.db")
    db = DatabaseManager(db_path=db_path)
    db.initialize()

    service = ContinuityService(db=db)
    response = service.execute_query("What was I working on?")

    assert response.has_sufficient_context is False
    assert "No recorded workspace activity or sessions found" in response.grounded_summary
    assert len(response.active_applications) == 0


def test_on_demand_segmentation_is_idempotent(tmp_path: Path) -> None:
    """Verifies that executing multiple continuity queries does not create duplicate sessions or pollute database."""
    db_path = str(tmp_path / "test_idempotency.db")
    db = DatabaseManager(db_path=db_path)
    db.initialize()

    # Create historical event (> 20 min ago)
    t_past = datetime.now(timezone.utc) - timedelta(minutes=25)
    e1 = WorkspaceEvent(
        timestamp=t_past,
        event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
        process_name="Code.exe",
        window_title="old_file.py - Project",
        resource_path="c:\\Projects\\old_file.py",
    )
    db.record_workspace_event(e1)

    service = ContinuityService(db=db)

    # First query
    res1 = service.execute_query("What was I working on?")
    assert res1.has_sufficient_context is True
    sessions_first = db.get_sessions()
    assert len(sessions_first) == 1

    # Second query
    res2 = service.execute_query("What was I working on?")
    assert res2.has_sufficient_context is True
    sessions_second = db.get_sessions()
    # Must remain 1 session; no duplicates created
    assert len(sessions_second) == 1
    assert sessions_second[0].session_id == sessions_first[0].session_id


@pytest.mark.asyncio
async def test_system_control_routing_preserved_end_to_end(tmp_path: Path) -> None:
    """Verifies that system-control queries ('Increase volume to 90%') still route to SystemControlTool."""
    db_path = str(tmp_path / "test_system_control.db")
    db = DatabaseManager(db_path=db_path)
    db.initialize()

    app = create_app(db_manager=db)
    orchestrator = app.state.orchestrator

    result = await orchestrator.run_task("Increase volume to 90%")

    assert result.status.value in ("COMPLETED", "FAILED")
    assert result.plan is not None
    assert len(result.plan.steps) == 1
    step = list(result.plan.steps.values())[0]
    assert step.tool_name in ("system_control", "system_control_tool")
    assert step.parameters.get("action") == "set_volume"
    assert step.parameters.get("level") == 90
