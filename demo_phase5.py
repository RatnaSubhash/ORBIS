"""
ORBIS Phase 5 Vertical-Slice MVP Demonstration Script.
Demonstrates:
1. End-to-end continuity pipeline:
   WorkspaceEvents -> Sessions -> Context DNA -> Knowledge Graph + Vector Memory -> Grounded Continuity Response.
2. Answering continuity queries ("What was I working on?", "Show my recent workspace context").
3. Grounded, non-hallucinatory synthesis strictly from stored evidence.
4. Comprehensive provenance preservation (session, DNA, events, graph nodes, vector records).
5. Safe failure when context is unavailable (empty database test).
6. Clear distinction between infrastructure verification and real model semantic evaluation.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from core.agent.llm.mock import MockLLMProvider
from core.agent.orchestrator import AgentOrchestrator
from core.continuity.dna import ContextDNASynthesizer
from core.continuity.models import ContinuityResponse
from core.continuity.service import ContinuityService
from core.continuity.sessionizer import SessionSegmenter
from core.memory.db import DatabaseManager
from core.memory.graph.builder import GraphBuilder
from core.memory.vector.embeddings.mock import MockEmbeddingProvider
from core.memory.vector.embeddings.sentence_transformer import (
    SentenceTransformerEmbeddingProvider,
)
from core.memory.vector.memory import SemanticMemory
from core.tools.continuity_tool import ContinuityTool
from core.tools.registry import ToolRegistry
from core.types import WorkspaceEvent, WorkspaceEventType


def run_phase5_demo() -> None:
    print("=" * 70)
    print("ORBIS PHASE 5 DEMONSTRATION: FIRST VERTICAL-SLICE MVP")
    print("Workspace Continuity: 'What was I working on?'")
    print("=" * 70)

    base_t = datetime(2026, 9, 10, 14, 0, 0, tzinfo=timezone.utc)

    # -------------------------------------------------------------
    # 1. Create realistic multi-session workspace observations
    # -------------------------------------------------------------
    # Session 1: ORBIS Backend Architecture & Documentation (4 events)
    events_s1 = [
        WorkspaceEvent(
            event_id="evt_dev_01",
            timestamp=base_t,
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="Code.exe",
            window_title="service.py - ORBIS",
            resource_path="c:\\Projects\\ORBIS\\core\\continuity\\service.py",
        ),
        WorkspaceEvent(
            event_id="evt_dev_02",
            timestamp=base_t + timedelta(minutes=3),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="chrome.exe",
            window_title="FastAPI Response Models - Google Chrome",
            resource_path="https://fastapi.tiangolo.com/tutorial/response-model/",
        ),
        WorkspaceEvent(
            event_id="evt_dev_03",
            timestamp=base_t + timedelta(minutes=6),
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="Code.exe",
            window_title="retriever.py - ORBIS",
            resource_path="c:\\Projects\\ORBIS\\core\\continuity\\retriever.py",
        ),
        WorkspaceEvent(
            event_id="evt_dev_04",
            timestamp=base_t + timedelta(minutes=10),
            event_type=WorkspaceEventType.WINDOW_ACTIVATED,
            process_name="Code.exe",
            window_title="grounding.py - ORBIS",
            resource_path="c:\\Projects\\ORBIS\\core\\continuity\\grounding.py",
        ),
    ]

    # Session 2: Terminal Test Verification (45 mins later, 3 events)
    events_s2 = [
        WorkspaceEvent(
            event_id="evt_term_01",
            timestamp=base_t + timedelta(minutes=55),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="WindowsTerminal.exe",
            window_title="PowerShell - pytest -v",
            resource_path="c:\\Projects\\ORBIS",
        ),
        WorkspaceEvent(
            event_id="evt_term_02",
            timestamp=base_t + timedelta(minutes=58),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="Code.exe",
            window_title="README.md - ORBIS",
            resource_path="c:\\Projects\\ORBIS\\README.md",
        ),
        WorkspaceEvent(
            event_id="evt_term_03",
            timestamp=base_t + timedelta(minutes=62),
            event_type=WorkspaceEventType.APPLICATION_ACTIVATED,
            process_name="WindowsTerminal.exe",
            window_title="PowerShell - git status",
            resource_path="c:\\Projects\\ORBIS",
        ),
    ]

    all_events = events_s1 + events_s2

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = str(Path(tmp_dir) / "demo_phase5.db")
        db = DatabaseManager(db_path=db_path)
        db.initialize()

        print("\n[Step 1] Recording 7 workspace observation events across 2 activity clusters...")
        for e in all_events:
            db.record_workspace_event(e)

        # -------------------------------------------------------------
        # 2. Session Segmentation & Context DNA Synthesis (Phase 3)
        # -------------------------------------------------------------
        print("[Step 2] Executing Session Segmentation & Context DNA Synthesis...")
        segmenter = SessionSegmenter(session_gap_seconds=900.0)
        sessions = segmenter.segment(all_events)
        assert len(sessions) == 2, f"Expected 2 sessions, got {len(sessions)}"

        synthesizer = ContextDNASynthesizer()
        dna_list = []
        for s in sessions:
            db.record_session(s)
            dna = synthesizer.synthesize(s, all_events)
            db.record_context_dna(dna)
            dna_list.append(dna)

        print(f"         Created {len(sessions)} sessions and {len(dna_list)} Context DNA snapshots in SQLite.")

        # -------------------------------------------------------------
        # 3. Knowledge Graph & Semantic Vector Memory (Phase 4)
        # -------------------------------------------------------------
        print("[Step 3] Building Knowledge Graph and indexing in Vector Memory...")
        graph_builder = GraphBuilder()
        kg = None
        for s, dna in zip(sessions, dna_list):
            kg = graph_builder.build_from_session(s, dna, all_events, graph=kg)
        graph_builder.persist_graph(db, kg)

        # Determine embedding provider: test local cache without downloading
        provider = None
        is_real_model = False
        try:
            from transformers.utils.hub import try_to_load_from_cache
            cached = try_to_load_from_cache("sentence-transformers/all-MiniLM-L6-v2", "config.json")
            if cached and isinstance(cached, str):
                provider = SentenceTransformerEmbeddingProvider("all-MiniLM-L6-v2")
                is_real_model = True
                print("         [INFO] Local SentenceTransformer ('all-MiniLM-L6-v2') detected. Using REAL semantic embeddings.")
        except Exception:
            pass

        if provider is None:
            provider = MockEmbeddingProvider(dimension=384, model_name="deterministic-mock-384")
            print("         [INFO] SentenceTransformer weights not locally cached.")
            print("         [NOTE] Using deterministic MockEmbeddingProvider for offline INFRASTRUCTURE VERIFICATION.")
            print("                (Verifies indexing, FAISS retrieval, and ID mapping without network access).")

        faiss_path = str(Path(tmp_dir) / "demo_phase5.faiss")
        vec_mem = SemanticMemory(embedding_provider=provider, db=db, index_path=faiss_path)
        for s in sessions:
            vec_mem.index_session(s)
        for dna in dna_list:
            vec_mem.index_context_dna(dna)

        print(f"         Knowledge Graph: {kg.node_count} nodes, {kg.edge_count} edges.")
        print(f"         Vector Memory:   {vec_mem.indexer.total_vectors} vectors indexed.")

        # -------------------------------------------------------------
        # 4. Phase 5 Continuity Service & Tool Execution
        # -------------------------------------------------------------
        print("\n" + "-" * 70)
        print("PART 1: EXECUTING PRIMARY CONTINUITY QUERY")
        print("-" * 70)

        service = ContinuityService(db=db, graph=kg, vector_memory=vec_mem)

        query_1 = "What was I working on?"
        print(f"User Request: '{query_1}'\n")

        resp_1: ContinuityResponse = service.execute_query(query_1)

        print("--- Grounded Summary ---")
        print(resp_1.grounded_summary)
        print("\n--- Structured Evidence Metadata ---")
        print(f"Has Sufficient Context : {resp_1.has_sufficient_context}")
        print(f"Active Applications    : {resp_1.active_applications}")
        print(f"Observed Resources     : {resp_1.primary_resources}")
        print(f"Last Activity          : {resp_1.last_activity_timestamp.isoformat() if resp_1.last_activity_timestamp else 'None'}")
        print(f"Confidence Heuristic   : {resp_1.confidence_score:.3f} (Deterministic evidence-quality score, NOT a probability)")

        print("\n--- Complete Provenance Trace ---")
        print(f"Session IDs            : {resp_1.provenance.session_ids}")
        print(f"Context DNA IDs        : {resp_1.provenance.dna_ids}")
        print(f"Event IDs              : {resp_1.provenance.event_ids}")
        print(f"Graph Node IDs ({len(resp_1.provenance.graph_node_ids)}): {resp_1.provenance.graph_node_ids[:5]}...")
        print(f"Vector Record IDs      : {resp_1.provenance.vector_record_ids}")

        # -------------------------------------------------------------
        # 5. Query 2: What was my last session about?
        # -------------------------------------------------------------
        print("\n" + "-" * 70)
        print("PART 2: QUERYING SPECIFIC RECENT CONTEXT")
        print("-" * 70)
        query_2 = "What was my last session about?"
        print(f"User Request: '{query_2}'\n")
        resp_2 = service.execute_query(query_2)
        print(f"Grounded Summary: {resp_2.grounded_summary}")
        print(f"Confidence Score: {resp_2.confidence_score:.3f}")

        # -------------------------------------------------------------
        # 6. Negative Test: Empty Database (Zero Hallucination)
        # -------------------------------------------------------------
        print("\n" + "-" * 70)
        print("PART 3: SAFE FAILURE ON EMPTY DATABASE (ZERO HALLUCINATION)")
        print("-" * 70)
        empty_db = DatabaseManager(db_path=":memory:")
        empty_db.initialize()
        empty_service = ContinuityService(db=empty_db)

        resp_empty = empty_service.execute_query("What was I working on?")
        print(f"User Request: 'What was I working on?' (against empty database)")
        print(f"Has Sufficient Context: {resp_empty.has_sufficient_context}")
        print(f"Confidence Score      : {resp_empty.confidence_score}")
        print(f"Response Summary      : {resp_empty.grounded_summary}")
        assert resp_empty.has_sufficient_context is False
        assert resp_empty.confidence_score == 0.0

        # -------------------------------------------------------------
        # 7. Orchestrator Integration Demonstration
        # -------------------------------------------------------------
        print("\n" + "-" * 70)
        print("PART 4: ORCHESTRATOR INTEGRATION (run_continuity)")
        print("-" * 70)
        tool_registry = ToolRegistry()
        tool_registry.register(ContinuityTool(continuity_service=service))
        orchestrator = AgentOrchestrator(
            llm_provider=MockLLMProvider(),
            tool_registry=tool_registry,
            db_manager=db,
            continuity_service=service,
        )

        import asyncio

        async def run_orch():
            return await orchestrator.run_continuity("What was I working on?")

        orch_resp = asyncio.run(run_orch())
        print(f"Orchestrator Result: {orch_resp.grounded_summary}")
        print(f"Context Grounded   : {orch_resp.has_sufficient_context}")

    print("\n" + "=" * 70)
    print("PHASE 5 VERTICAL-SLICE MVP DEMONSTRATION COMPLETE: ALL PASS")
    print("=" * 70)


if __name__ == "__main__":
    run_phase5_demo()
