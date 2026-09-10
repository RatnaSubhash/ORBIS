"""
Unit tests for Phase 2 SQLite Workspace Event storage and retrieval.
"""

from datetime import datetime, timedelta, timezone
import pytest

from core.memory.db import DatabaseManager
from core.types import WorkspaceEvent, WorkspaceEventType


def test_migration_phase2_applied() -> None:
    """Verifies that migration 2 applies cleanly and creates workspace_events table."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    with db.get_connection() as conn:
        cursor = conn.execute("SELECT version, name FROM schema_migrations ORDER BY version ASC;")
        migrations = cursor.fetchall()
        assert len(migrations) >= 2
        assert migrations[1]["version"] == 2
        assert migrations[1]["name"] == "phase2_workspace_events"

        # Verify table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='workspace_events';"
        )
        assert cursor.fetchone() is not None


def test_record_and_retrieve_workspace_events() -> None:
    """Verifies persisting and querying workspace events in chronological order."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    t0 = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=5)
    t2 = t0 + timedelta(minutes=10)

    e1 = WorkspaceEvent(
        timestamp=t0,
        event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
        process_name="Code.exe",
        process_id=101,
        window_title="main.py - ORBIS",
        resource_path="c:\\Projects\\ORBIS\\main.py",
        metadata={"monitor": 1},
    )
    e2 = WorkspaceEvent(
        timestamp=t1,
        event_type=WorkspaceEventType.WINDOW_ACTIVATED,
        process_name="Code.exe",
        process_id=101,
        window_title="test_db.py - ORBIS",
        resource_path="c:\\Projects\\ORBIS\\tests\\test_db.py",
        metadata={"monitor": 1},
    )
    e3 = WorkspaceEvent(
        timestamp=t2,
        event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
        process_name="chrome.exe",
        process_id=202,
        window_title="Python Documentation - Chrome",
        metadata={"monitor": 2},
    )

    db.record_workspace_event(e1)
    db.record_workspace_event(e2)
    db.record_workspace_event(e3)

    # 1. Retrieve all with default limit
    all_events = db.get_workspace_events()
    assert len(all_events) == 3
    assert all_events[0].process_name == "Code.exe"
    assert all_events[0].window_title == "main.py - ORBIS"
    assert all_events[1].window_title == "test_db.py - ORBIS"
    assert all_events[2].process_name == "chrome.exe"

    # 2. Query with limit
    limited = db.get_workspace_events(limit=2)
    assert len(limited) == 2

    # 3. Query with time window
    time_window = db.get_workspace_events(start_time=t1, end_time=t2)
    assert len(time_window) == 2
    assert time_window[0].event_id == e2.event_id
    assert time_window[1].event_id == e3.event_id
