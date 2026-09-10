"""
Unit tests for Phase 3 SQLite persistence of Sessions, Context DNA, and Traceability.
"""

from datetime import datetime, timedelta, timezone
import sqlite3
import pytest

from core.continuity.dna import ContextDNASynthesizer
from core.continuity.sessionizer import SessionSegmenter
from core.memory.db import DatabaseManager
from core.types import ContextDNA, Session, WorkspaceEvent, WorkspaceEventType


def test_migration_phase3_applied() -> None:
    """Verifies that migration 3 applies cleanly and creates sessions and context_dna tables."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    with db.get_connection() as conn:
        cursor = conn.execute("SELECT version, name FROM schema_migrations ORDER BY version ASC;")
        migrations = cursor.fetchall()
        assert len(migrations) >= 3
        assert migrations[2]["version"] == 3
        assert migrations[2]["name"] == "phase3_sessions_and_context_dna"

        # Verify tables exist
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('sessions', 'context_dna');"
        )
        table_names = {row["name"] for row in cursor.fetchall()}
        assert "sessions" in table_names
        assert "context_dna" in table_names


def test_record_and_get_session() -> None:
    """Verifies persisting and querying a Session by session_id."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    t0 = datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=15)

    session = Session(
        session_id="sess_abc123",
        start_time=t0,
        end_time=t1,
        duration_seconds=900.0,
        event_count=4,
        applications=["Code.exe", "chrome.exe"],
        process_names=["Code.exe", "chrome.exe"],
        resources=["c:\\Projects\\ORBIS\\main.py"],
        event_ids=["evt_1", "evt_2", "evt_3", "evt_4"],
        session_metadata={"category": "development"},
        confidence=1.0,
    )

    row_id = db.record_session(session)
    assert row_id > 0

    fetched = db.get_session("sess_abc123")
    assert fetched is not None
    assert fetched.session_id == "sess_abc123"
    assert fetched.start_time == t0
    assert fetched.end_time == t1
    assert fetched.duration_seconds == 900.0
    assert fetched.event_count == 4
    assert fetched.applications == ["Code.exe", "chrome.exe"]
    assert fetched.resources == ["c:\\Projects\\ORBIS\\main.py"]
    assert fetched.event_ids == ["evt_1", "evt_2", "evt_3", "evt_4"]
    assert fetched.session_metadata == {"category": "development"}
    assert fetched.confidence == 1.0


def test_get_sessions_chronological_and_time_range() -> None:
    """Verifies querying multiple sessions with ordering and time window filters."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    base_t = datetime(2026, 9, 9, 8, 0, 0, tzinfo=timezone.utc)

    s1 = Session(
        session_id="sess_1",
        start_time=base_t,
        end_time=base_t + timedelta(minutes=10),
        duration_seconds=600.0,
        event_count=2,
    )
    s2 = Session(
        session_id="sess_2",
        start_time=base_t + timedelta(hours=1),
        end_time=base_t + timedelta(hours=1, minutes=20),
        duration_seconds=1200.0,
        event_count=3,
    )
    s3 = Session(
        session_id="sess_3",
        start_time=base_t + timedelta(hours=3),
        end_time=base_t + timedelta(hours=3, minutes=15),
        duration_seconds=900.0,
        event_count=4,
    )

    db.record_session(s1)
    db.record_session(s2)
    db.record_session(s3)

    # 1. Fetch all
    all_sessions = db.get_sessions()
    assert len(all_sessions) == 3
    assert all_sessions[0].session_id == "sess_1"
    assert all_sessions[1].session_id == "sess_2"
    assert all_sessions[2].session_id == "sess_3"

    # 2. Limit
    limited = db.get_sessions(limit=2)
    assert len(limited) == 2
    assert limited[0].session_id == "sess_1"

    # 3. Time range filter
    range_filtered = db.get_sessions(
        start_time=base_t + timedelta(minutes=30),
        end_time=base_t + timedelta(hours=2),
    )
    assert len(range_filtered) == 1
    assert range_filtered[0].session_id == "sess_2"


def test_record_and_get_context_dna() -> None:
    """Verifies persisting and retrieving Context DNA records by dna_id and session_id."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    t0 = datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc)
    session = Session(
        session_id="sess_dna_test",
        start_time=t0,
        end_time=t0 + timedelta(minutes=5),
        duration_seconds=300.0,
        event_count=2,
        applications=["Code.exe"],
    )
    db.record_session(session)

    synthesizer = ContextDNASynthesizer()
    dna = synthesizer.synthesize(session)

    # Persist
    row_id = db.record_context_dna(dna)
    assert row_id > 0

    # Retrieve by dna_id
    by_dna = db.get_context_dna(dna.dna_id)
    assert by_dna is not None
    assert by_dna.dna_id == dna.dna_id
    assert by_dna.session_id == "sess_dna_test"
    assert by_dna.context_dna_version == "1.0"
    assert by_dna.application_context["active_applications"] == ["Code.exe"]

    # Retrieve by session_id
    by_session = db.get_context_dna("sess_dna_test")
    assert by_session is not None
    assert by_session.dna_id == dna.dna_id


def test_session_source_events_traceability() -> None:
    """Verifies end-to-end traceability from Session to source WorkspaceEvents."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    base_t = datetime(2026, 9, 9, 11, 0, 0, tzinfo=timezone.utc)

    e1 = WorkspaceEvent(
        event_id="trace_evt_1",
        timestamp=base_t,
        event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
        process_name="Code.exe",
        window_title="main.py - ORBIS",
    )
    e2 = WorkspaceEvent(
        event_id="trace_evt_2",
        timestamp=base_t + timedelta(minutes=2),
        event_type=WorkspaceEventType.WINDOW_ACTIVATED,
        process_name="chrome.exe",
        window_title="Google Search - Chrome",
    )

    db.record_workspace_event(e1)
    db.record_workspace_event(e2)

    # Segment
    segmenter = SessionSegmenter(session_gap_seconds=900.0)
    sessions = segmenter.segment([e1, e2])
    assert len(sessions) == 1
    session = sessions[0]
    db.record_session(session)

    # Trace back to source events via get_session_events
    traced_events = db.get_session_events(session.session_id)
    assert len(traced_events) == 2
    assert traced_events[0].event_id == "trace_evt_1"
    assert traced_events[0].process_name == "Code.exe"
    assert traced_events[1].event_id == "trace_evt_2"
    assert traced_events[1].process_name == "chrome.exe"


def test_nonexistent_queries_return_none() -> None:
    """Verifies that non-existent IDs return None or empty list gracefully."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    assert db.get_session("non_existent_id") is None
    assert db.get_context_dna("non_existent_id") is None
    assert db.get_session_events("non_existent_id") == []


def test_database_foreign_key_enforcement() -> None:
    """Verifies foreign key constraints prevent orphan Context DNA records."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    # Attempt to record Context DNA for a session_id that does not exist in sessions table
    synthesizer = ContextDNASynthesizer()
    orphan_session = Session(
        session_id="orphan_session_id",
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc),
        duration_seconds=0.0,
        event_count=1,
    )
    orphan_dna = synthesizer.synthesize(orphan_session)

    with pytest.raises(sqlite3.IntegrityError):
        db.record_context_dna(orphan_dna)


def test_get_session_events_non_chronological_insertion_order() -> None:
    """
    Verifies that get_session_events() returns events ordered by (timestamp ASC, event_id ASC),
    even when events were inserted into SQLite in an out-of-order sequence (verifying it does
    not rely on SQLite autoincrement row id).
    """
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    base_t = datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc)

    e_early = WorkspaceEvent(
        event_id="evt_01_early",
        timestamp=base_t,
        event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
        process_name="Code.exe",
    )
    e_mid = WorkspaceEvent(
        event_id="evt_02_mid",
        timestamp=base_t + timedelta(minutes=5),
        event_type=WorkspaceEventType.WINDOW_ACTIVATED,
        process_name="chrome.exe",
    )
    e_late = WorkspaceEvent(
        event_id="evt_03_late",
        timestamp=base_t + timedelta(minutes=10),
        event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
        process_name="Code.exe",
    )

    # Deliberately insert in reversed chronological order: late (id=1), early (id=2), mid (id=3)
    db.record_workspace_event(e_late)
    db.record_workspace_event(e_early)
    db.record_workspace_event(e_mid)

    session = Session(
        session_id="sess_out_of_order_test",
        start_time=base_t,
        end_time=base_t + timedelta(minutes=10),
        duration_seconds=600.0,
        event_count=3,
        event_ids=["evt_01_early", "evt_02_mid", "evt_03_late"],
    )
    db.record_session(session)

    retrieved = db.get_session_events(session.session_id)
    assert len(retrieved) == 3

    # Must be sorted by timestamp ASC, event_id ASC, NOT insertion order [evt_03_late, evt_01_early, evt_02_mid]
    retrieved_ids = [e.event_id for e in retrieved]
    assert retrieved_ids == ["evt_01_early", "evt_02_mid", "evt_03_late"]


def test_database_traceability_matches_context_dna_ordering() -> None:
    """
    Verifies that the event sequence returned by DatabaseManager.get_session_events()
    matches the event_id sequence in ContextDNA.activity_context['ordered_event_sequence']
    identically, including across events with identical timestamps.
    """
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    base_t = datetime(2026, 9, 9, 15, 0, 0, tzinfo=timezone.utc)

    # Include identical timestamps to test deterministic tie-breaking by event_id
    e1 = WorkspaceEvent(
        event_id="evt_b_same_time",
        timestamp=base_t,
        event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
        process_name="proc_b.exe",
    )
    e2 = WorkspaceEvent(
        event_id="evt_a_same_time",
        timestamp=base_t,
        event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
        process_name="proc_a.exe",
    )
    e3 = WorkspaceEvent(
        event_id="evt_c_later",
        timestamp=base_t + timedelta(minutes=3),
        event_type=WorkspaceEventType.WINDOW_ACTIVATED,
        process_name="proc_c.exe",
    )

    # Insert in mixed order
    db.record_workspace_event(e3)
    db.record_workspace_event(e1)
    db.record_workspace_event(e2)

    segmenter = SessionSegmenter(session_gap_seconds=900.0)
    sessions = segmenter.segment([e1, e2, e3])
    assert len(sessions) == 1
    session = sessions[0]
    db.record_session(session)

    synthesizer = ContextDNASynthesizer()
    dna = synthesizer.synthesize(session, [e1, e2, e3])
    db.record_context_dna(dna)

    # Query SQLite
    db_events = db.get_session_events(session.session_id)
    db_event_ids = [e.event_id for e in db_events]

    # Query Context DNA
    dna_event_ids = [e["event_id"] for e in dna.activity_context["ordered_event_sequence"]]

    # Both MUST match identically: ["evt_a_same_time", "evt_b_same_time", "evt_c_later"]
    assert db_event_ids == dna_event_ids
    assert db_event_ids == ["evt_a_same_time", "evt_b_same_time", "evt_c_later"]

