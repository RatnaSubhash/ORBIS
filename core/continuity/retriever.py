"""
Continuity Retrieval Layer for ORBIS Phase 5.
Combines SQLite session data, Context DNA, Knowledge Graph topology,
and Semantic Vector Memory to retrieve factual continuity evidence.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from core.continuity.models import (
    ContinuityQuery,
    ContinuityQueryType,
    ProvenanceSource,
    RetrievedContextEvidence,
)
from core.memory.db import DatabaseManager
from core.memory.graph.graph import KnowledgeGraph
from core.memory.vector.memory import SemanticMemory
from core.types import Session


class ContinuityRetriever:
    """
    Retrieves and correlates factual workspace evidence from stored ORBIS memory
    to support answering continuity questions.
    """

    def __init__(
        self,
        db: DatabaseManager,
        graph: Optional[KnowledgeGraph] = None,
        vector_memory: Optional[SemanticMemory] = None,
    ) -> None:
        self.db = db
        self.graph = graph
        self.vector_memory = vector_memory

    def retrieve(self, query: ContinuityQuery) -> List[RetrievedContextEvidence]:
        """
        Retrieves top candidate session evidence for a given continuity query.
        Preserves complete provenance only for genuinely existing entities.
        """
        # 1. Query sessions from SQLite
        start_time_filter: Optional[datetime] = None
        if query.time_window_hours is not None and query.time_window_hours > 0:
            start_time_filter = datetime.now(timezone.utc) - timedelta(
                hours=query.time_window_hours
            )

        sessions = self.db.get_sessions(
            limit=100,
            start_time=start_time_filter,
        )

        if not sessions:
            return []

        # Sort sessions chronologically descending (most recent first)
        sessions_sorted = sorted(sessions, key=lambda s: s.end_time, reverse=True)

        # 2. Perform Semantic Vector Search if vector memory is available and populated
        session_similarities: Dict[str, float] = {}
        if self.vector_memory and self.vector_memory.indexer.total_vectors > 0:
            try:
                results = self.vector_memory.search(
                    query=query.query_text, top_k=max(5, query.max_results * 2)
                )
                for res in results:
                    if res.entity_type == "SESSION":
                        session_similarities[res.entity_id] = res.similarity
                    elif res.entity_type == "CONTEXT_DNA":
                        sid = res.source_metadata.get("session_id")
                        if sid and (sid not in session_similarities or res.similarity > session_similarities[sid]):
                            session_similarities[sid] = res.similarity
            except Exception:
                # Fall back gracefully if search fails
                pass

        # 3. Determine candidate selection strategy
        # For general recency / continuity queries ("What was I working on?"), recency dominates.
        # For topical queries with strong semantic matches, balance semantic similarity.
        is_generic_query = query.query_type in (
            ContinuityQueryType.WHAT_WAS_I_WORKING_ON,
            ContinuityQueryType.RECENT_CONTEXT,
            ContinuityQueryType.LAST_SESSION,
        )

        if is_generic_query or not session_similarities:
            candidates = sessions_sorted[: query.max_results]
        else:
            # Rank by semantic similarity first, then recency
            def _rank_key(sess: Session) -> float:
                sim = session_similarities.get(sess.session_id, -1.0)
                return sim

            candidates = sorted(sessions_sorted, key=_rank_key, reverse=True)[: query.max_results]

        # 4. Enrich each candidate session into a RetrievedContextEvidence instance
        evidence_list: List[RetrievedContextEvidence] = []
        for sess in candidates:
            evidence = self._enrich_session(sess, session_similarities.get(sess.session_id))
            evidence_list.append(evidence)

        return evidence_list

    def _enrich_session(
        self, session: Session, vector_similarity: Optional[float]
    ) -> RetrievedContextEvidence:
        """
        Enriches a raw Session with ContextDNA, Knowledge Graph connections,
        and genuine provenance IDs.
        """
        # Context DNA lookup
        dna = self.db.get_context_dna(session.session_id)
        primary_app: Optional[str] = None
        if dna and dna.application_context:
            primary_app = dna.application_context.get("primary_application")

        # Knowledge Graph enrichment
        apps = list(session.applications)
        resources = list(session.resources)
        graph_node_ids: List[str] = []

        if self.graph is not None:
            # Check if session node exists in graph
            sess_nid = f"session:{session.session_id}"
            if self.graph.get_node(sess_nid) is not None:
                graph_node_ids.append(sess_nid)

                # Fetch topological apps & resources from graph
                g_apps = self.graph.get_session_applications(session.session_id)
                for a in g_apps:
                    if a not in apps:
                        apps.append(a)
                    app_nid = f"app:{a.lower()}"
                    if self.graph.get_node(app_nid) is not None and app_nid not in graph_node_ids:
                        graph_node_ids.append(app_nid)

                g_resources = self.graph.get_session_resources(session.session_id)
                for r in g_resources:
                    if r not in resources:
                        resources.append(r)
                    res_nid = f"resource:{r.replace('\\', '/').lower()}"
                    if self.graph.get_node(res_nid) is not None and res_nid not in graph_node_ids:
                        graph_node_ids.append(res_nid)

                if dna:
                    dna_nid = f"dna:{dna.dna_id}"
                    if self.graph.get_node(dna_nid) is not None and dna_nid not in graph_node_ids:
                        graph_node_ids.append(dna_nid)

        if not primary_app and apps:
            primary_app = apps[0]

        # Vector record ID lookup from SQLite (genuine records only)
        vector_record_ids: List[str] = []
        try:
            vec_recs = self.db.get_vector_records_by_entity("SESSION", session.session_id)
            for vr in vec_recs:
                if vr.get("vector_id"):
                    vector_record_ids.append(vr["vector_id"])
            if dna:
                dna_vecs = self.db.get_vector_records_by_entity("CONTEXT_DNA", dna.dna_id)
                for vr in dna_vecs:
                    if vr.get("vector_id") and vr["vector_id"] not in vector_record_ids:
                        vector_record_ids.append(vr["vector_id"])
        except Exception:
            pass

        # Build genuine ProvenanceSource without fabrication
        provenance = ProvenanceSource(
            session_ids=[session.session_id],
            dna_ids=[dna.dna_id] if dna else [],
            event_ids=list(session.event_ids),
            graph_node_ids=graph_node_ids,
            vector_record_ids=vector_record_ids,
        )

        return RetrievedContextEvidence(
            session_id=session.session_id,
            start_time=session.start_time,
            end_time=session.end_time,
            duration_seconds=session.duration_seconds,
            applications=apps,
            primary_application=primary_app,
            resources=resources,
            event_count=session.event_count,
            vector_similarity=vector_similarity,
            provenance=provenance,
        )
