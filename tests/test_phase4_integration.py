"""
Integration tests for ORBIS Phase 4.
Verifies end-to-end flow from WorkspaceEvents and Sessions to KnowledgeGraph,
SemanticMemory, SQLite persistence, and privacy boundary enforcement.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import pytest

from core.continuity.dna import ContextDNASynthesizer
from core.continuity.sessionizer import SessionSegmenter
from core.memory.db import DatabaseManager
from core.memory.graph.builder import GraphBuilder
from core.memory.graph.graph import KnowledgeGraph
from core.memory.vector.embeddings.mock import MockEmbeddingProvider
from core.memory.vector.memory import SemanticMemory
from core.types import WorkspaceEvent, WorkspaceEventType


def test_end_to_end_phase4_memory_pipeline() -> None:
    """
    Verifies full integration flow:
    Raw events -> Sessions -> Context DNA -> Knowledge Graph + Vector Memory -> SQLite.
    """
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    base_t = datetime(2026, 9, 9, 9, 0, 0, tzinfo=timezone.utc)

    # 1. Create realistic workspace events
    events = [
        WorkspaceEvent(
            event_id="e1",
            timestamp=base_t,
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="Code.exe",
            window_title="main.py - ORBIS",
            resource_path="c:\\Projects\\ORBIS\\main.py",
        ),
        WorkspaceEvent(
            event_id="e2",
            timestamp=base_t + timedelta(minutes=2),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="chrome.exe",
            window_title="FastAPI Docs - Chrome",
            resource_path="https://fastapi.tiangolo.com",
        ),
    ]

    for e in events:
        db.record_workspace_event(e)

    # 2. Segment Sessions & Synthesize Context DNA
    segmenter = SessionSegmenter(session_gap_seconds=900.0)
    sessions = segmenter.segment(events)
    assert len(sessions) == 1
    session = sessions[0]
    db.record_session(session)

    synthesizer = ContextDNASynthesizer()
    dna = synthesizer.synthesize(session, events)
    db.record_context_dna(dna)

    # 3. Build and persist Knowledge Graph
    graph_builder = GraphBuilder()
    kg = graph_builder.build_from_session(session, dna, events)
    graph_builder.persist_graph(db, kg)

    assert kg.node_count >= 5
    assert len(kg.get_session_applications(session.session_id)) == 2
    assert len(kg.get_session_resources(session.session_id)) == 2

    # 4. Index in Semantic Vector Memory
    with tempfile.TemporaryDirectory() as tmp_dir:
        faiss_path = str(Path(tmp_dir) / "integration.faiss")
        vec_mem = SemanticMemory(
            embedding_provider=MockEmbeddingProvider(dimension=64),
            db=db,
            index_path=faiss_path,
        )

        rec_session = vec_mem.index_session(session)
        rec_dna = vec_mem.index_context_dna(dna)
        vec_mem.save()

        # 5. Verify Search
        search_results = vec_mem.search(rec_session.text_representation, top_k=2)
        assert len(search_results) == 2
        top_match = search_results[0]
        assert top_match.entity_id == session.session_id

        # 6. Reconstruct both memory stores from SQLite + Disk
        kg_reloaded = graph_builder.load_graph(db)
        assert kg_reloaded.node_count == kg.node_count
        assert kg_reloaded.edge_count == kg.edge_count

        vec_mem_reloaded = SemanticMemory(
            embedding_provider=MockEmbeddingProvider(dimension=64),
            db=db,
            index_path=faiss_path,
        )
        vec_mem_reloaded.load()
        assert vec_mem_reloaded.indexer.total_vectors == 2

        reloaded_search = vec_mem_reloaded.search(rec_session.text_representation, top_k=1)
        assert reloaded_search[0].entity_id == session.session_id


def test_privacy_preservation_in_graph_and_vector_memory() -> None:
    """Verifies that redacted/excluded events do not introduce unauthorized sensitive text."""
    base_t = datetime(2026, 9, 9, 11, 0, 0, tzinfo=timezone.utc)
    event_redacted = WorkspaceEvent(
        event_id="e_redacted",
        timestamp=base_t,
        event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
        process_name="notepad.exe",
        window_title="[REDACTED]",
        resource_path=None,
        metadata={"redacted": True},
    )

    segmenter = SessionSegmenter(session_gap_seconds=900.0)
    sessions = segmenter.segment([event_redacted])
    session = sessions[0]

    synthesizer = ContextDNASynthesizer()
    dna = synthesizer.synthesize(session, [event_redacted])

    # 1. Graph node inspection: no passwords or sensitive content
    builder = GraphBuilder()
    kg = builder.build_from_session(session, dna, [event_redacted])
    node = kg.get_node("event:e_redacted")
    assert node is not None
    assert node.properties["window_title"] == "[REDACTED]"

    # 2. Vector text representation inspection
    dna_text = SemanticMemory.format_context_dna_text(dna)
    assert "[REDACTED]" not in dna_text  # DNA summaries omit raw window titles altogether
