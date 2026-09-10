"""
Unit and integration tests for Phase 2 Workspace Observation Layer.
Validates event models, observer abstraction, deduplication, privacy exclusion,
and service lifecycle.
"""

import asyncio
from datetime import datetime, timezone
import pytest

from core.config import Settings
from core.memory.db import DatabaseManager
from core.observation.mock import MockWorkspaceObserver
from core.observation.service import WorkspaceObservationService
from core.types import WindowMetadata, WorkspaceEvent, WorkspaceEventType


def test_workspace_event_validation_and_serialization() -> None:
    """Verifies WorkspaceEvent model validation and JSON serialization round-trip."""
    event = WorkspaceEvent(
        event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
        process_name="Code.exe",
        process_id=4521,
        window_title="main.py - ORBIS - Visual Studio Code",
        resource_path="C:\\Projects\\ORBIS\\main.py",
        metadata={"hwnd": 123456},
    )

    assert event.event_id is not None
    assert event.process_name == "Code.exe"
    assert event.event_type == WorkspaceEventType.APPLICATION_ACTIVATED

    # JSON round-trip
    dumped = event.model_dump_json()
    loaded = WorkspaceEvent.model_validate_json(dumped)
    assert loaded.event_id == event.event_id
    assert loaded.process_name == event.process_name
    assert loaded.window_title == event.window_title


def test_mock_workspace_observer_abstraction() -> None:
    """Verifies the observer abstraction returns configured mock window state."""
    observer = MockWorkspaceObserver()
    assert observer.get_active_window() is None

    observer.set_active_window(
        title="Terminal",
        process_name="wt.exe",
        process_id=999,
    )
    window = observer.get_active_window()
    assert window is not None
    assert window.title == "Terminal"
    assert window.process_name == "wt.exe"
    assert window.process_id == 999


@pytest.mark.asyncio
async def test_observer_state_change_detection() -> None:
    """Verifies that switching applications produces APPLICATION_ACTIVATED and titles produce WINDOW_ACTIVATED."""
    observer = MockWorkspaceObserver()
    service = WorkspaceObservationService(observer=observer, polling_interval=0.01)

    # 1. Initial State: App A
    observer.set_active_window(title="Project - Code", process_name="Code.exe", process_id=100)
    event1 = service.sample_once()
    assert event1 is not None
    assert event1.event_type == WorkspaceEventType.APPLICATION_ACTIVATED
    assert event1.process_name == "Code.exe"

    # 2. Window Title Change inside same process
    observer.set_active_window(title="Settings - Code", process_name="Code.exe", process_id=100)
    event2 = service.sample_once()
    assert event2 is not None
    assert event2.event_type == WorkspaceEventType.WINDOW_ACTIVATED
    assert event2.window_title == "Settings - Code"

    # 3. Process Switch to App B
    observer.set_active_window(title="GitHub - Chrome", process_name="chrome.exe", process_id=200)
    event3 = service.sample_once()
    assert event3 is not None
    assert event3.event_type == WorkspaceEventType.APPLICATION_ACTIVATED
    assert event3.process_name == "chrome.exe"


def test_duplicate_event_suppression() -> None:
    """Verifies that consecutive identical samples produce zero duplicate events."""
    observer = MockWorkspaceObserver()
    service = WorkspaceObservationService(observer=observer)

    observer.set_active_window(title="Editor", process_name="Code.exe", process_id=10)

    # First sample detects change
    event1 = service.sample_once()
    assert event1 is not None

    # Second sample is identical -> suppressed
    event2 = service.sample_once()
    assert event2 is None

    # Third sample is still identical -> suppressed
    event3 = service.sample_once()
    assert event3 is None


def test_polling_interval_configuration() -> None:
    """Verifies that polling interval can be configured via parameter and settings."""
    observer = MockWorkspaceObserver()
    custom_service = WorkspaceObservationService(observer=observer, polling_interval=5.5)
    assert custom_service.polling_interval == 5.5

    default_service = WorkspaceObservationService(observer=observer)
    assert default_service.polling_interval == 3.0  # Default from config


@pytest.mark.asyncio
async def test_observer_start_stop_lifecycle() -> None:
    """Verifies that the background observation service starts and stops cleanly without leaks."""
    observer = MockWorkspaceObserver()
    events = []

    service = WorkspaceObservationService(observer=observer, polling_interval=0.01)
    service.add_event_listener(lambda e: events.append(e) or asyncio.sleep(0))

    assert service.is_running is False
    service.start()
    assert service.is_running is True

    # Emit a window change while running
    observer.set_active_window(title="Window 1", process_name="app1.exe", process_id=1)
    await asyncio.sleep(0.03)

    await service.stop()
    assert service.is_running is False
    assert len(events) >= 1


def test_excluded_application_privacy_policy() -> None:
    """Verifies that excluded processes or window titles are dropped from observation events."""
    settings = Settings(
        excluded_processes=["1password.exe", "bank.exe"],
        excluded_window_titles=["Bitwarden", "Sign In"],
    )

    # 1. Test settings helper
    assert settings.is_excluded("1password.exe", "Any Title") is True
    assert settings.is_excluded("normal.exe", "Bitwarden Vault") is True
    assert settings.is_excluded("normal.exe", "Please Sign In") is True
    assert settings.is_excluded("Code.exe", "main.py") is False

    # 2. Test observer integration
    observer = MockWorkspaceObserver()
    service = WorkspaceObservationService(observer=observer)

    # Active window is marked excluded
    observer.set_active_window(
        title="Bitwarden - Master Password",
        process_name="bitwarden.exe",
        process_id=555,
        is_excluded=True,
    )

    event = service.sample_once()
    # Must be dropped completely under privacy policy
    assert event is None


@pytest.mark.asyncio
async def test_capture_snapshot_event() -> None:
    """Verifies on-demand explicit OBSERVATION_SNAPSHOT capture."""
    observer = MockWorkspaceObserver()
    observer.set_active_window(title="Console", process_name="cmd.exe", process_id=12)

    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    service = WorkspaceObservationService(observer=observer, db_manager=db)
    snapshot_event = await service.capture_snapshot_event()

    assert snapshot_event.event_type == WorkspaceEventType.OBSERVATION_SNAPSHOT
    assert snapshot_event.process_name == "cmd.exe"

    # Verifies persisted in SQLite
    events_in_db = db.get_workspace_events()
    assert len(events_in_db) == 1
    assert events_in_db[0].event_type == WorkspaceEventType.OBSERVATION_SNAPSHOT
