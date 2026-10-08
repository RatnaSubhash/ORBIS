"""
Comprehensive test suite for Workspace Resource Extraction, Protected Resource Safety,
Dimension-based Query Classification, Context DNA Enrichment, and Grounded Continuity Responses.

Enforces:
- No temporary databases in production orbis.db.
- Strict protection and exclusion of sensitive files (.env, credentials, SSH keys).
- Generic structure-based extraction across multiple tools (VS Code, JetBrains, Notepad, Explorer, Browsers).
- Dimension-based continuity queries (APP, PROJECT, FILE, GENERAL).
- Historical session backfill capability.
"""

from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from core.memory.db import DatabaseManager
from core.types import WorkspaceEvent, WorkspaceEventType
from core.continuity.models import (
    ContinuityQuery,
    ContinuityQueryType,
    ProvenanceSource,
    RetrievedContextEvidence,
)
from core.continuity.extractor import (
    WorkspaceResourceExtractor,
    ExtractedResourceEvidence,
    is_protected_resource,
)
from core.continuity.sessionizer import SessionSegmenter
from core.continuity.dna import ContextDNASynthesizer
from core.continuity.retriever import ContinuityRetriever
from core.continuity.grounding import GroundedResponseBuilder
from core.continuity.service import ContinuityService


# ==============================================================================
# 1. Protected Resource Safety & Invariants
# ==============================================================================

class TestProtectedResourceSafety:
    """Verifies that sensitive/protected resources are reliably identified and excluded."""

    @pytest.mark.parametrize(
        "resource_name",
        [
            ".env",
            ".env.local",
            ".env.production",
            ".env.staging",
            "id_rsa",
            "id_ed25519",
            "server.key",
            "client.pem",
            "private_key.txt",
            "my_private_key",
            "credentials.json",
            "aws_credentials",
            "api_token.secret",
            "user_passwords.csv",
            "C:\\Windows\\System32\\config\\sam",
        ],
    )
    def test_protected_resources_detected(self, resource_name: str) -> None:
        assert is_protected_resource(resource_name) is True

    @pytest.mark.parametrize(
        "resource_name",
        [
            "README.md",
            "main.py",
            "app.tsx",
            "schema.sql",
            "pyproject.toml",
            "package.json",
            "Dockerfile",
            "Makefile",
            "demo_phase9.py",
            "environment_settings.py",  # Not .env
            "token_parser.py",         # Not secret token
        ],
    )
    def test_safe_resources_not_flagged(self, resource_name: str) -> None:
        assert is_protected_resource(resource_name) is False


# ==============================================================================
# 2. Structural Window Title Extraction (Multi-Tool)
# ==============================================================================

class TestWorkspaceResourceExtractor:
    """Verifies generic, structure-based extraction across developer tools."""

    def test_vscode_three_part_standard(self) -> None:
        ev = WorkspaceResourceExtractor.extract(
            "Code.exe", "demo_phase2.py - ORBIS - Visual Studio Code"
        )
        assert ev.resource_type == "file"
        assert ev.name == "demo_phase2.py"
        assert ev.project == "ORBIS"
        assert ev.is_protected is False

    def test_vscode_dirty_buffer_bullet_stripped(self) -> None:
        ev = WorkspaceResourceExtractor.extract(
            "Code.exe", "● index.tsx - Smart Window - Visual Studio Code"
        )
        assert ev.resource_type == "file"
        assert ev.name == "index.tsx"
        assert ev.project == "Smart Window"
        assert ev.is_protected is False

    def test_vscode_protected_file_flagged(self) -> None:
        ev = WorkspaceResourceExtractor.extract(
            "Code.exe", ".env.local - FintechService - Visual Studio Code"
        )
        assert ev.resource_type == "file"
        assert ev.name == ".env.local"
        assert ev.project == "FintechService"
        assert ev.is_protected is True

    def test_vscode_internal_pages_not_treated_as_files(self) -> None:
        ev1 = WorkspaceResourceExtractor.extract(
            "Code.exe", "Release Notes: 1.138.0 - ORBIS - Visual Studio Code"
        )
        assert ev1.name is None
        assert ev1.project == "ORBIS"

        ev2 = WorkspaceResourceExtractor.extract(
            "Code.exe", "Welcome - Visual Studio Code"
        )
        assert ev2.name is None
        assert ev2.project is None

        ev3 = WorkspaceResourceExtractor.extract(
            "Code.exe", "Settings - Visual Studio Code"
        )
        assert ev3.name is None
        assert ev3.project is None

    def test_vscode_workspace_only_no_file(self) -> None:
        ev = WorkspaceResourceExtractor.extract(
            "Code.exe", "ORBIS - Visual Studio Code"
        )
        assert ev.name is None
        assert ev.project == "ORBIS"
        assert ev.resource_type == "project"

    def test_antigravity_ide_structural_title(self) -> None:
        ev = WorkspaceResourceExtractor.extract(
            "Antigravity IDE.exe", "ORBIS - Antigravity IDE - Implementation Plan"
        )
        assert ev.project == "ORBIS"
        assert ev.name is None
        assert ev.is_protected is False

    def test_jetbrains_title_extraction(self) -> None:
        ev = WorkspaceResourceExtractor.extract(
            "pycharm64.exe", "BackendService – main.py"
        )
        assert ev.resource_type == "file"
        assert ev.name == "main.py"
        assert ev.project == "BackendService"

    def test_notepad_title_extraction(self) -> None:
        ev = WorkspaceResourceExtractor.extract(
            "notepad.exe", "*notes.txt - Notepad"
        )
        assert ev.resource_type == "file"
        assert ev.name == "notes.txt"
        assert ev.project is None

    def test_browser_never_fabricates_urls_or_files(self) -> None:
        ev1 = WorkspaceResourceExtractor.extract(
            "chrome.exe", "ORBIS Project Explanation - Google Chrome"
        )
        assert ev1.name is None
        assert ev1.project is None
        assert ev1.resource_type == "unknown"

        ev2 = WorkspaceResourceExtractor.extract(
            "msedge.exe", "https://github.com/microsoft - Microsoft Edge"
        )
        # Even if URL-like string is in title, we do not claim verified URL
        assert ev2.name is None
        assert ev2.project is None

    def test_explorer_task_switching_ignored(self) -> None:
        ev = WorkspaceResourceExtractor.extract("explorer.exe", "Task Switching")
        assert ev.name is None
        assert ev.project is None
        assert ev.resource_type == "unknown"

    def test_no_inference_from_process_name_alone(self) -> None:
        ev = WorkspaceResourceExtractor.extract("Code.exe", "")
        assert ev.name is None
        assert ev.project is None
        assert ev.resource_type == "unknown"


# ==============================================================================
# 3. Sessionizer Safe Resource & Project Aggregation
# ==============================================================================

class TestSessionizerExtraction:
    """Verifies SessionSegmenter accumulates safe resources and isolates protected ones."""

    def test_session_accumulates_safe_files_and_projects(self) -> None:
        base_time = datetime(2026, 9, 19, 10, 0, 0, tzinfo=timezone.utc)
        events = [
            WorkspaceEvent(
                event_id="evt_01",
                timestamp=base_time,
                event_type=WorkspaceEventType.WINDOW_ACTIVATED,
                process_name="Code.exe",
                window_title="main.py - AlphaProject - Visual Studio Code",
                resource_path=None,
            ),
            WorkspaceEvent(
                event_id="evt_02",
                timestamp=base_time + timedelta(seconds=10),
                event_type=WorkspaceEventType.WINDOW_ACTIVATED,
                process_name="Code.exe",
                window_title="● utils.py - AlphaProject - Visual Studio Code",
                resource_path=None,
            ),
            WorkspaceEvent(
                event_id="evt_03",
                timestamp=base_time + timedelta(seconds=20),
                event_type=WorkspaceEventType.WINDOW_ACTIVATED,
                process_name="Code.exe",
                window_title=".env - AlphaProject - Visual Studio Code",
                resource_path=None,
            ),
        ]

        segmenter = SessionSegmenter()
        sessions = segmenter.segment(events)
        assert len(sessions) == 1
        sess = sessions[0]

        # Safe files included and deduplicated
        assert "main.py" in sess.resources
        assert "utils.py" in sess.resources

        # Protected .env MUST NOT enter sess.resources
        assert ".env" not in sess.resources
        assert not any(".env" in r for r in sess.resources)

        # Projects recorded in metadata
        assert "AlphaProject" in sess.session_metadata["projects"]
        assert sess.session_metadata["redacted_resources_count"] >= 1


# ==============================================================================
# 4. Context DNA Synthesis
# ==============================================================================

class TestContextDNAEnrichment:
    """Verifies ContextDNA captures projects and excludes sensitive resources."""

    def test_dna_captures_project_and_safe_files(self) -> None:
        base_time = datetime(2026, 9, 19, 11, 0, 0, tzinfo=timezone.utc)
        events = [
            WorkspaceEvent(
                event_id="evt_10",
                timestamp=base_time,
                event_type=WorkspaceEventType.WINDOW_ACTIVATED,
                process_name="Code.exe",
                window_title="server.py - CloudGateway - Visual Studio Code",
            ),
            WorkspaceEvent(
                event_id="evt_11",
                timestamp=base_time + timedelta(seconds=15),
                event_type=WorkspaceEventType.WINDOW_ACTIVATED,
                process_name="Code.exe",
                window_title="id_rsa - CloudGateway - Visual Studio Code",
            ),
        ]

        segmenter = SessionSegmenter()
        session = segmenter.create_session(events)

        synthesizer = ContextDNASynthesizer()
        dna = synthesizer.synthesize(session, events)

        # Resource context
        res_ctx = dna.resource_context
        assert "server.py" in res_ctx["observed_resource_paths"]
        assert "id_rsa" not in res_ctx["observed_resource_paths"]
        assert "CloudGateway" in res_ctx["projects"]

        # Privacy context
        priv_ctx = dna.privacy_context
        assert priv_ctx["redaction_count"] >= 1


# ==============================================================================
# 5. Dimension-based Query Classification & Paraphrase Coverage
# ==============================================================================

class TestDimensionBasedQueryClassification:
    """Verifies that query classification evaluates dimensions rather than memorizing phrases."""

    @pytest.mark.parametrize(
        "query",
        [
            "What apps was I using?",
            "which applications did i open",
            "What software did I run earlier?",
            "Which tools was I on?",
            "Tell me what apps were open",
            "What programs did I execute?",
        ],
    )
    def test_app_query_paraphrases(self, query: str) -> None:
        structured = ContinuityService.classify_query(query)
        assert structured.query_type == ContinuityQueryType.APP_QUERY

    @pytest.mark.parametrize(
        "query",
        [
            "Which project was I working on?",
            "What project was I on?",
            "What repository was I in?",
            "Tell me what project I had open",
            "Which codebase was I editing?",
            "What workspace was active?",
            "which repo did i work on",
        ],
    )
    def test_project_query_paraphrases(self, query: str) -> None:
        structured = ContinuityService.classify_query(query)
        assert structured.query_type == ContinuityQueryType.PROJECT_QUERY

    @pytest.mark.parametrize(
        "query",
        [
            "Which files was I working with?",
            "What files did I touch?",
            "What files were open?",
            "Show me the files I worked on",
            "Which document was I editing?",
            "What scripts did I open?",
            "what filename was active",
        ],
    )
    def test_file_query_paraphrases(self, query: str) -> None:
        structured = ContinuityService.classify_query(query)
        assert structured.query_type == ContinuityQueryType.FILE_QUERY

    @pytest.mark.parametrize(
        "query",
        [
            "What was I doing earlier?",
            "What was I working on?",
            "Where did I leave off?",
            "What am I working on?",
            "Continue where I left off",
        ],
    )
    def test_general_continuity_paraphrases(self, query: str) -> None:
        structured = ContinuityService.classify_query(query)
        assert structured.query_type == ContinuityQueryType.WHAT_WAS_I_WORKING_ON

    def test_last_session_and_recent_context_take_precedence(self) -> None:
        q1 = ContinuityService.classify_query("Show recent workspace context")
        assert q1.query_type == ContinuityQueryType.RECENT_CONTEXT

        q2 = ContinuityService.classify_query("What was my last session about?")
        assert q2.query_type == ContinuityQueryType.LAST_SESSION


# ==============================================================================
# 6. Grounded Responses (Honesty, Formatting, and Privacy Defense)
# ==============================================================================

class TestGroundedResponses:
    """Verifies natural language answers are grounded, dimension-specific, and honest."""

    @pytest.fixture
    def evidence_with_all(self) -> RetrievedContextEvidence:
        return RetrievedContextEvidence(
            session_id="sess_full",
            start_time=datetime(2026, 9, 19, 14, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 9, 19, 14, 15, 0, tzinfo=timezone.utc),
            duration_seconds=900.0,
            applications=["Code.exe", "chrome.exe"],
            primary_application="Code.exe",
            resources=["demo.py", "README.md"],
            projects=["ORBIS"],
            event_count=15,
            provenance=ProvenanceSource(session_ids=["sess_full"]),
        )

    @pytest.fixture
    def evidence_without_files_or_project(self) -> RetrievedContextEvidence:
        return RetrievedContextEvidence(
            session_id="sess_empty",
            start_time=datetime(2026, 9, 19, 15, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 9, 19, 15, 5, 0, tzinfo=timezone.utc),
            duration_seconds=300.0,
            applications=["Spotify.exe"],
            primary_application="Spotify.exe",
            resources=[],
            projects=[],
            event_count=5,
            provenance=ProvenanceSource(session_ids=["sess_empty"]),
        )

    def test_project_query_with_evidence(self, evidence_with_all: RetrievedContextEvidence) -> None:
        builder = GroundedResponseBuilder()
        res = builder.build_response(
            "Which project was I working on?",
            [evidence_with_all],
            ContinuityQueryType.PROJECT_QUERY,
        )
        assert "ORBIS" in res.grounded_summary
        assert "You were working on the ORBIS project" in res.grounded_summary
        assert res.projects == ["ORBIS"]

    def test_project_query_without_evidence_is_honest(
        self, evidence_without_files_or_project: RetrievedContextEvidence
    ) -> None:
        builder = GroundedResponseBuilder()
        res = builder.build_response(
            "Which project was I working on?",
            [evidence_without_files_or_project],
            ContinuityQueryType.PROJECT_QUERY,
        )
        assert "No specific project name was captured" in res.grounded_summary

    def test_file_query_with_evidence(self, evidence_with_all: RetrievedContextEvidence) -> None:
        builder = GroundedResponseBuilder()
        res = builder.build_response(
            "Which files was I working with?",
            [evidence_with_all],
            ContinuityQueryType.FILE_QUERY,
        )
        assert "demo.py" in res.grounded_summary
        assert "README.md" in res.grounded_summary
        assert "You were working with the following files" in res.grounded_summary

    def test_file_query_without_evidence_is_honest(
        self, evidence_without_files_or_project: RetrievedContextEvidence
    ) -> None:
        builder = GroundedResponseBuilder()
        res = builder.build_response(
            "Which files was I working with?",
            [evidence_without_files_or_project],
            ContinuityQueryType.FILE_QUERY,
        )
        assert "No specific files were captured" in res.grounded_summary

    def test_app_query(self, evidence_with_all: RetrievedContextEvidence) -> None:
        builder = GroundedResponseBuilder()
        res = builder.build_response(
            "What apps was I using?",
            [evidence_with_all],
            ContinuityQueryType.APP_QUERY,
        )
        assert "You were using Code.exe" in res.grounded_summary
        assert "chrome.exe" in res.grounded_summary

    def test_response_builder_defense_in_depth_filters_leaked_env(self) -> None:
        # Simulate a corrupted evidence item that somehow contains a protected resource
        leaked_evidence = RetrievedContextEvidence(
            session_id="sess_leak",
            start_time=datetime(2026, 9, 19, 16, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 9, 19, 16, 10, 0, tzinfo=timezone.utc),
            duration_seconds=600.0,
            applications=["Code.exe"],
            resources=[".env", ".env.production", "safe_file.py"],
            projects=["Fintech"],
            event_count=5,
        )
        builder = GroundedResponseBuilder()
        res = builder.build_response(
            "Which files was I working with?",
            [leaked_evidence],
            ContinuityQueryType.FILE_QUERY,
        )
        assert ".env" not in res.grounded_summary
        assert ".env.production" not in res.grounded_summary
        assert "safe_file.py" in res.grounded_summary
        assert ".env" not in res.primary_resources


# ==============================================================================
# 7. End-to-End Historical Session Backfill Test (with temp DB)
# ==============================================================================

class TestHistoricalSessionBackfill:
    """Verifies that historical sessions in a database without pre-parsed resources are backfilled."""

    def test_backfill_historical_database_events(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test_continuity.db")
        db = DatabaseManager(db_path)
        db.initialize()

        base_time = datetime(2026, 9, 19, 18, 0, 0, tzinfo=timezone.utc)
        events = [
            WorkspaceEvent(
                event_id="evt_h1",
                timestamp=base_time,
                event_type=WorkspaceEventType.WINDOW_ACTIVATED,
                process_name="Code.exe",
                window_title="● demo_phase9.py - ORBIS - Visual Studio Code",
                resource_path=None,
            ),
            WorkspaceEvent(
                event_id="evt_h2",
                timestamp=base_time + timedelta(seconds=20),
                event_type=WorkspaceEventType.WINDOW_ACTIVATED,
                process_name="Code.exe",
                window_title="demo.py - ORBIS - Visual Studio Code",
                resource_path=None,
            ),
            WorkspaceEvent(
                event_id="evt_h3",
                timestamp=base_time + timedelta(seconds=40),
                event_type=WorkspaceEventType.WINDOW_ACTIVATED,
                process_name="Code.exe",
                window_title=".env - ORBIS - Visual Studio Code",
                resource_path=None,
            ),
            WorkspaceEvent(
                event_id="evt_h4",
                timestamp=base_time + timedelta(seconds=60),
                event_type=WorkspaceEventType.WINDOW_ACTIVATED,
                process_name="chrome.exe",
                window_title="ORBIS Project Architecture - Google Chrome",
                resource_path=None,
            ),
        ]

        # Record events into SQLite
        for e in events:
            db.record_workspace_event(e)

        # Create a historical session that has EMPTY resources (simulating older recorded sessions)
        segmenter = SessionSegmenter()
        session = segmenter.create_session(events)
        # Clear resources to test the backfill mechanism explicitly
        session.resources = []
        db.record_session(session)

        # Synthesize and store DNA (with ordered event sequence)
        synthesizer = ContextDNASynthesizer()
        dna = synthesizer.synthesize(session, events)
        dna.resource_context["observed_resource_paths"] = []  # Clear to test backfill
        db.record_context_dna(dna)

        # Execute queries via ContinuityService
        svc = ContinuityService(db=db)

        # 1. Project query
        res_proj = svc.execute_query("Which project was I working on?")
        assert "ORBIS" in res_proj.grounded_summary
        assert "ORBIS" in res_proj.projects

        # 2. File query
        res_file = svc.execute_query("Which files was I working with?")
        assert "demo_phase9.py" in res_file.grounded_summary
        assert "demo.py" in res_file.grounded_summary
        assert ".env" not in res_file.grounded_summary
        assert ".env" not in res_file.primary_resources

        # 3. App query
        res_app = svc.execute_query("What apps was I using?")
        assert "Code.exe" in res_app.grounded_summary
        assert "chrome.exe" in res_app.grounded_summary

        # 4. What was I working on
        res_work = svc.execute_query("What was I working on?")
        assert "ORBIS" in res_work.grounded_summary
        assert "demo_phase9.py" in res_work.grounded_summary
        assert ".env" not in res_work.grounded_summary
