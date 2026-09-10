"""
Unit tests for ORBIS Phase 4 Workspace Knowledge Graph.
Tests typed graph nodes, explicit edges, GraphBuilder, topological queries,
and SQLite persistence / reload parity.
"""

from datetime import datetime, timedelta, timezone
import pytest

from core.memory.db import DatabaseManager
from core.memory.graph.builder import GraphBuilder
from core.memory.graph.graph import KnowledgeGraph
from core.memory.graph.models import EdgeType, GraphEdge, GraphNode, NodeType
from core.types import ContextDNA, Session, WorkspaceEvent, WorkspaceEventType


def _make_sample_entities():
    t0 = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
    e1 = WorkspaceEvent(
        event_id="evt_01",
        timestamp=t0,
        event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
        process_name="Code.exe",
        window_title="main.py - ORBIS",
        resource_path="c:\\Projects\\ORBIS\\main.py",
    )
    e2 = WorkspaceEvent(
        event_id="evt_02",
        timestamp=t0 + timedelta(minutes=2),
        event_type=WorkspaceEventType.WINDOW_ACTIVATED,
        process_name="chrome.exe",
        window_title="Python Docs - Chrome",
        resource_path="https://docs.python.org",
    )

    session = Session(
        session_id="sess_1001",
        start_time=t0,
        end_time=t0 + timedelta(minutes=5),
        duration_seconds=300.0,
        event_count=2,
        applications=["Code.exe", "chrome.exe"],
        process_names=["Code.exe", "chrome.exe"],
        resources=["c:\\Projects\\ORBIS\\main.py", "https://docs.python.org"],
        event_ids=["evt_01", "evt_02"],
        confidence=1.0,
    )

    dna = ContextDNA(
        dna_id="dna_2001",
        session_id="sess_1001",
        context_dna_version="1.0",
        temporal_context={"session_start": t0.isoformat(), "duration_seconds": 300.0},
        application_context={"active_applications": ["Code.exe", "chrome.exe"], "primary_application": "Code.exe"},
        resource_context={"observed_resource_paths": ["c:\\Projects\\ORBIS\\main.py"]},
        activity_context={"event_count": 2, "event_types": {"APPLICATION_ACTIVATED": 1, "WINDOW_ACTIVATED": 1}},
        privacy_context={"privacy_exclusions_active": True, "contains_sensitive_content": False},
        quality_indicators={"data_quality": "high", "boundary_confidence": 1.0},
        source_event_ids=["evt_01", "evt_02"],
    )

    return session, dna, [e1, e2]


def test_node_creation_and_properties() -> None:
    """Verifies creating strongly typed graph nodes."""
    kg = KnowledgeGraph()
    node = GraphNode(
        node_id="session:s1",
        node_type=NodeType.SESSION,
        label="Session 1",
        properties={"duration_seconds": 120.0},
    )
    kg.add_node(node)

    assert kg.node_count == 1
    retrieved = kg.get_node("session:s1")
    assert retrieved is not None
    assert retrieved.node_id == "session:s1"
    assert retrieved.node_type == NodeType.SESSION
    assert retrieved.label == "Session 1"
    assert retrieved.properties["duration_seconds"] == 120.0


def test_edge_creation_and_properties() -> None:
    """Verifies creating directed edges with explicit relationship types."""
    kg = KnowledgeGraph()
    n1 = GraphNode(node_id="session:s1", node_type=NodeType.SESSION, label="S1")
    n2 = GraphNode(node_id="app:code.exe", node_type=NodeType.APPLICATION, label="Code.exe")
    kg.add_node(n1)
    kg.add_node(n2)

    edge = GraphEdge(
        source_id="session:s1",
        target_id="app:code.exe",
        edge_type=EdgeType.USES,
        properties={"switch_count": 2},
    )
    kg.add_edge(edge)

    assert kg.edge_count == 1
    edges = kg.get_all_edges()
    assert len(edges) == 1
    assert edges[0].source_id == "session:s1"
    assert edges[0].target_id == "app:code.exe"
    assert edges[0].edge_type == EdgeType.USES
    assert edges[0].properties["switch_count"] == 2


def test_idempotent_duplicate_node_and_edge_prevention() -> None:
    """Verifies that adding the same node or edge twice is idempotent without duplicates."""
    kg = KnowledgeGraph()
    node = GraphNode(node_id="app:chrome.exe", node_type=NodeType.APPLICATION, label="chrome.exe")
    kg.add_node(node)
    kg.add_node(node)
    assert kg.node_count == 1

    edge = GraphEdge(source_id="app:chrome.exe", target_id="app:chrome.exe", edge_type=EdgeType.USES)
    kg.add_edge(edge)
    kg.add_edge(edge)
    assert kg.edge_count == 1


def test_graph_builder_session_construction() -> None:
    """Verifies GraphBuilder produces all factual nodes and relationships."""
    session, dna, events = _make_sample_entities()
    builder = GraphBuilder()
    kg = builder.build_from_session(session=session, dna=dna, events=events)

    # Check node presence:
    # 1 Session, 2 Applications, 2 Resources, 1 ContextDNA, 2 WorkspaceEvents = 8 nodes
    assert kg.node_count == 8
    assert kg.get_node("session:sess_1001") is not None
    assert kg.get_node("app:code.exe") is not None
    assert kg.get_node("app:chrome.exe") is not None
    assert kg.get_node("resource:c:/projects/orbis/main.py") is not None
    assert kg.get_node("resource:https://docs.python.org") is not None
    assert kg.get_node("dna:dna_2001") is not None
    assert kg.get_node("event:evt_01") is not None
    assert kg.get_node("event:evt_02") is not None

    # Check relationships:
    # session uses code.exe, uses chrome.exe
    # session observes main.py, observes python docs
    # session has_context dna
    # session contains evt_01, evt_02
    # evt_01 occurred_in session, occurred_in code.exe, references main.py
    # evt_02 occurred_in session, occurred_in chrome.exe, references python docs
    assert kg.edge_count > 10


def test_topological_session_queries() -> None:
    """Verifies targeted graph query methods for a session."""
    session, dna, events = _make_sample_entities()
    builder = GraphBuilder()
    kg = builder.build_from_session(session=session, dna=dna, events=events)

    # 1. Applications query
    apps = kg.get_session_applications("sess_1001")
    assert sorted(apps) == ["Code.exe", "chrome.exe"]

    # 2. Resources query
    resources = kg.get_session_resources("sess_1001")
    assert len(resources) == 2
    assert "c:\\Projects\\ORBIS\\main.py" in resources

    # 3. Events query
    evts = kg.get_session_events("sess_1001")
    assert sorted(evts) == ["evt_01", "evt_02"]

    # 4. Context DNA query
    dna_id = kg.get_session_context_dna("sess_1001")
    assert dna_id == "dna_2001"


def test_session_subgraph_extraction() -> None:
    """Verifies extracting an isolated subgraph centered on a session."""
    session, dna, events = _make_sample_entities()
    builder = GraphBuilder()
    kg = builder.build_from_session(session=session, dna=dna, events=events)

    subgraph = kg.get_session_subgraph("sess_1001")
    assert subgraph.node_count > 0
    assert subgraph.get_node("session:sess_1001") is not None
    # All directly connected nodes exist in subgraph
    assert subgraph.get_node("dna:dna_2001") is not None


def test_neighbor_queries() -> None:
    """Verifies querying node neighbors with direction filtering."""
    kg = KnowledgeGraph()
    n1 = GraphNode(node_id="session:s1", node_type=NodeType.SESSION, label="S1")
    n2 = GraphNode(node_id="app:code.exe", node_type=NodeType.APPLICATION, label="Code.exe")
    kg.add_node(n1)
    kg.add_node(n2)
    kg.add_edge(GraphEdge(source_id="session:s1", target_id="app:code.exe", edge_type=EdgeType.USES))

    # Outgoing neighbors from s1
    out_neighbors = kg.get_neighbors("session:s1", direction="out")
    assert len(out_neighbors) == 1
    assert out_neighbors[0].node_id == "app:code.exe"

    # Incoming neighbors to code.exe
    in_neighbors = kg.get_neighbors("app:code.exe", direction="in")
    assert len(in_neighbors) == 1
    assert in_neighbors[0].node_id == "session:s1"


def test_graph_persistence_and_reload_parity() -> None:
    """Verifies that KnowledgeGraph persists to SQLite and reloads with 100% parity."""
    session, dna, events = _make_sample_entities()
    builder = GraphBuilder()
    kg = builder.build_from_session(session=session, dna=dna, events=events)

    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    # Persist graph to SQLite
    builder.persist_graph(db, kg)

    # Verify tables in SQLite
    nodes_in_db = db.get_all_graph_nodes()
    edges_in_db = db.get_all_graph_edges()
    assert len(nodes_in_db) == kg.node_count
    assert len(edges_in_db) == kg.edge_count

    # Reload into a fresh KnowledgeGraph
    reloaded_kg = builder.load_graph(db)
    assert reloaded_kg.node_count == kg.node_count
    assert reloaded_kg.edge_count == kg.edge_count

    # Verify logical node and edge equivalence
    original_node_ids = {n.node_id for n in kg.get_all_nodes()}
    reloaded_node_ids = {n.node_id for n in reloaded_kg.get_all_nodes()}
    assert original_node_ids == reloaded_node_ids

    original_edge_tuples = {(e.source_id, e.target_id, e.edge_type) for e in kg.get_all_edges()}
    reloaded_edge_tuples = {(e.source_id, e.target_id, e.edge_type) for e in reloaded_kg.get_all_edges()}
    assert original_edge_tuples == reloaded_edge_tuples


def test_graph_traceability_to_source_records() -> None:
    """Verifies that graph nodes retain references back to source database IDs."""
    session, dna, _ = _make_sample_entities()
    builder = GraphBuilder()
    kg = builder.build_from_session(session=session, dna=dna)

    sess_node = kg.get_node(f"session:{session.session_id}")
    assert sess_node is not None
    assert sess_node.properties["session_id"] == session.session_id

    dna_node = kg.get_node(f"dna:{dna.dna_id}")
    assert dna_node is not None
    assert dna_node.properties["dna_id"] == dna.dna_id
    assert dna_node.properties["session_id"] == session.session_id


def test_malformed_and_empty_source_data_handling() -> None:
    """Verifies that GraphBuilder handles empty lists, None values, and missing fields gracefully."""
    builder = GraphBuilder()
    empty_session = Session(
        session_id="empty_sess",
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc),
        duration_seconds=0.0,
        event_count=0,
        applications=[],
        resources=[],
        event_ids=[],
    )

    kg = builder.build_from_session(empty_session, dna=None, events=None)
    assert kg.node_count == 1  # Only the session node
    assert kg.edge_count == 0
    assert kg.get_session_applications("empty_sess") == []
    assert kg.get_session_resources("empty_sess") == []
    assert kg.get_session_events("empty_sess") == []
    assert kg.get_session_context_dna("empty_sess") is None
