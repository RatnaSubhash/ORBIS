"""
Test Suite: ORBIS Continuity User Workspace vs. Control Plane Boundary
Validates that ORBIS assistant control-plane interactions (such as focusing the
ORBIS Electron UI shell to ask a question) do not contaminate or overwrite the
retrieved user workspace continuity context.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from core.continuity.extractor import (
    WorkspaceResourceExtractor,
    is_orbis_control_plane,
    is_protected_resource,
)
from core.continuity.models import ContinuityQuery, ContinuityQueryType
from core.continuity.retriever import ContinuityRetriever
from core.continuity.service import ContinuityService
from core.continuity.sessionizer import SessionSegmenter
from core.memory.db import DatabaseManager
from core.tools.continuity_tool import ContinuityTool, ContinuityToolParameters
from core.types import ExecutionContext, Session, WorkspaceEvent, WorkspaceEventType


@pytest.fixture
def test_db(tmp_path: Path) -> DatabaseManager:
    db_file = tmp_path / "test_continuity.db"
    db = DatabaseManager(str(db_file))
    db.initialize()
    return db


class TestOrbisControlPlaneIdentification:
    """Validates robust identification of ORBIS's own control-plane activity."""

    def test_orbis_electron_shell_identified(self):
        assert is_orbis_control_plane("electron.exe", "ORBIS Agent Shell") is True
        assert is_orbis_control_plane("electron.exe", "orbis-frontend") is True
        assert is_orbis_control_plane("electron", "ORBIS Agent Shell") is True
        assert is_orbis_control_plane("orbis.exe", "ORBIS Agent Shell") is True
        assert is_orbis_control_plane("electron.exe", "ORBIS") is True

    def test_orbis_executable_path_identified(self):
        exe_path = r"C:\Projects\ORBIS\frontend\node_modules\electron\dist\electron.exe"
        assert is_orbis_control_plane("electron.exe", "Any Title", exe_path) is True

    def test_unrelated_electron_app_not_classified_as_orbis(self):
        """Invariant: Unrelated Electron applications must NOT be classified as ORBIS."""
        exe_path = r"C:\Users\user\AppData\Local\Programs\Slack\slack.exe"
        assert is_orbis_control_plane("slack.exe", "Slack - Project Sync", exe_path) is False

        custom_electron = r"C:\Apps\FintechApp\electron.exe"
        assert is_orbis_control_plane("electron.exe", "Fintech Portfolio Dashboard", custom_electron) is False

    def test_ide_working_on_orbis_not_classified_as_control_plane(self):
        """Invariant: User coding ORBIS in an IDE is user workspace, NOT control plane."""
        assert is_orbis_control_plane("Code.exe", "demo_phase5.py - ORBIS - Visual Studio Code") is False
        assert is_orbis_control_plane("Antigravity IDE.exe", "ORBIS - Antigravity IDE") is False
        assert is_orbis_control_plane("chrome.exe", "ORBIS Project Explanation - Google Chrome") is False

    def test_resource_extractor_ignores_orbis_control_plane(self):
        """ORBIS control-plane activity never produces project or file resources."""
        evidence = WorkspaceResourceExtractor.extract("electron.exe", "ORBIS Agent Shell")
        assert evidence.name is None
        assert evidence.project is None
        assert evidence.resource_type == "unknown"


class TestContinuityCutoffAndExclusion:
    """Validates temporal cutoff, control-plane exclusion, and user workspace retrieval."""

    def test_user_workspace_followed_by_orbis_interaction(self, test_db: DatabaseManager):
        """
        User works in VS Code on demo.py in project ORBIS.
        User then opens ORBIS Electron window to ask a question.
        Continuity retrieval must return the VS Code workspace, NOT electron.exe.
        """
        now = datetime.now(timezone.utc)
        t0 = now - timedelta(minutes=10)
        t1 = now - timedelta(minutes=5)
        t_orbis = now - timedelta(seconds=30)

        # 1. User workspace events (VS Code)
        e1 = WorkspaceEvent(
            timestamp=t0,
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="Code.exe",
            window_title="demo.py - ORBIS - Visual Studio Code",
        )
        e2 = WorkspaceEvent(
            timestamp=t1,
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="Code.exe",
            window_title="demo.py - ORBIS - Visual Studio Code",
        )
        test_db.record_workspace_event(e1)
        test_db.record_workspace_event(e2)

        # 2. Segment into a session
        segmenter = SessionSegmenter()
        s_user = segmenter.create_session([e1, e2])
        test_db.record_session(s_user)

        # 3. ORBIS interaction event (user focuses ORBIS window)
        e_orbis = WorkspaceEvent(
            timestamp=t_orbis,
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="electron.exe",
            window_title="ORBIS Agent Shell",
            metadata={"executable_path": r"C:\Projects\ORBIS\frontend\dist\electron.exe"},
        )
        test_db.record_workspace_event(e_orbis)

        service = ContinuityService(db=test_db)
        response = service.execute_query("What was I working on?")

        assert response.has_sufficient_context is True
        assert "Code.exe" in response.active_applications
        assert "electron.exe" not in response.active_applications
        assert "demo.py" in response.primary_resources
        assert "ORBIS" in response.grounded_summary

    def test_consecutive_continuity_queries_do_not_destroy_context(self, test_db: DatabaseManager):
        """
        Multiple consecutive queries must all retrieve the same pre-query workspace context.
        """
        now = datetime.now(timezone.utc)
        t0 = now - timedelta(minutes=15)
        t1 = now - timedelta(minutes=10)

        e1 = WorkspaceEvent(
            timestamp=t0,
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="Code.exe",
            window_title="test_tavily_search.py - ORBIS - Visual Studio Code",
        )
        e2 = WorkspaceEvent(
            timestamp=t1,
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="Code.exe",
            window_title="demo_phase5.py - ORBIS - Visual Studio Code",
        )
        test_db.record_workspace_event(e1)
        test_db.record_workspace_event(e2)

        segmenter = SessionSegmenter()
        s_user = segmenter.create_session([e1, e2])
        test_db.record_session(s_user)

        # 4 consecutive ORBIS interaction events
        queries = [
            ("What project was I working on?", "ORBIS"),
            ("Which files was I working with?", "demo_phase5.py"),
            ("What was I doing earlier?", "Code.exe"),
            ("What apps was I using?", "Code.exe"),
        ]

        service = ContinuityService(db=test_db)

        for i, (q_text, expected_token) in enumerate(queries):
            # Record an electron event simulating user typing / speaking in ORBIS
            t_ev = now - timedelta(seconds=40 - i * 10)
            e_orbis = WorkspaceEvent(
                timestamp=t_ev,
                event_type=WorkspaceEventType.WINDOW_ACTIVATED,
                process_name="electron.exe",
                window_title="ORBIS Agent Shell",
            )
            test_db.record_workspace_event(e_orbis)

            res = service.execute_query(q_text)
            assert res.has_sufficient_context is True
            assert "electron.exe" not in res.active_applications
            assert expected_token in (res.grounded_summary + " ".join(res.primary_resources) + " ".join(res.active_applications))

    def test_new_user_work_after_orbis_interaction_becomes_new_context(self, test_db: DatabaseManager):
        """
        Context does NOT permanently freeze:
        User works on Project A -> asks ORBIS -> works on Project B -> asks ORBIS again.
        The second query must retrieve Project B!
        """
        now = datetime.now(timezone.utc)
        t_a1 = now - timedelta(hours=2)
        t_a2 = now - timedelta(hours=1, minutes=50)

        # 1. Project A
        e_a1 = WorkspaceEvent(
            timestamp=t_a1,
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="Code.exe",
            window_title="alpha.py - ProjectAlpha - Visual Studio Code",
        )
        e_a2 = WorkspaceEvent(
            timestamp=t_a2,
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="Code.exe",
            window_title="alpha.py - ProjectAlpha - Visual Studio Code",
        )
        test_db.record_workspace_event(e_a1)
        test_db.record_workspace_event(e_a2)

        segmenter = SessionSegmenter()
        s_a = segmenter.create_session([e_a1, e_a2])
        test_db.record_session(s_a)

        # First ORBIS query
        t_orbis1 = now - timedelta(hours=1, minutes=40)
        e_o1 = WorkspaceEvent(
            timestamp=t_orbis1,
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="electron.exe",
            window_title="ORBIS Agent Shell",
        )
        test_db.record_workspace_event(e_o1)

        service = ContinuityService(db=test_db)
        res1 = service.execute_query("What project was I working on?")
        assert "ProjectAlpha" in res1.grounded_summary

        # 2. User works on Project B
        t_b1 = now - timedelta(minutes=30)
        t_b2 = now - timedelta(minutes=20)
        e_b1 = WorkspaceEvent(
            timestamp=t_b1,
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="Code.exe",
            window_title="server.go - ProjectBeta - Visual Studio Code",
        )
        e_b2 = WorkspaceEvent(
            timestamp=t_b2,
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="Code.exe",
            window_title="server.go - ProjectBeta - Visual Studio Code",
        )
        test_db.record_workspace_event(e_b1)
        test_db.record_workspace_event(e_b2)

        s_b = segmenter.create_session([e_b1, e_b2])
        test_db.record_session(s_b)

        # Second ORBIS query
        t_orbis2 = now - timedelta(seconds=10)
        e_o2 = WorkspaceEvent(
            timestamp=t_orbis2,
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="electron.exe",
            window_title="ORBIS Agent Shell",
        )
        test_db.record_workspace_event(e_o2)

        res2 = service.execute_query("What project was I working on?")
        assert "ProjectBeta" in res2.grounded_summary
        assert "server.go" in res2.primary_resources

    def test_unrelated_electron_app_is_retrieved(self, test_db: DatabaseManager):
        """
        If the user was legitimately working in a non-ORBIS Electron application
        (e.g. Fintech Dashboard), it must be retrieved and not excluded.
        """
        now = datetime.now(timezone.utc)
        t0 = now - timedelta(minutes=10)
        t1 = now - timedelta(minutes=5)

        e1 = WorkspaceEvent(
            timestamp=t0,
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="electron.exe",
            window_title="Fintech Portfolio Dashboard",
            metadata={"executable_path": r"C:\Apps\Fintech\electron.exe"},
        )
        e2 = WorkspaceEvent(
            timestamp=t1,
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="electron.exe",
            window_title="Fintech Portfolio Dashboard",
            metadata={"executable_path": r"C:\Apps\Fintech\electron.exe"},
        )
        test_db.record_workspace_event(e1)
        test_db.record_workspace_event(e2)

        segmenter = SessionSegmenter()
        s_fintech = segmenter.create_session([e1, e2])
        test_db.record_session(s_fintech)

        # User opens ORBIS
        e_orbis = WorkspaceEvent(
            timestamp=now - timedelta(seconds=5),
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="electron.exe",
            window_title="ORBIS Agent Shell",
        )
        test_db.record_workspace_event(e_orbis)

        service = ContinuityService(db=test_db)
        res = service.execute_query("What apps was I using?")
        assert "electron.exe" in res.active_applications

    def test_continuity_tool_with_execution_context_timestamp(self, test_db: DatabaseManager):
        """
        Validates that ContinuityTool reads request_timestamp from ExecutionContext
        and enforces pre-query cutoff.
        """
        now = datetime.now(timezone.utc)
        t_user = now - timedelta(minutes=10)
        t_req = now - timedelta(seconds=15)
        t_orbis = now - timedelta(seconds=10)

        e_user = WorkspaceEvent(
            timestamp=t_user,
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="Code.exe",
            window_title="main.py - MyService - Visual Studio Code",
        )
        test_db.record_workspace_event(e_user)

        segmenter = SessionSegmenter()
        s_user = segmenter.create_session([e_user])
        test_db.record_session(s_user)

        # Contaminating event after request began
        e_orbis = WorkspaceEvent(
            timestamp=t_orbis,
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="electron.exe",
            window_title="ORBIS Agent Shell",
        )
        test_db.record_workspace_event(e_orbis)

        tool = ContinuityTool(db=test_db)
        ctx = ExecutionContext(
            task_id="task_test_123",
            metadata={"request_timestamp": t_req.isoformat()},
        )
        params = ContinuityToolParameters(query="What was I working on?")

        import asyncio
        result = asyncio.run(tool.execute(params, ctx))
        assert result.success is True
        assert "MyService" in result.output["grounded_summary"]
        assert "Code.exe" in result.output["active_applications"]
        assert "electron.exe" not in result.output["active_applications"]

    def test_protected_resources_remain_redacted(self, test_db: DatabaseManager):
        """Sensitive files are never exposed in continuity responses."""
        now = datetime.now(timezone.utc)
        e_secret = WorkspaceEvent(
            timestamp=now - timedelta(minutes=5),
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="Code.exe",
            window_title=".env.production - SecretProject - Visual Studio Code",
        )
        test_db.record_workspace_event(e_secret)
        segmenter = SessionSegmenter()
        s_secret = segmenter.create_session([e_secret])
        test_db.record_session(s_secret)

        service = ContinuityService(db=test_db)
        res = service.execute_query("Which files was I working with?")
        for r in res.primary_resources:
            assert not is_protected_resource(r)
        assert ".env.production" not in res.grounded_summary
