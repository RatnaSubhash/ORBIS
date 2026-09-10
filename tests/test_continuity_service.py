"""
Unit and Integration Tests for ORBIS Phase 5 Continuity Subsystem.
Verifies end-to-end continuity workflow, retrieval from semantic memory and graph,
grounded response generation, provenance preservation, empty/insufficient context handling,
deterministic heuristics, query classification, ContinuityTool, and Orchestrator integration.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import pytest

from core.agent.llm.mock import MockLLMProvider
from core.agent.orchestrator import AgentOrchestrator
from core.continuity.dna import ContextDNASynthesizer
from core.continuity.grounding import GroundedResponseBuilder
from core.continuity.models import (
    ContinuityQuery,
    ContinuityQueryType,
    ContinuityResponse,
    RetrievedContextEvidence,
)
from core.continuity.retriever import ContinuityRetriever
from core.continuity.service import ContinuityService
from core.continuity.sessionizer import SessionSegmenter
from core.memory.db import DatabaseManager
from core.memory.graph.builder import GraphBuilder
from core.memory.graph.graph import KnowledgeGraph
from core.memory.vector.embeddings.mock import MockEmbeddingProvider
from core.memory.vector.memory import SemanticMemory
from core.tools.continuity_tool import ContinuityTool, ContinuityToolParameters
from core.tools.registry import ToolRegistry
from core.types import ExecutionContext, Session, WorkspaceEvent, WorkspaceEventType


def _setup_test_workspace(db: DatabaseManager):
    """Populates database with a realistic multi-event session."""
    t0 = datetime(2026, 9, 10, 10, 0, 0, tzinfo=timezone.utc)
    events = [
        WorkspaceEvent(
            event_id="evt_01",
            timestamp=t0,
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="Code.exe",
            window_title="main.py - ORBIS",
            resource_path="c:\\Projects\\ORBIS\\main.py",
        ),
        WorkspaceEvent(
            event_id="evt_02",
            timestamp=t0 + timedelta(minutes=2),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="chrome.exe",
            window_title="FastAPI Documentation - Chrome",
            resource_path="https://fastapi.tiangolo.com",
        ),
        WorkspaceEvent(
            event_id="evt_03",
            timestamp=t0 + timedelta(minutes=4),
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="Code.exe",
            window_title="models.py - ORBIS",
            resource_path="c:\\Projects\\ORBIS\\models.py",
        ),
    ]
    for e in events:
        db.record_workspace_event(e)

    segmenter = SessionSegmenter(session_gap_seconds=900.0)
    sessions = segmenter.segment(events)
    assert len(sessions) == 1
    session = sessions[0]
    db.record_session(session)

    synthesizer = ContextDNASynthesizer()
    dna = synthesizer.synthesize(session, events)
    db.record_context_dna(dna)

    builder = GraphBuilder()
    kg = builder.build_from_session(session, dna, events)
    builder.persist_graph(db, kg)

    provider = MockEmbeddingProvider(dimension=64)
    vec_mem = SemanticMemory(embedding_provider=provider, db=db)
    vec_mem.index_session(session)
    vec_mem.index_context_dna(dna)

    return session, dna, events, kg, vec_mem


def test_end_to_end_continuity_pipeline():
    """Verifies complete end-to-end flow from stored memory to grounded continuity response."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()
    session, dna, events, kg, vec_mem = _setup_test_workspace(db)

    service = ContinuityService(db=db, graph=kg, vector_memory=vec_mem)
    response = service.execute_query("What was I working on?")

    assert isinstance(response, ContinuityResponse)
    assert response.has_sufficient_context is True
    assert "Code.exe" in response.grounded_summary
    assert "chrome.exe" in response.grounded_summary
    assert "main.py" in response.grounded_summary
    assert response.confidence_score > 0.5
    assert response.last_activity_timestamp == session.end_time

    # Verify provenance
    assert session.session_id in response.provenance.session_ids
    assert dna.dna_id in response.provenance.dna_ids
    assert "evt_01" in response.provenance.event_ids
    assert f"session:{session.session_id}" in response.provenance.graph_node_ids
    assert len(response.provenance.vector_record_ids) > 0


def test_semantic_retrieval_prioritization():
    """Verifies that queries with topical keywords prioritize relevant sessions via semantic retrieval."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    t0 = datetime(2026, 9, 10, 8, 0, 0, tzinfo=timezone.utc)

    # Session A: Python Backend
    s_a = Session(
        session_id="sess_python_backend",
        start_time=t0,
        end_time=t0 + timedelta(minutes=10),
        duration_seconds=600.0,
        event_count=3,
        applications=["Code.exe"],
        resources=["c:\\Projects\\backend\\server.py"],
        event_ids=["e1", "e2", "e3"],
    )
    db.record_session(s_a)

    # Session B: Git Terminal (more recent)
    s_b = Session(
        session_id="sess_terminal_git",
        start_time=t0 + timedelta(hours=2),
        end_time=t0 + timedelta(hours=2, minutes=5),
        duration_seconds=300.0,
        event_count=2,
        applications=["WindowsTerminal.exe"],
        resources=["c:\\Projects\\repo"],
        event_ids=["e4", "e5"],
    )
    db.record_session(s_b)

    provider = MockEmbeddingProvider(dimension=64)
    vec_mem = SemanticMemory(embedding_provider=provider, db=db)
    vec_mem.index_session(s_a)
    vec_mem.index_session(s_b)

    retriever = ContinuityRetriever(db=db, vector_memory=vec_mem)

    # Query specifically about python backend code
    query = ContinuityQuery(
        query_text=SemanticMemory.format_session_text(s_a),
        query_type=ContinuityQueryType.GENERAL_CONTINUITY,
        max_results=1,
    )
    evidence = retriever.retrieve(query)

    assert len(evidence) == 1
    assert evidence[0].session_id == "sess_python_backend"
    assert evidence[0].vector_similarity is not None


def test_graph_and_session_context_enrichment():
    """Verifies that topological KnowledgeGraph links enrich applications, resources, and provenance."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()
    session, dna, events, kg, _ = _setup_test_workspace(db)

    retriever = ContinuityRetriever(db=db, graph=kg, vector_memory=None)
    query = ContinuityQuery(query_text="What was I working on?", max_results=1)
    evidence_list = retriever.retrieve(query)

    assert len(evidence_list) == 1
    ev = evidence_list[0]
    assert "Code.exe" in ev.applications
    assert "chrome.exe" in ev.applications
    assert f"session:{session.session_id}" in ev.provenance.graph_node_ids
    assert "app:code.exe" in ev.provenance.graph_node_ids
    assert f"dna:{dna.dna_id}" in ev.provenance.graph_node_ids


def test_provenance_preserves_genuine_sources_only():
    """Verifies that missing memory subsystems do not produce fabricated provenance IDs."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    t0 = datetime(2026, 9, 10, 10, 0, 0, tzinfo=timezone.utc)
    session = Session(
        session_id="sess_isolated",
        start_time=t0,
        end_time=t0 + timedelta(minutes=5),
        duration_seconds=300.0,
        event_count=1,
        applications=["notepad.exe"],
        resources=["c:\\notes.txt"],
        event_ids=["evt_note"],
    )
    db.record_session(session)

    # Do NOT provide KnowledgeGraph or SemanticMemory
    retriever = ContinuityRetriever(db=db, graph=None, vector_memory=None)
    query = ContinuityQuery(query_text="What was I working on?")
    evidence_list = retriever.retrieve(query)

    assert len(evidence_list) == 1
    prov = evidence_list[0].provenance
    assert prov.session_ids == ["sess_isolated"]
    assert prov.event_ids == ["evt_note"]
    assert prov.dna_ids == []  # No Context DNA created
    assert prov.graph_node_ids == []  # No graph provided -> strictly empty
    assert prov.vector_record_ids == []  # No vectors created -> strictly empty


def test_empty_database_handling():
    """Verifies safe failure with explicit explanation when database is empty."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    service = ContinuityService(db=db)
    response = service.execute_query("What was I working on?")

    assert response.has_sufficient_context is False
    assert response.confidence_score == 0.0
    assert "No recorded workspace activity" in response.grounded_summary
    assert response.active_applications == []
    assert response.primary_resources == []
    assert response.evidence == []
    assert response.provenance.session_ids == []


def test_insufficient_evidence_handling():
    """Verifies that empty sessions with no observable activity return insufficient context."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    t0 = datetime(2026, 9, 10, 10, 0, 0, tzinfo=timezone.utc)
    empty_session = Session(
        session_id="sess_empty",
        start_time=t0,
        end_time=t0,
        duration_seconds=0.0,
        event_count=0,
        applications=[],
        resources=[],
        event_ids=[],
    )
    db.record_session(empty_session)

    service = ContinuityService(db=db)
    response = service.execute_query("What was I working on?")

    assert response.has_sufficient_context is False
    assert "contains no observable" in response.grounded_summary
    assert response.confidence_score <= 0.2


def test_deterministic_output():
    """Verifies that identical query on identical data produces identical grounded responses."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()
    _setup_test_workspace(db)

    service = ContinuityService(db=db)
    res1 = service.execute_query("What was I working on?")
    res2 = service.execute_query("What was I working on?")

    assert res1.grounded_summary == res2.grounded_summary
    assert res1.confidence_score == res2.confidence_score
    assert res1.provenance.session_ids == res2.provenance.session_ids
    assert res1.active_applications == res2.active_applications


def test_query_classification_and_invalid_queries():
    """Verifies is_continuity_query, classify_query, and handling of empty queries."""
    assert ContinuityService.is_continuity_query("What was I working on?") is True
    assert ContinuityService.is_continuity_query("What was my last session about?") is True
    assert ContinuityService.is_continuity_query("Show recent workspace context") is True
    assert ContinuityService.is_continuity_query("Where did I leave off?") is True
    assert ContinuityService.is_continuity_query("Search Google for weather") is False
    assert ContinuityService.is_continuity_query("") is False

    q1 = ContinuityService.classify_query("What was my last session about?")
    assert q1.query_type == ContinuityQueryType.LAST_SESSION
    assert q1.max_results == 1

    q2 = ContinuityService.classify_query("Show recent workspace context")
    assert q2.query_type == ContinuityQueryType.RECENT_CONTEXT

    # Empty query execution
    db = DatabaseManager(db_path=":memory:")
    db.initialize()
    service = ContinuityService(db=db)
    res = service.execute_query("")
    assert res.has_sufficient_context is False
    assert "Empty continuity query" in res.grounded_summary


@pytest.mark.asyncio
async def test_continuity_tool_execution_and_verification():
    """Verifies ContinuityTool BaseTool contract, execution, and verification report."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()
    _setup_test_workspace(db)

    tool = ContinuityTool(db=db)
    assert tool.name == "continuity_tool"
    assert tool.risk_level.value == "LOW"

    params = tool.validate_parameters({"query": "What was I working on?"})
    ctx = ExecutionContext()

    result = await tool.execute(params, ctx)
    assert result.success is True
    assert isinstance(result.output, dict)
    assert result.output["has_sufficient_context"] is True

    report = await tool.verify(params, result, ctx)
    assert report.verified is True
    assert "Grounded continuity response generated successfully" in report.details


@pytest.mark.asyncio
async def test_orchestrator_run_continuity_helper():
    """Verifies that AgentOrchestrator.run_continuity provides a clean vertical-slice path."""
    db = DatabaseManager(db_path=":memory:")
    db.initialize()
    _setup_test_workspace(db)

    tool_registry = ToolRegistry()
    tool_registry.register(ContinuityTool(db=db))
    llm = MockLLMProvider()

    service = ContinuityService(db=db)
    orchestrator = AgentOrchestrator(
        llm_provider=llm,
        tool_registry=tool_registry,
        db_manager=db,
        continuity_service=service,
    )

    response = await orchestrator.run_continuity("What was I working on?")
    assert isinstance(response, ContinuityResponse)
    assert response.has_sufficient_context is True
    assert "Code.exe" in response.grounded_summary
