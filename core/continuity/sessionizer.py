"""
Session Segmentation Engine for ORBIS Phase 3.
Transforms raw chronological workspace events into bounded, factual workspace sessions
using deterministic temporal thresholding and observable continuity rules.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional

from core.config import get_settings
from core.types import Session, WorkspaceEvent

logger = logging.getLogger("orbis.continuity.sessionizer")


class SessionSegmenter:
    """
    Deterministic rule-based session segmenter.
    Clusters sequential workspace events into discrete work sessions based on
    configurable inactivity gap thresholds.
    """

    def __init__(self, session_gap_seconds: Optional[float] = None) -> None:
        if session_gap_seconds is not None:
            self.session_gap_seconds = session_gap_seconds
        else:
            settings = get_settings()
            self.session_gap_seconds = settings.session_gap_minutes * 60.0

    def calculate_confidence(self, events: List[WorkspaceEvent], duration_seconds: float) -> float:
        """
        Calculates a deterministic session-quality heuristic score (0.0 to 1.0) for a session.
        This is a heuristic quality score, NOT a statistical probability or confidence calibration.
        - Single isolated events carry 0.5 heuristic quality (minimal temporal evidence).
        - Multi-event sessions with non-zero duration carry 1.0 heuristic quality.
        - Multi-event sessions where all events share the exact same timestamp carry 0.8 heuristic quality.
        """
        if not events:
            return 0.0
        if len(events) == 1:
            return 0.5
        if duration_seconds == 0.0:
            return 0.8
        return 1.0

    def create_session(self, events: List[WorkspaceEvent]) -> Session:
        """
        Aggregates a contiguous chunk of workspace events into a factual Session model.
        Extracts observed applications, resources, and event IDs without inferring intent.
        """
        if not events:
            raise ValueError("Cannot create a session from an empty event list.")

        # Explicit deterministic sorting by timestamp, then event_id
        sorted_events = sorted(events, key=lambda e: (e.timestamp, e.event_id))

        start_time = sorted_events[0].timestamp
        end_time = sorted_events[-1].timestamp
        duration_seconds = max(0.0, (end_time - start_time).total_seconds())
        event_count = len(sorted_events)

        # Factual extraction: ordered, deduplicated applications and resources
        applications = list(
            dict.fromkeys(e.process_name for e in sorted_events if e.process_name)
        )
        resources = list(
            dict.fromkeys(e.resource_path for e in sorted_events if e.resource_path)
        )
        event_ids = [e.event_id for e in sorted_events]

        confidence = self.calculate_confidence(sorted_events, duration_seconds)

        avg_interval = (
            round(duration_seconds / (event_count - 1), 2)
            if event_count > 1
            else 0.0
        )

        metadata: Dict[str, Any] = {
            "first_process": applications[0] if applications else None,
            "last_process": sorted_events[-1].process_name,
            "unique_applications_count": len(applications),
            "unique_resources_count": len(resources),
            "average_event_interval_seconds": avg_interval,
        }

        return Session(
            start_time=start_time,
            end_time=end_time,
            duration_seconds=duration_seconds,
            event_count=event_count,
            applications=applications,
            process_names=applications,
            resources=resources,
            event_ids=event_ids,
            session_metadata=metadata,
            confidence=confidence,
        )

    def segment(self, events: List[WorkspaceEvent]) -> List[Session]:
        """
        Segments an arbitrary list of workspace events into one or more discrete Sessions.
        Events are deterministically sorted by (timestamp, event_id).
        A new session boundary is created whenever the time delta between consecutive
        events exceeds session_gap_seconds.
        """
        if not events:
            return []

        # 1. Deterministic sorting
        sorted_events = sorted(events, key=lambda e: (e.timestamp, e.event_id))

        sessions: List[Session] = []
        current_chunk: List[WorkspaceEvent] = [sorted_events[0]]

        # 2. Sequential linear scan with threshold demarcation
        for event in sorted_events[1:]:
            last_event = current_chunk[-1]
            time_gap = (event.timestamp - last_event.timestamp).total_seconds()

            if time_gap > self.session_gap_seconds:
                # Inactivity gap threshold exceeded: finalize current chunk as a session
                sessions.append(self.create_session(current_chunk))
                current_chunk = [event]
            else:
                # Temporal gap within session threshold: continue accumulating
                current_chunk.append(event)

        # 3. Finalize trailing chunk
        if current_chunk:
            sessions.append(self.create_session(current_chunk))

        return sessions


class IncrementalSessionBuilder:
    """
    Incremental session builder for streaming/real-time event ingestion.
    Maintains an active session buffer and automatically finalizes the session
    when an arriving event exceeds the session gap threshold.

    CONTRACT:
    - Expects events to arrive strictly in chronological order.
    - Intended for real-time ordered event streams.
    - Out-of-order historical data must use SessionSegmenter.segment batch segmentation instead.
    """

    def __init__(self, session_gap_seconds: Optional[float] = None) -> None:
        self.segmenter = SessionSegmenter(session_gap_seconds=session_gap_seconds)
        self._buffer: List[WorkspaceEvent] = []

    @property
    def buffered_event_count(self) -> int:
        return len(self._buffer)

    def add_event(self, event: WorkspaceEvent) -> Optional[Session]:
        """
        Ingests a new workspace event into the active buffer.
        Returns a finalized Session if the new event creates a boundary gap,
        or None if the event was appended to the active session.
        """
        if not self._buffer:
            self._buffer.append(event)
            return None

        last_event = self._buffer[-1]
        time_gap = (event.timestamp - last_event.timestamp).total_seconds()

        if time_gap > self.segmenter.session_gap_seconds:
            # Time gap creates a session boundary: finalize existing buffer
            finalized_session = self.segmenter.create_session(self._buffer)
            self._buffer = [event]
            return finalized_session

        self._buffer.append(event)
        return None

    def finalize(self) -> Optional[Session]:
        """
        Flushes and finalizes any remaining events in the buffer into a Session.
        Returns None if buffer was empty.
        """
        if not self._buffer:
            return None
        session = self.segmenter.create_session(self._buffer)
        self._buffer = []
        return session
