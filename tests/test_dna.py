"""
Unit tests for ORBIS Phase 3 Context DNA Synthesizer (ContextDNASynthesizer).
"""

from datetime import datetime, timedelta, timezone
import json
import pytest

from core.continuity.dna import ContextDNASynthesizer, CURRENT_CONTEXT_DNA_VERSION
from core.types import ContextDNA, Session, WorkspaceEvent, WorkspaceEventType


def _make_sample_session_and_events():
    base_t = datetime(2026, 9, 9, 14, 0, 0, tzinfo=timezone.utc)
    e1 = WorkspaceEvent(
        event_id="evt_001",
        timestamp=base_t,
        event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
        process_name="Code.exe",
        window_title="main.py - ORBIS",
        resource_path="c:\\Projects\\ORBIS\\main.py",
    )
    e2 = WorkspaceEvent(
        event_id="evt_002",
        timestamp=base_t + timedelta(minutes=3),
        event_type=WorkspaceEventType.WINDOW_ACTIVATED,
        process_name="chrome.exe",
        window_title="Python Documentation - Chrome",
        resource_path="https://docs.python.org",
        metadata={"redacted": True},
    )
    e3 = WorkspaceEvent(
        event_id="evt_003",
        timestamp=base_t + timedelta(minutes=6),
        event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
        process_name="Code.exe",
        window_title="README.md - ORBIS",
        resource_path="c:\\Projects\\ORBIS\\README.md",
    )

    session = Session(
        session_id="sess_12345",
        start_time=base_t,
        end_time=base_t + timedelta(minutes=6),
        duration_seconds=360.0,
        event_count=3,
        applications=["Code.exe", "chrome.exe"],
        process_names=["Code.exe", "chrome.exe"],
        resources=["c:\\Projects\\ORBIS\\main.py", "https://docs.python.org", "c:\\Projects\\ORBIS\\README.md"],
        event_ids=["evt_001", "evt_002", "evt_003"],
        session_metadata={"average_event_interval_seconds": 180.0},
        confidence=1.0,
    )
    return session, [e1, e2, e3]


def test_context_dna_version_and_metadata() -> None:
    """Verifies that Context DNA includes the required explicit version '1.0'."""
    synthesizer = ContextDNASynthesizer()
    assert synthesizer.version == "1.0"
    assert CURRENT_CONTEXT_DNA_VERSION == "1.0"

    session, events = _make_sample_session_and_events()
    dna = synthesizer.synthesize(session, events)

    assert dna.context_dna_version == "1.0"
    assert dna.session_id == session.session_id
    assert dna.dna_id is not None
    assert dna.created_at is not None


def test_context_dna_six_factual_categories() -> None:
    """Verifies that all 6 required factual categories are accurately synthesized."""
    synthesizer = ContextDNASynthesizer()
    session, events = _make_sample_session_and_events()
    dna = synthesizer.synthesize(session, events)

    # A. Temporal Context
    temporal = dna.temporal_context
    assert temporal["session_start"] == session.start_time.isoformat()
    assert temporal["session_end"] == session.end_time.isoformat()
    assert temporal["duration_seconds"] == 360.0
    assert temporal["event_count"] == 3
    assert temporal["average_event_interval_seconds"] == 180.0
    assert temporal["first_event_id"] == "evt_001"
    assert temporal["last_event_id"] == "evt_003"

    # B. Application Context
    app_ctx = dna.application_context
    assert app_ctx["active_applications"] == ["Code.exe", "chrome.exe"]
    assert app_ctx["primary_application"] == "Code.exe"  # Occurred 2 times vs 1
    assert app_ctx["application_count"] == 2
    # Transitions: Code.exe -> chrome.exe -> Code.exe (2 transitions)
    assert app_ctx["transition_count"] == 2
    assert app_ctx["application_transitions"][0]["from_application"] == "Code.exe"
    assert app_ctx["application_transitions"][0]["to_application"] == "chrome.exe"
    assert app_ctx["application_transitions"][1]["from_application"] == "chrome.exe"
    assert app_ctx["application_transitions"][1]["to_application"] == "Code.exe"

    # C. Resource Context
    res_ctx = dna.resource_context
    assert len(res_ctx["observed_resource_paths"]) == 3
    assert ".py" in res_ctx["resource_extensions"]
    assert ".md" in res_ctx["resource_extensions"]
    assert len(res_ctx["workspace_directories"]) > 0

    # D. Activity Context
    act_ctx = dna.activity_context
    assert act_ctx["event_count"] == 3
    assert act_ctx["event_types"]["APPLICATION_ACTIVATED"] == 2
    assert act_ctx["event_types"]["WINDOW_ACTIVATED"] == 1
    assert len(act_ctx["ordered_event_sequence"]) == 3
    assert act_ctx["ordered_event_sequence"][0]["event_id"] == "evt_001"

    # E. Privacy Context
    priv_ctx = dna.privacy_context
    assert priv_ctx["privacy_exclusions_active"] is True
    assert priv_ctx["contains_sensitive_content"] is False
    assert priv_ctx["redaction_count"] == 1  # evt_002 had redacted: True

    # F. Quality Indicators
    qual_ctx = dna.quality_indicators
    assert qual_ctx["completeness_indicator"] == "complete"
    assert qual_ctx["data_quality"] == "high"
    assert qual_ctx["boundary_confidence"] == 1.0
    assert qual_ctx["event_density_per_minute"] == 0.5  # 3 events / 6 minutes


def test_context_dna_traceability() -> None:
    """Verifies that Context DNA maintains strict traceability back to source events."""
    synthesizer = ContextDNASynthesizer()
    session, events = _make_sample_session_and_events()
    dna = synthesizer.synthesize(session, events)

    assert dna.source_event_ids == ["evt_001", "evt_002", "evt_003"]
    assert dna.session_id == "sess_12345"


def test_context_dna_serialization_and_deserialization() -> None:
    """Verifies that Context DNA roundtrips flawlessly through JSON serialization."""
    synthesizer = ContextDNASynthesizer()
    session, events = _make_sample_session_and_events()
    dna = synthesizer.synthesize(session, events)

    # Pydantic JSON dump
    json_str = dna.model_dump_json()
    assert isinstance(json_str, str)
    data = json.loads(json_str)
    assert data["context_dna_version"] == "1.0"
    assert data["session_id"] == "sess_12345"

    # Pydantic JSON load
    dna_restored = ContextDNA.model_validate_json(json_str)
    assert dna_restored.dna_id == dna.dna_id
    assert dna_restored.session_id == dna.session_id
    assert dna_restored.source_event_ids == dna.source_event_ids
    assert dna_restored.temporal_context == dna.temporal_context
    assert dna_restored.quality_indicators == dna.quality_indicators


def test_context_dna_without_events_fallback() -> None:
    """Verifies Context DNA generation when raw events list is not provided."""
    synthesizer = ContextDNASynthesizer()
    session, _ = _make_sample_session_and_events()
    dna = synthesizer.synthesize(session, events=None)

    assert dna.session_id == session.session_id
    assert dna.temporal_context["event_count"] == 3
    assert dna.application_context["active_applications"] == ["Code.exe", "chrome.exe"]
    assert dna.source_event_ids == session.event_ids
    assert dna.quality_indicators["data_quality"] == "high"


def test_context_dna_deterministic_output() -> None:
    """Verifies that synthesis on identical input produces identical category content."""
    synthesizer = ContextDNASynthesizer()
    session, events = _make_sample_session_and_events()

    dna1 = synthesizer.synthesize(session, events)
    dna2 = synthesizer.synthesize(session, events)

    assert dna1.temporal_context == dna2.temporal_context
    assert dna1.application_context == dna2.application_context
    assert dna1.resource_context == dna2.resource_context
    assert dna1.activity_context == dna2.activity_context
    assert dna1.privacy_context == dna2.privacy_context
    assert dna1.quality_indicators == dna2.quality_indicators
    assert dna1.source_event_ids == dna2.source_event_ids
