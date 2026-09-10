"""
Context DNA Synthesizer for ORBIS Phase 3.
Transforms bounded Sessions and raw WorkspaceEvents into structured, explainable,
and deterministic Context DNA records across 6 factual dimensions.
"""

from collections import Counter
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from core.types import ContextDNA, Session, WorkspaceEvent

logger = logging.getLogger("orbis.continuity.dna")

CURRENT_CONTEXT_DNA_VERSION = "1.0"


class ContextDNASynthesizer:
    """
    Deterministic synthesizer for ORBIS Context DNA v1.0.
    Produces structured, explainable, factual snapshots of workspace sessions
    strictly from observed workspace evidence. No LLM or generative inferences.

    VERSIONING:
    context_dna_version identifies the schema format and enables future schema
    migration or compatibility handling.
    """

    def __init__(self, version: str = CURRENT_CONTEXT_DNA_VERSION) -> None:
        self.version = version

    def synthesize(
        self,
        session: Session,
        events: Optional[List[WorkspaceEvent]] = None,
    ) -> ContextDNA:
        """
        Synthesizes a ContextDNA record from a Session and optional source WorkspaceEvents.
        Ensures strict traceability back to session_id and source_event_ids.
        """
        # Sort and filter events deterministically if provided
        sorted_events: List[WorkspaceEvent] = []
        if events:
            if session.event_ids:
                id_set = set(session.event_ids)
                session_events = [e for e in events if e.event_id in id_set]
            else:
                session_events = events
            sorted_events = sorted(session_events, key=lambda e: (e.timestamp, e.event_id))

        # 1. Temporal Context
        temporal_context = self._build_temporal_context(session, sorted_events)

        # 2. Application Context
        application_context = self._build_application_context(session, sorted_events)

        # 3. Resource Context
        resource_context = self._build_resource_context(session, sorted_events)

        # 4. Activity Context
        activity_context = self._build_activity_context(session, sorted_events)

        # 5. Privacy Context
        privacy_context = self._build_privacy_context(session, sorted_events)

        # 6. Quality Indicators
        quality_indicators = self._build_quality_indicators(session, sorted_events)

        # Traceability: source event IDs
        source_event_ids = list(session.event_ids)
        if not source_event_ids and sorted_events:
            source_event_ids = [e.event_id for e in sorted_events]

        return ContextDNA(
            session_id=session.session_id,
            context_dna_version=self.version,
            created_at=datetime.now(timezone.utc),
            temporal_context=temporal_context,
            application_context=application_context,
            resource_context=resource_context,
            activity_context=activity_context,
            privacy_context=privacy_context,
            quality_indicators=quality_indicators,
            source_event_ids=source_event_ids,
        )

    def _build_temporal_context(
        self, session: Session, events: List[WorkspaceEvent]
    ) -> Dict[str, Any]:
        """Factual temporal metrics describing the session."""
        avg_interval = (
            session.session_metadata.get("average_event_interval_seconds", 0.0)
            if session.session_metadata
            else 0.0
        )
        return {
            "session_start": session.start_time.isoformat(),
            "session_end": session.end_time.isoformat(),
            "duration_seconds": session.duration_seconds,
            "event_count": session.event_count,
            "average_event_interval_seconds": avg_interval,
            "first_event_id": session.event_ids[0] if session.event_ids else None,
            "last_event_id": session.event_ids[-1] if session.event_ids else None,
        }

    def _build_application_context(
        self, session: Session, events: List[WorkspaceEvent]
    ) -> Dict[str, Any]:
        """Factual application metrics and observed transitions."""
        active_apps = list(session.applications)

        # Derive primary application (highest occurrence count)
        primary_app = active_apps[0] if active_apps else None
        transitions: List[Dict[str, Any]] = []

        if events:
            app_counts = Counter(e.process_name for e in events if e.process_name)
            if app_counts:
                primary_app = app_counts.most_common(1)[0][0]

            # Detect application switches
            prev_app: Optional[str] = None
            for e in events:
                curr_app = e.process_name
                if curr_app and prev_app and curr_app != prev_app:
                    transitions.append(
                        {
                            "from_application": prev_app,
                            "to_application": curr_app,
                            "timestamp": e.timestamp.isoformat(),
                        }
                    )
                if curr_app:
                    prev_app = curr_app
        elif len(active_apps) > 1:
            for i in range(len(active_apps) - 1):
                transitions.append(
                    {
                        "from_application": active_apps[i],
                        "to_application": active_apps[i + 1],
                        "timestamp": None,
                    }
                )

        return {
            "active_applications": active_apps,
            "process_names": active_apps,
            "primary_application": primary_app,
            "application_count": len(active_apps),
            "application_transitions": transitions,
            "transition_count": len(transitions),
        }

    def _build_resource_context(
        self, session: Session, events: List[WorkspaceEvent]
    ) -> Dict[str, Any]:
        """Factual observed resource metadata (paths, extensions, directories)."""
        observed_paths = list(session.resources)
        if not observed_paths and events:
            observed_paths = list(
                dict.fromkeys(e.resource_path for e in events if e.resource_path)
            )

        extensions: List[str] = []
        directories: List[str] = []

        for p_str in observed_paths:
            try:
                p = Path(p_str)
                suffix = p.suffix.lower()
                if suffix and suffix not in extensions:
                    extensions.append(suffix)
                parent_dir = str(p.parent)
                if parent_dir and parent_dir not in directories:
                    directories.append(parent_dir)
            except Exception:
                continue

        return {
            "observed_resource_paths": observed_paths,
            "resource_count": len(observed_paths),
            "resource_extensions": extensions,
            "workspace_directories": directories,
        }

    def _build_activity_context(
        self, session: Session, events: List[WorkspaceEvent]
    ) -> Dict[str, Any]:
        """Factual event tally and chronological event sequence."""
        event_types: Dict[str, int] = {}
        ordered_sequence: List[Dict[str, Any]] = []

        if events:
            for e in events:
                t_str = e.event_type.value
                event_types[t_str] = event_types.get(t_str, 0) + 1
                ordered_sequence.append(
                    {
                        "event_id": e.event_id,
                        "event_type": t_str,
                        "process_name": e.process_name,
                        "window_title": e.window_title,
                        "timestamp": e.timestamp.isoformat(),
                        "resource_path": e.resource_path,
                    }
                )
        else:
            ordered_sequence = [
                {"event_id": eid} for eid in session.event_ids
            ]

        return {
            "event_count": session.event_count,
            "event_types": event_types,
            "ordered_event_sequence": ordered_sequence,
        }

    def _build_privacy_context(
        self, session: Session, events: List[WorkspaceEvent]
    ) -> Dict[str, Any]:
        """Factual privacy indicators preserving Phase 2 boundary integrity."""
        redaction_count = 0
        if events:
            for e in events:
                if e.metadata and e.metadata.get("redacted"):
                    redaction_count += 1

        return {
            "privacy_exclusions_active": True,
            "contains_sensitive_content": False,
            "redaction_count": redaction_count,
            "boundary_rule": "phase2_privacy_boundary_enforced",
        }

    def _build_quality_indicators(
        self, session: Session, events: List[WorkspaceEvent]
    ) -> Dict[str, Any]:
        """Measurable, explainable session quality indicators."""
        density = 0.0
        if session.duration_seconds > 0:
            duration_minutes = session.duration_seconds / 60.0
            density = round(session.event_count / duration_minutes, 2)
        else:
            density = float(session.event_count)

        completeness = "complete" if session.event_count >= 2 else "single_event"
        if session.confidence >= 1.0:
            quality = "high"
        elif session.confidence >= 0.8:
            quality = "moderate"
        else:
            quality = "low"

        return {
            "completeness_indicator": completeness,
            "data_quality": quality,
            "boundary_confidence": session.confidence,
            "event_density_per_minute": density,
        }
