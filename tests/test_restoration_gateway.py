"""
Tests for ORBIS Workspace Restoration Gateway REST API endpoints.
Tests:
- GET /api/restoration/sessions
- POST /api/restoration/preview
- POST /api/restoration/execute
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi.testclient import TestClient

from core.gateway.app import create_app
from core.gateway import routes
from core.memory.db import DatabaseManager
from core.restoration.engine import RestorationEngine
from core.restoration.models import (
    RestorationPlan,
    RestorationResult,
    RestorationStep,
    RestorationStepStatus,
    ResourceTarget,
    ResourceTargetType,
)
from core.types import RiskLevel, Session, WorkspaceEvent, WorkspaceEventType


@pytest.fixture
def mock_db():
    db = DatabaseManager(db_path=":memory:")
    db.initialize()
    now = datetime.now(timezone.utc)
    # Record a test session with real apps
    session = Session(
        session_id="test_session_restoration_001",
        start_time=now - timedelta(minutes=30),
        end_time=now - timedelta(minutes=5),
        duration_seconds=1500.0,
        event_count=5,
        applications=["Code.exe", "chrome.exe", "SearchHost.exe", "explorer.exe"],
        resources=["c:\\Projects\\ORBIS", "https://github.com/orbis"],
        event_ids=["e1", "e2"],
        session_metadata={"projects": ["ORBIS"]},
        confidence=0.95,
    )
    db.record_session(session)
    return db


@pytest.fixture
def restoration_client(mock_db):
    app = create_app(db_manager=mock_db)
    
    # Mock orchestrator and restoration engine
    mock_orchestrator = MagicMock()
    mock_engine = RestorationEngine(
        db=mock_db,
        process_launcher=lambda exe, args: (True, 9999, None),
    )
    mock_orchestrator.restoration_engine = mock_engine

    app.dependency_overrides[routes.get_orchestrator] = lambda: mock_orchestrator
    app.dependency_overrides[routes.get_db_manager] = lambda: mock_db
    app.dependency_overrides[routes.get_restoration_engine] = lambda: mock_engine

    with TestClient(app) as client:
        yield client


def test_get_restoration_sessions(restoration_client):
    """GET /api/restoration/sessions returns candidate sessions with filtered apps."""
    res = restoration_client.get("/api/restoration/sessions")
    assert res.status_code == 200
    data = res.json()
    assert "sessions" in data
    assert data["total"] >= 1
    session = data["sessions"][0]
    assert session["session_id"] == "test_session_restoration_001"
    assert session["project_name"] == "ORBIS"
    app_names = [a["process_name"] for a in session["applications"]]
    # SearchHost.exe must be filtered out
    assert "SearchHost.exe" not in app_names
    assert "Code.exe" in app_names
    assert "chrome.exe" in app_names


def test_preview_restoration_plan(restoration_client):
    """POST /api/restoration/preview constructs and preflights plan without launching."""
    payload = {
        "session_id": "test_session_restoration_001",
    }
    res = restoration_client.post("/api/restoration/preview", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["plan"] is not None
    plan = data["plan"]
    assert plan["session_id"] == "test_session_restoration_001"
    assert len(plan["steps"]) >= 2
    app_ids = [s["app_identifier"] for s in plan["steps"]]
    assert "vscode" in app_ids or "chrome" in app_ids


def test_execute_restoration_plan(restoration_client):
    """POST /api/restoration/execute runs validated plan with mock process launcher."""
    payload = {
        "session_id": "test_session_restoration_001",
        "approved": True,
    }
    res = restoration_client.post("/api/restoration/execute", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["session_id"] == "test_session_restoration_001"
    assert data["plan_id"] is not None
    assert "summary" in data
    assert len(data["completed_steps"]) + len(data["skipped_steps"]) > 0
