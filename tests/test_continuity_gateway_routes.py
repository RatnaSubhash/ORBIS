"""
Unit and integration tests for ORBIS Phase F Continuity Gateway Routes.
Tests:
1. GET /api/continuity/context returns 200 OK.
2. Response conforms to the declared Pydantic schema.
3. Empty database produces a valid empty state rather than 500.
4. Real recorded session is serialized correctly.
5. Context DNA serialization is accurate.
6. Knowledge Graph counts and entity breakdown are accurate.
7. Session event endpoint returns the correct sanitized events.
8. Sensitive credentials and secret assignments are scrubbed.
9. Invalid or unknown session ID returns appropriate 400/404.
"""

from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from core.gateway.app import create_app
from core.memory.db import DatabaseManager
from core.observation.mock import MockWorkspaceObserver
from core.observation.service import WorkspaceObservationService
from core.types import ContextDNA, Session, WorkspaceEvent, WorkspaceEventType


@pytest.fixture
def temp_db(tmp_path: Path) -> DatabaseManager:
    """Provides a fresh isolated SQLite database initialized with schema migrations."""
    db_file = tmp_path / "test_continuity_gateway.db"
    db = DatabaseManager(db_path=str(db_file))
    db.initialize()
    return db


@pytest.fixture
def mock_obs_service(temp_db: DatabaseManager) -> WorkspaceObservationService:
    """Provides an observation service with Mock observer."""
    observer = MockWorkspaceObserver()
    service = WorkspaceObservationService(
        observer=observer,
        db_manager=temp_db,
        polling_interval=5.0,
    )
    return service


@pytest.fixture
def client(temp_db: DatabaseManager, mock_obs_service: WorkspaceObservationService):
    """Provides a TestClient with dependencies wired to temp_db and mock_obs_service."""
    app = create_app(
        db_manager=temp_db,
        observation_service=mock_obs_service,
    )
    with TestClient(app) as test_client:
        yield test_client


def test_continuity_context_empty_db(client: TestClient):
    """Test 1-3: Empty database returns 200 OK with valid empty schema (never 500)."""
    resp = client.get("/api/continuity/context")
    assert resp.status_code == 200
    data = resp.json()

    # Observation status
    assert "observation" in data
    assert isinstance(data["observation"]["active"], bool)
    assert data["observation"]["active"] is True
    assert data["observation"]["polling_interval"] == 5.0
    assert data["observation"]["total_events"] == 0

    # No current Context DNA in empty DB
    assert data["current_dna"] is None

    # Empty sessions list
    assert data["recent_sessions"] == []

    # Empty Knowledge Graph summary
    assert "knowledge_graph" in data
    kg = data["knowledge_graph"]
    assert kg["total_nodes"] == 0
    assert kg["total_edges"] == 0
    assert kg["application_count"] == 0
    assert kg["file_count"] == 0
    assert kg["topic_count"] == 0
    assert kg["top_entities"] == []


def test_continuity_context_with_recorded_session_and_dna(
    client: TestClient, temp_db: DatabaseManager
):
    """Test 4-6: Recorded session, Context DNA, and Knowledge Graph nodes are serialized accurately."""
    now = datetime.now(timezone.utc)

    # 1. Record a real Session
    session = Session(
        session_id="sess_alpha_123",
        start_time=now - timedelta(minutes=15),
        end_time=now,
        duration_seconds=900.0,
        event_count=12,
        applications=["Antigravity IDE.exe", "chrome.exe"],
        process_names=["Antigravity IDE.exe", "chrome.exe"],
        resources=["c:\\Projects\\ORBIS\\README.md", "c:\\Projects\\ORBIS\\frontend\\src\\App.jsx"],
        event_ids=["ev_1", "ev_2"],
        confidence=0.95,
    )
    temp_db.record_session(session)

    # 2. Record Context DNA for the session
    dna = ContextDNA(
        dna_id="dna_alpha_456",
        session_id="sess_alpha_123",
        context_dna_version="1.0",
        created_at=now,
        temporal_context={
            "session_start": session.start_time.isoformat(),
            "session_end": session.end_time.isoformat(),
            "duration_seconds": 900.0,
            "event_count": 12,
        },
        application_context={
            "primary_application": "Antigravity IDE.exe",
            "active_applications": ["Antigravity IDE.exe", "chrome.exe"],
            "application_count": 2,
        },
        resource_context={
            "primary_resource": "c:\\Projects\\ORBIS\\README.md",
            "observed_resource_paths": ["c:\\Projects\\ORBIS\\README.md", "c:\\Projects\\ORBIS\\frontend\\src\\App.jsx"],
            "projects": ["C:\\Projects\\ORBIS"],
        },
        activity_context={
            "task_summary": "Editing ORBIS architecture documentation",
            "dominant_activity": "Coding assistance & workspace continuity",
        },
        privacy_context={},
        quality_indicators={
            "focus_score": 0.88,
            "confidence_score": 0.95,
        },
        source_event_ids=["ev_1", "ev_2"],
    )
    temp_db.record_context_dna(dna)

    # 3. Record Knowledge Graph nodes and edges
    temp_db.record_graph_node("node_app_ide", "application", "Antigravity IDE.exe", {})
    temp_db.record_graph_node("node_file_readme", "file", "README.md", {})
    temp_db.record_graph_node("node_proj_orbis", "project", "ORBIS", {})
    temp_db.record_graph_edge("node_app_ide", "node_file_readme", "OPENS", {})
    temp_db.record_graph_edge("node_file_readme", "node_proj_orbis", "BELONGS_TO", {})

    # Fetch context
    resp = client.get("/api/continuity/context")
    assert resp.status_code == 200
    data = resp.json()

    # Verify session serialization
    assert len(data["recent_sessions"]) == 1
    sess_item = data["recent_sessions"][0]
    assert sess_item["session_id"] == "sess_alpha_123"
    assert sess_item["duration_seconds"] == 900.0
    assert sess_item["event_count"] == 12
    assert "Antigravity IDE.exe" in sess_item["applications"]
    assert sess_item["confidence"] == 0.95

    # Verify Context DNA serialization
    assert data["current_dna"] is not None
    dna_item = data["current_dna"]
    assert dna_item["dna_id"] == "dna_alpha_456"
    assert dna_item["session_id"] == "sess_alpha_123"
    assert dna_item["freshness"] == "persisted"
    assert dna_item["task_summary"] == "Editing ORBIS architecture documentation"
    assert dna_item["focus_score"] == 0.88
    assert dna_item["project_root"] == "C:\\Projects\\ORBIS"
    assert dna_item["primary_application"] == "Antigravity IDE.exe"
    assert "chrome.exe" in dna_item["active_applications"]

    # Verify Knowledge Graph telemetry
    kg = data["knowledge_graph"]
    assert kg["total_nodes"] == 3
    assert kg["total_edges"] == 2
    assert kg["application_count"] == 1
    assert kg["file_count"] == 1
    assert kg["topic_count"] == 1
    assert len(kg["top_entities"]) == 3


def test_session_events_sanitization(client: TestClient, temp_db: DatabaseManager):
    """Test 7-8: Session events endpoint returns sanitized events and scrubs secrets."""
    now = datetime.now(timezone.utc)

    # Record WorkspaceEvents with sensitive tokens to verify redaction
    ev1 = WorkspaceEvent(
        event_id="ev_safe_01",
        timestamp=now - timedelta(minutes=5),
        event_type=WorkspaceEventType.WINDOW_ACTIVATED,
        process_name="Antigravity IDE.exe",
        process_id=1234,
        window_title="README.md - ORBIS",
        resource_path="c:\\Projects\\ORBIS\\README.md",
        metadata={"file": "README.md"},
    )
    ev2 = WorkspaceEvent(
        event_id="ev_sensitive_02",
        timestamp=now,
        event_type=WorkspaceEventType.WINDOW_ACTIVATED,
        process_name="chrome.exe",
        process_id=5678,
        window_title="https://login.example.com?api_key=secret_xyz123&password=supersecretpassword",
        resource_path="c:\\Projects\\ORBIS\\.env",
        metadata={"token": "bearer_secret_abc123"},
    )
    temp_db.record_workspace_event(ev1)
    temp_db.record_workspace_event(ev2)

    session = Session(
        session_id="sess_sec_999",
        start_time=now - timedelta(minutes=5),
        end_time=now,
        duration_seconds=300.0,
        event_count=2,
        applications=["Antigravity IDE.exe", "chrome.exe"],
        process_names=["Antigravity IDE.exe", "chrome.exe"],
        resources=["c:\\Projects\\ORBIS\\README.md"],
        event_ids=["ev_safe_01", "ev_sensitive_02"],
        confidence=1.0,
    )
    temp_db.record_session(session)

    resp = client.get("/api/continuity/sessions/sess_sec_999/events")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "sess_sec_999"
    assert data["total"] == 2
    assert len(data["events"]) == 2

    # Check that secrets are scrubbed
    ev2_res = data["events"][1]
    assert "supersecretpassword" not in ev2_res["window_title"]
    assert "secret_xyz123" not in ev2_res["window_title"]
    # Token key in metadata should be masked or dropped
    assert "bearer_secret_abc123" not in str(ev2_res["metadata"])


def test_session_events_unknown_session(client: TestClient):
    """Test 9: Requesting events for non-existent session returns 404."""
    resp = client.get("/api/continuity/sessions/nonexistent_session_id/events")
    assert resp.status_code == 404
    assert "not found" in resp.json()["error"].lower()


def test_session_events_invalid_id(client: TestClient):
    """Test 9: Invalid session ID format returns 400 Bad Request."""
    resp = client.get("/api/continuity/sessions/invalid!id@bad/events")
    assert resp.status_code == 400
