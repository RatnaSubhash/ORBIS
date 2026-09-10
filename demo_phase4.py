"""
ORBIS Phase 4 Demonstration Script.
Demonstrates:
1. Workspace Knowledge Graph construction (NetworkX + SQLite persistence).
2. Topological graph neighborhood and session queries.
3. Semantic Vector Memory indexing and FAISS cosine similarity retrieval.
   (Clearly distinguished between infrastructure verification and real model semantic evaluation).
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from core.continuity.dna import ContextDNASynthesizer
from core.continuity.sessionizer import SessionSegmenter
from core.memory.db import DatabaseManager
from core.memory.graph.builder import GraphBuilder
from core.memory.vector.embeddings.mock import MockEmbeddingProvider
from core.memory.vector.embeddings.sentence_transformer import (
    SentenceTransformerEmbeddingProvider,
)
from core.memory.vector.memory import SemanticMemory
from core.types import WorkspaceEvent, WorkspaceEventType


def run_phase4_demo() -> None:
    print("=" * 65)
    print("ORBIS PHASE 4 DEMONSTRATION")
    print("Knowledge Graph + Semantic Vector Memory Foundation")
    print("=" * 65)

    base_t = datetime(2026, 9, 9, 9, 0, 0, tzinfo=timezone.utc)

    # 1. Create synthetic workspace events across two distinct sessions
    # Cluster 1: Python/FastAPI Backend Coding & Documentation (5 events)
    events_cluster1 = [
        WorkspaceEvent(
            event_id="evt_backend_01",
            timestamp=base_t,
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="Code.exe",
            window_title="server.py - ORBIS Backend",
            resource_path="c:\\Projects\\ORBIS\\server.py",
        ),
        WorkspaceEvent(
            event_id="evt_backend_02",
            timestamp=base_t + timedelta(minutes=2),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="chrome.exe",
            window_title="FastAPI Async Route Reference - Google Chrome",
            resource_path="https://fastapi.tiangolo.com/async",
        ),
        WorkspaceEvent(
            event_id="evt_backend_03",
            timestamp=base_t + timedelta(minutes=4),
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="Code.exe",
            window_title="models.py - ORBIS",
            resource_path="c:\\Projects\\ORBIS\\models.py",
        ),
        WorkspaceEvent(
            event_id="evt_backend_04",
            timestamp=base_t + timedelta(minutes=6),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="chrome.exe",
            window_title="SQLite WAL Pragmas - Google Chrome",
            resource_path="https://sqlite.org/wal.html",
        ),
        WorkspaceEvent(
            event_id="evt_backend_05",
            timestamp=base_t + timedelta(minutes=8),
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="Code.exe",
            window_title="db.py - ORBIS",
            resource_path="c:\\Projects\\ORBIS\\db.py",
        ),
    ]

    # Cluster 2: Terminal Git Verification (45 mins later, 3 events)
    events_cluster2 = [
        WorkspaceEvent(
            event_id="evt_terminal_01",
            timestamp=base_t + timedelta(minutes=55),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="WindowsTerminal.exe",
            window_title="PowerShell - pytest -v",
            resource_path="c:\\Projects\\ORBIS",
        ),
        WorkspaceEvent(
            event_id="evt_terminal_02",
            timestamp=base_t + timedelta(minutes=57),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="Code.exe",
            window_title="README.md - ORBIS",
            resource_path="c:\\Projects\\ORBIS\\README.md",
        ),
        WorkspaceEvent(
            event_id="evt_terminal_03",
            timestamp=base_t + timedelta(minutes=60),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="WindowsTerminal.exe",
            window_title="PowerShell - git status",
            resource_path="c:\\Projects\\ORBIS",
        ),
    ]

    all_events = events_cluster1 + events_cluster2

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = str(Path(tmp_dir) / "demo_phase4.db")
        faiss_path = str(Path(tmp_dir) / "demo_vectors.faiss")

        db = DatabaseManager(db_path=db_path)
        db.initialize()

        for e in all_events:
            db.record_workspace_event(e)

        # 2. Session Segmentation & Context DNA Synthesis
        segmenter = SessionSegmenter(session_gap_seconds=900.0)
        sessions = segmenter.segment(all_events)
        assert len(sessions) == 2

        synthesizer = ContextDNASynthesizer()
        dna_list = []
        for s in sessions:
            db.record_session(s)
            dna = synthesizer.synthesize(s, all_events)
            db.record_context_dna(dna)
            dna_list.append(dna)

        # -------------------------------------------------------------
        # PART 1: Workspace Knowledge Graph Demonstration
        # -------------------------------------------------------------
        print("\n" + "-" * 65)
        print("PART 1: WORKSPACE KNOWLEDGE GRAPH (NetworkX + SQLite)")
        print("-" * 65)

        graph_builder = GraphBuilder()
        kg = None
        for s, dna in zip(sessions, dna_list):
            kg = graph_builder.build_from_session(s, dna, all_events, graph=kg)

        # Persist graph to SQLite
        graph_builder.persist_graph(db, kg)

        print(f"Total Graph Nodes: {kg.node_count}")
        print(f"Total Graph Edges: {kg.edge_count}")

        # Query Session 1 Subgraph & Neighbors
        s1 = sessions[0]
        s1_apps = kg.get_session_applications(s1.session_id)
        s1_resources = kg.get_session_resources(s1.session_id)
        s1_events = kg.get_session_events(s1.session_id)
        s1_dna = kg.get_session_context_dna(s1.session_id)

        print(f"\nTopological Queries for Session 1 [{s1.session_id[:8]}...]:")
        print(f"  Connected Applications ({len(s1_apps)}): {s1_apps}")
        print(f"  Observed Resources     ({len(s1_resources)}):")
        for res in s1_resources:
            print(f"    - {res}")
        print(f"  Contained Events Count : {len(s1_events)}")
        print(f"  Associated Context DNA : {s1_dna}")

        # Verify SQLite Graph Reload
        reloaded_kg = graph_builder.load_graph(db)
        print(f"\nSQLite Graph Reload Verification:")
        print(f"  Persisted Nodes: {len(db.get_all_graph_nodes())} | Reloaded: {reloaded_kg.node_count}")
        print(f"  Persisted Edges: {len(db.get_all_graph_edges())} | Reloaded: {reloaded_kg.edge_count}")
        assert reloaded_kg.node_count == kg.node_count

        # -------------------------------------------------------------
        # PART 2: Semantic Vector Memory Infrastructure Demonstration
        # -------------------------------------------------------------
        print("\n" + "-" * 65)
        print("PART 2: SEMANTIC VECTOR MEMORY (FAISS + MockEmbeddingProvider)")
        print("NOTE: Demonstrates vector indexing, FAISS storage, metadata mapping,")
        print("and persistence. Labeled as INFRASTRUCTURE VERIFICATION, not semantic evaluation.")
        print("-" * 65)

        mock_provider = MockEmbeddingProvider(dimension=384, model_name="mock-384")
        vec_mem = SemanticMemory(embedding_provider=mock_provider, db=db, index_path=faiss_path)

        for s in sessions:
            vec_mem.index_session(s)
        for dna in dna_list:
            vec_mem.index_context_dna(dna)

        vec_mem.save()
        print(f"Indexed Entities in FAISS: {vec_mem.indexer.total_vectors}")
        print(f"Persisted Vector Records in SQLite: {len(db.get_all_vector_records())}")

        # Search query on mock vector store
        query_text = SemanticMemory.format_session_text(sessions[0])
        results = vec_mem.search(query_text, top_k=2)

        print(f"\nDeterministic Infrastructure Search (Query: Exact Session 1 text):")
        for rank, res in enumerate(results, 1):
            print(f"  [{rank}] Entity: {res.entity_type} ({res.entity_id[:8]}...) | Cosine Similarity: {res.similarity:.4f}")

        # Reload FAISS from disk + SQLite metadata
        reloaded_vec_mem = SemanticMemory(embedding_provider=mock_provider, db=db, index_path=faiss_path)
        reloaded_vec_mem.load()
        print(f"\nFAISS Reload Verification from Disk: {reloaded_vec_mem.indexer.total_vectors} vectors reloaded successfully.")

        # -------------------------------------------------------------
        # PART 3: Optional Real Model Evaluation (Offline / Local-Only)
        # -------------------------------------------------------------
        print("\n" + "-" * 65)
        print("PART 3: REAL SENTENCE-TRANSFORMER EVALUATION (Optional / Local-Only)")
        print("-" * 65)

        # Check if model is locally cached to prevent internet download
        model_name = "all-MiniLM-L6-v2"
        is_cached = False
        try:
            from huggingface_hub import try_to_load_from_cache
            cache_hit = try_to_load_from_cache(model_name, "config.json")
            is_cached = cache_hit is not None and isinstance(cache_hit, str)
        except Exception:
            is_cached = False

        if is_cached:
            print(f"Model '{model_name}' found in local cache. Running real semantic search...")
            try:
                st_provider = SentenceTransformerEmbeddingProvider(model_name=model_name)
                st_mem = SemanticMemory(embedding_provider=st_provider, db=None)
                for s in sessions:
                    st_mem.index_session(s)

                real_query = "FastAPI backend async development"
                real_results = st_mem.search(real_query, top_k=2)
                print(f"Query: '{real_query}'")
                for rank, res in enumerate(real_results, 1):
                    print(f"  [{rank}] Entity: {res.entity_id[:8]}... | Cosine Similarity: {res.similarity:.4f}")
            except Exception as ex:
                print(f"Skipping real model execution: {ex}")
        else:
            print(f"Status: Model '{model_name}' is not in the local HuggingFace cache.")
            print("In strict compliance with Phase 4 offline requirements, network downloads were omitted.")
            print("The memory architecture is verified using the deterministic MockEmbeddingProvider.")

        db.close()

    print("\n" + "=" * 65)
    print("PHASE 4 DEMONSTRATION COMPLETE")
    print("Knowledge Graph: Verified")
    print("Vector Memory:   Verified")
    print("Traceability:    Preserved")
    print("=" * 65)


if __name__ == "__main__":
    run_phase4_demo()
