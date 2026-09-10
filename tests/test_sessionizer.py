"""
Unit tests for ORBIS Phase 3 Session Segmentation Engine (SessionSegmenter).
"""

from datetime import datetime, timedelta, timezone
import pytest
from pydantic import ValidationError

from core.continuity.sessionizer import IncrementalSessionBuilder, SessionSegmenter
from core.types import Session, WorkspaceEvent, WorkspaceEventType


def _make_event(
    offset_minutes: float,
    process_name: str = "Code.exe",
    resource_path: str = "c:\\Projects\\ORBIS\\main.py",
    event_id: str = None,
    base_time: datetime = None,
) -> WorkspaceEvent:
    if base_time is None:
        base_time = datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc)
    ts = base_time + timedelta(minutes=offset_minutes)
    kwargs = {
        "timestamp": ts,
        "event_type": WorkspaceEventType.APPLICATION_ACTIVATED,
        "process_name": process_name,
        "resource_path": resource_path,
        "window_title": f"{process_name} Window",
    }
    if event_id:
        kwargs["event_id"] = event_id
    return WorkspaceEvent(**kwargs)


def test_session_model_validation() -> None:
    """Verifies Session Pydantic model type checking and required fields."""
    t0 = datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=10)

    # Valid session instantiation
    session = Session(
        start_time=t0,
        end_time=t1,
        duration_seconds=600.0,
        event_count=2,
        applications=["Code.exe"],
        process_names=["Code.exe"],
        resources=["c:\\Projects\\ORBIS\\main.py"],
        event_ids=["evt_1", "evt_2"],
        confidence=1.0,
    )
    assert session.session_id is not None
    assert session.duration_seconds == 600.0
    assert session.event_count == 2
    assert session.confidence == 1.0

    # Missing required field raises ValidationError
    with pytest.raises(ValidationError):
        Session(start_time=t0)  # Missing end_time, duration_seconds, event_count


def test_empty_event_list() -> None:
    """Verifies behavior on empty event list."""
    segmenter = SessionSegmenter(session_gap_seconds=900.0)
    # segment() returns an empty list
    assert segmenter.segment([]) == []

    # create_session() raises ValueError
    with pytest.raises(ValueError, match="Cannot create a session from an empty event list"):
        segmenter.create_session([])


def test_single_event_session() -> None:
    """Verifies session creation for an isolated single workspace event."""
    segmenter = SessionSegmenter(session_gap_seconds=900.0)
    e = _make_event(0.0, process_name="notepad.exe", resource_path="c:\\notes.txt")

    sessions = segmenter.segment([e])
    assert len(sessions) == 1
    s = sessions[0]

    assert s.start_time == e.timestamp
    assert s.end_time == e.timestamp
    assert s.duration_seconds == 0.0
    assert s.event_count == 1
    assert s.applications == ["notepad.exe"]
    assert s.resources == ["c:\\notes.txt"]
    assert s.event_ids == [e.event_id]
    # Single event confidence is calibrated to 0.5
    assert s.confidence == 0.5


def test_multiple_events_same_session() -> None:
    """Verifies that events within the threshold are clustered into a single session."""
    segmenter = SessionSegmenter(session_gap_seconds=900.0)  # 15 minutes
    events = [
        _make_event(0.0, process_name="Code.exe"),
        _make_event(2.0, process_name="Code.exe"),
        _make_event(5.0, process_name="Code.exe"),
    ]
    sessions = segmenter.segment(events)
    assert len(sessions) == 1
    s = sessions[0]

    assert s.event_count == 3
    assert s.duration_seconds == 300.0  # 5 minutes
    assert s.confidence == 1.0
    assert s.applications == ["Code.exe"]
    assert len(s.event_ids) == 3


def test_large_inactivity_gap_creates_new_session() -> None:
    """Verifies that an inactivity gap exceeding the threshold creates a new session."""
    segmenter = SessionSegmenter(session_gap_seconds=900.0)  # 15 min gap threshold
    events = [
        _make_event(0.0, process_name="Code.exe"),
        _make_event(5.0, process_name="Code.exe"),
        # Gap of 30 minutes (> 15 min threshold)
        _make_event(35.0, process_name="chrome.exe"),
        _make_event(40.0, process_name="chrome.exe"),
    ]
    sessions = segmenter.segment(events)
    assert len(sessions) == 2

    s1, s2 = sessions[0], sessions[1]
    assert s1.event_count == 2
    assert s1.applications == ["Code.exe"]
    assert s1.duration_seconds == 300.0

    assert s2.event_count == 2
    assert s2.applications == ["chrome.exe"]
    assert s2.duration_seconds == 300.0


def test_small_gaps_remain_in_same_session() -> None:
    """Verifies that events with varying small gaps remain in a single session."""
    segmenter = SessionSegmenter(session_gap_seconds=600.0)  # 10 min threshold
    events = [
        _make_event(0.0),
        _make_event(3.0),
        _make_event(7.0),
        _make_event(12.0),
        _make_event(16.0),
    ]
    sessions = segmenter.segment(events)
    assert len(sessions) == 1
    assert sessions[0].event_count == 5
    assert sessions[0].duration_seconds == 16.0 * 60.0


def test_application_switching_within_session() -> None:
    """Verifies that application switches within one session are preserved in order."""
    segmenter = SessionSegmenter(session_gap_seconds=900.0)
    events = [
        _make_event(0.0, process_name="Code.exe"),
        _make_event(2.0, process_name="chrome.exe"),
        _make_event(4.0, process_name="Slack.exe"),
    ]
    sessions = segmenter.segment(events)
    assert len(sessions) == 1
    assert sessions[0].applications == ["Code.exe", "chrome.exe", "Slack.exe"]
    assert sessions[0].process_names == ["Code.exe", "chrome.exe", "Slack.exe"]


def test_returning_to_previous_application() -> None:
    """Verifies deduplication of application names when returning to a previously active app."""
    segmenter = SessionSegmenter(session_gap_seconds=900.0)
    events = [
        _make_event(0.0, process_name="Code.exe"),
        _make_event(2.0, process_name="chrome.exe"),
        _make_event(4.0, process_name="Code.exe"),
    ]
    sessions = segmenter.segment(events)
    assert len(sessions) == 1
    # applications list maintains unique process names preserving first-seen order
    assert sessions[0].applications == ["Code.exe", "chrome.exe"]
    # All 3 events are retained
    assert sessions[0].event_count == 3
    assert len(sessions[0].event_ids) == 3


def test_identical_timestamps_deterministic_tiebreaker() -> None:
    """Verifies deterministic sorting when multiple events have identical timestamps."""
    segmenter = SessionSegmenter(session_gap_seconds=900.0)
    base_t = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)

    # Two events with exact same timestamp, but distinct event_ids
    e1 = _make_event(0.0, process_name="proc_b.exe", event_id="id_2", base_time=base_t)
    e2 = _make_event(0.0, process_name="proc_a.exe", event_id="id_1", base_time=base_t)

    # Pass in reverse order [e1 (id_2), e2 (id_1)]
    sessions = segmenter.segment([e1, e2])
    assert len(sessions) == 1
    s = sessions[0]

    # Must be deterministically ordered by (timestamp, event_id): id_1 before id_2
    assert s.event_ids == ["id_1", "id_2"]
    assert s.duration_seconds == 0.0
    # Multi-event with zero duration confidence = 0.8
    assert s.confidence == 0.8


def test_out_of_order_events_deterministic_sorting() -> None:
    """Verifies that unsorted/out-of-order events are deterministically sorted."""
    segmenter = SessionSegmenter(session_gap_seconds=900.0)
    e1 = _make_event(0.0, process_name="app1.exe", event_id="evt_01")
    e2 = _make_event(5.0, process_name="app2.exe", event_id="evt_02")
    e3 = _make_event(10.0, process_name="app3.exe", event_id="evt_03")

    # Pass in shuffled order
    sessions = segmenter.segment([e3, e1, e2])
    assert len(sessions) == 1
    s = sessions[0]

    assert s.event_ids == ["evt_01", "evt_02", "evt_03"]
    assert s.start_time == e1.timestamp
    assert s.end_time == e3.timestamp
    assert s.duration_seconds == 600.0


def test_resource_aggregation_and_deduplication() -> None:
    """Verifies factual resource paths are collected, deduplicated, and preserved."""
    segmenter = SessionSegmenter(session_gap_seconds=900.0)
    events = [
        _make_event(0.0, resource_path="c:\\project\\app.py"),
        _make_event(2.0, resource_path="c:\\project\\test.py"),
        _make_event(4.0, resource_path="c:\\project\\app.py"),  # Duplicate
        _make_event(6.0, resource_path=None),  # None resource
    ]
    session = segmenter.create_session(events)
    assert session.resources == ["c:\\project\\app.py", "c:\\project\\test.py"]
    assert session.session_metadata["unique_resources_count"] == 2


def test_incremental_session_builder() -> None:
    """Verifies real-time / streaming incremental session construction."""
    builder = IncrementalSessionBuilder(session_gap_seconds=600.0)  # 10 min

    e1 = _make_event(0.0, process_name="Code.exe")
    res1 = builder.add_event(e1)
    assert res1 is None
    assert builder.buffered_event_count == 1

    e2 = _make_event(3.0, process_name="Code.exe")
    res2 = builder.add_event(e2)
    assert res2 is None
    assert builder.buffered_event_count == 2

    # e3 arrives 20 minutes later (> 10 min threshold)
    e3 = _make_event(23.0, process_name="chrome.exe")
    finalized_session = builder.add_event(e3)
    assert finalized_session is not None
    assert finalized_session.event_count == 2
    assert finalized_session.applications == ["Code.exe"]
    assert builder.buffered_event_count == 1

    # Finalize remaining buffer
    trailing_session = builder.finalize()
    assert trailing_session is not None
    assert trailing_session.event_count == 1
    assert trailing_session.applications == ["chrome.exe"]
    assert builder.buffered_event_count == 0

    # Finalize on empty returns None
    assert builder.finalize() is None


def test_malformed_and_boundary_events() -> None:
    """Verifies that events with None or empty attributes are processed safely without crashing."""
    segmenter = SessionSegmenter(session_gap_seconds=900.0)
    t0 = datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc)

    # Events with None process_name, None resource_path, empty metadata
    e1 = WorkspaceEvent(
        event_id="malformed_1",
        timestamp=t0,
        event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
        process_name=None,
        resource_path=None,
        window_title=None,
    )
    e2 = WorkspaceEvent(
        event_id="malformed_2",
        timestamp=t0 + timedelta(minutes=1),
        event_type=WorkspaceEventType.WINDOW_ACTIVATED,
        process_name="",
        resource_path="",
        window_title="",
    )

    sessions = segmenter.segment([e1, e2])
    assert len(sessions) == 1
    s = sessions[0]
    assert s.event_count == 2
    # None and empty strings should be safely ignored in applications and resources
    assert s.applications == []
    assert s.resources == []
    assert s.event_ids == ["malformed_1", "malformed_2"]

