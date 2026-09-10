"""
Intent Evidence Extractor for ORBIS Phase 9 Intent Continuity Engine (ICE).
Correlates multi-modal factual telemetry from Phase 5 ContinuityRetriever, SQLite
audit logs, WorkspaceEvents, Knowledge Graph topology, and Semantic Memory.
"""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional, Tuple

from core.continuity.models import ContinuityQuery, ContinuityQueryType, RetrievedContextEvidence
from core.continuity.retriever import ContinuityRetriever
from core.ice.models import (
    ICEQuery,
    IntentEvidenceItem,
    IntentEvidenceType,
    IntentProvenance,
)
from core.memory.db import DatabaseManager
from core.memory.graph.graph import KnowledgeGraph
from core.memory.vector.memory import SemanticMemory
from core.types import Session, WorkspaceEvent


class ExtractedSessionContext:
    """Factual telemetry and correlated evidence extracted for a single session."""

    def __init__(
        self,
        session: Session,
        evidence_items: List[IntentEvidenceItem],
        provenance: IntentProvenance,
        last_terminal_exit_code: Optional[int] = None,
        has_git_commit: bool = False,
        has_goal_completion_evidence: bool = False,
        has_uncommitted_modifications: bool = False,
        has_active_process: bool = False,
        subsequent_disjoint_sessions: int = 0,
        raw_cosine_similarity: Optional[float] = None,
        graph_proximity: float = 0.0,
    ) -> None:
        self.session = session
        self.evidence_items = evidence_items
        self.provenance = provenance
        self.last_terminal_exit_code = last_terminal_exit_code
        self.has_git_commit = has_git_commit
        self.has_goal_completion_evidence = has_goal_completion_evidence
        self.has_uncommitted_modifications = has_uncommitted_modifications
        self.has_active_process = has_active_process
        self.subsequent_disjoint_sessions = subsequent_disjoint_sessions
        self.raw_cosine_similarity = raw_cosine_similarity
        self.graph_proximity = graph_proximity


class IntentEvidenceExtractor:
    """
    Extracts, filters, and correlates multi-modal historical evidence for ICE queries.
    """

    def __init__(
        self,
        db: DatabaseManager,
        retriever: Optional[ContinuityRetriever] = None,
        graph: Optional[KnowledgeGraph] = None,
        vector_memory: Optional[SemanticMemory] = None,
    ) -> None:
        self.db = db
        self.retriever = retriever or ContinuityRetriever(
            db=db, graph=graph, vector_memory=vector_memory
        )
        self.graph = graph
        self.vector_memory = vector_memory

    def extract_evidence(self, query: ICEQuery) -> List[ExtractedSessionContext]:
        """
        Retrieves top candidate sessions and enriches each with multi-modal evidence.
        """
        # 1. Fetch sessions via Phase 5 ContinuityRetriever
        cont_query = ContinuityQuery(
            query_text=query.query_text,
            query_type=ContinuityQueryType.GENERAL_CONTINUITY,
            time_window_hours=query.time_window_hours,
            max_results=query.max_intents * 2,  # Fetch wider candidate pool
        )
        retrieved_list: List[RetrievedContextEvidence] = self.retriever.retrieve(cont_query)

        if not retrieved_list:
            return []

        all_sessions = self.db.get_sessions(limit=100)
        sessions_by_id: Dict[str, Session] = {s.session_id: s for s in all_sessions}

        # Chronologically sorted sessions descending
        all_sorted_desc = sorted(all_sessions, key=lambda s: s.end_time, reverse=True)

        results: List[ExtractedSessionContext] = []

        for ret in retrieved_list:
            sess = sessions_by_id.get(ret.session_id)
            if not sess:
                continue

            extracted = self._extract_for_session(sess, ret, all_sorted_desc)
            results.append(extracted)

        return results

    def _extract_for_session(
        self,
        session: Session,
        retrieved: RetrievedContextEvidence,
        all_sorted_desc: List[Session],
    ) -> ExtractedSessionContext:
        """Enriches an individual session with audit logs, events, graph, and signals."""
        evidence_items: List[IntentEvidenceItem] = []
        audit_log_ids: List[int] = []

        # 1. Base session evidence
        sess_ev = IntentEvidenceItem(
            evidence_type=IntentEvidenceType.SESSION,
            timestamp=session.end_time,
            description=(
                f"Observed session {session.session_id[:8]} with {session.event_count} events "
                f"spanning {session.duration_seconds:.1f}s."
            ),
            confidence_contribution=0.2,
            source_ref=session.session_id,
            is_observed_fact=True,
        )
        evidence_items.append(sess_ev)

        # 2. Context DNA evidence
        dna = self.db.get_context_dna(session.session_id)
        if dna:
            primary_app = None
            if dna.application_context:
                primary_app = dna.application_context.get("primary_application")
            dna_ev = IntentEvidenceItem(
                evidence_type=IntentEvidenceType.CONTEXT_DNA,
                timestamp=dna.created_at,
                description=f"Context DNA v{dna.context_dna_version} (primary app: {primary_app or 'unknown'}).",
                confidence_contribution=0.15,
                source_ref=dna.dna_id,
                is_observed_fact=True,
            )
            evidence_items.append(dna_ev)

        # 3. Correlate SQLite audit logs occurring during or immediately after the session
        # Retrieve audit logs and check if actions occurred around this session
        all_audit_logs = self.db.get_audit_logs()
        last_terminal_exit_code: Optional[int] = None
        has_git_commit = False
        has_goal_completion_evidence = False
        has_uncommitted_modifications = False

        sess_start = session.start_time
        sess_end = session.end_time

        session_actions: List[Dict[str, Any]] = []
        for log in all_audit_logs:
            log_time = None
            try:
                # audit_logs table timestamp format
                if "timestamp" in log and log["timestamp"]:
                    log_time = datetime.fromisoformat(log["timestamp"])
            except Exception:
                pass

            # If log has task_id or overlaps in time
            overlaps = False
            if log_time:
                if log_time.tzinfo is None:
                    log_time = log_time.replace(tzinfo=timezone.utc)
                overlaps = (sess_start <= log_time <= sess_end) or (
                    abs((log_time - sess_end).total_seconds()) <= 300
                )

            if overlaps or (log.get("task_id") == session.session_id):
                session_actions.append(log)
                audit_log_ids.append(log["id"])

        # Inspect correlated actions
        for act in session_actions:
            action_name = act.get("action_name", "")
            result_data = {}
            params = {}
            try:
                params = json.loads(act.get("parameters_json") or "{}")
                result_data = json.loads(act.get("result_json") or "{}")
            except Exception:
                pass

            # Check terminal actions
            if action_name == "terminal":
                command = params.get("command", "")
                output_obj = result_data.get("output", {})
                exit_code = output_obj.get("exit_code") if isinstance(output_obj, dict) else None
                if exit_code is not None:
                    last_terminal_exit_code = exit_code

                # Check if it was a git commit
                if "git commit" in command:
                    has_git_commit = True
                    evidence_items.append(
                        IntentEvidenceItem(
                            evidence_type=IntentEvidenceType.AUDIT_LOG_ACTION,
                            timestamp=session.end_time,
                            description=f"Terminal executed Git commit command: '{command}' (exit code {exit_code}).",
                            confidence_contribution=0.1,
                            source_ref=str(act["id"]),
                            is_observed_fact=True,
                        )
                    )

                # Check if pytest or tests were run
                if "pytest" in command or "test" in command:
                    if exit_code == 0 and act.get("verified"):
                        has_goal_completion_evidence = True
                        evidence_items.append(
                            IntentEvidenceItem(
                                evidence_type=IntentEvidenceType.AUDIT_LOG_ACTION,
                                timestamp=session.end_time,
                                description=f"Test verification command '{command}' succeeded with exit code 0.",
                                confidence_contribution=0.25,
                                source_ref=str(act["id"]),
                                is_observed_fact=True,
                            )
                        )
                    elif exit_code != 0:
                        evidence_items.append(
                            IntentEvidenceItem(
                                evidence_type=IntentEvidenceType.TERMINAL_OUTPUT,
                                timestamp=session.end_time,
                                description=f"Test command '{command}' failed with exit code {exit_code}.",
                                confidence_contribution=0.2,
                                source_ref=str(act["id"]),
                                is_observed_fact=True,
                            )
                        )

            # Check document writes
            if action_name in ("document", "document_tool"):
                has_uncommitted_modifications = True
                if act.get("verified"):
                    # Writing a verified document is completion evidence for document tasks
                    has_goal_completion_evidence = True
                    evidence_items.append(
                        IntentEvidenceItem(
                            evidence_type=IntentEvidenceType.AUDIT_LOG_ACTION,
                            timestamp=session.end_time,
                            description=f"Verified document created at '{params.get('file_path')}'.",
                            confidence_contribution=0.25,
                            source_ref=str(act["id"]),
                            is_observed_fact=True,
                        )
                    )

        # 4. Resource evidence
        for res_path in session.resources:
            norm_path = res_path.replace("\\", "/")
            evidence_items.append(
                IntentEvidenceItem(
                    evidence_type=IntentEvidenceType.FILE_RESOURCE,
                    timestamp=session.end_time,
                    description=f"Observed resource modification/access: '{norm_path}'.",
                    confidence_contribution=0.1,
                    source_ref=norm_path,
                    is_observed_fact=True,
                )
            )

        # 5. Calculate subsequent disjoint sessions
        subsequent_count = 0
        for s in all_sorted_desc:
            if s.start_time > session.end_time:
                # Check resource overlap
                shared = set(session.resources).intersection(set(s.resources))
                if not shared:
                    subsequent_count += 1

        # 6. Graph Proximity
        graph_proximity = 0.0
        if self.graph is not None:
            sess_nid = f"session:{session.session_id}"
            neighbors = self.graph.get_neighbors(sess_nid)
            if neighbors:
                graph_proximity = min(1.0, len(neighbors) / 5.0)

        # 7. Compile genuine IntentProvenance
        provenance = IntentProvenance(
            session_ids=[session.session_id],
            dna_ids=[dna.dna_id] if dna else [],
            event_ids=list(session.event_ids),
            graph_node_ids=retrieved.provenance.graph_node_ids,
            vector_record_ids=retrieved.provenance.vector_record_ids,
            resource_paths=list(session.resources),
            audit_log_ids=audit_log_ids,
        )

        return ExtractedSessionContext(
            session=session,
            evidence_items=evidence_items,
            provenance=provenance,
            last_terminal_exit_code=last_terminal_exit_code,
            has_git_commit=has_git_commit,
            has_goal_completion_evidence=has_goal_completion_evidence,
            has_uncommitted_modifications=has_uncommitted_modifications or bool(session.resources),
            has_active_process=False,
            subsequent_disjoint_sessions=subsequent_count,
            raw_cosine_similarity=retrieved.vector_similarity,
            graph_proximity=graph_proximity,
        )
