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
        if query.start_time is not None:
            start_time_filter = query.start_time
        elif query.time_window_hours is not None and query.time_window_hours > 0:
            start_time_filter = datetime.now(timezone.utc) - timedelta(
                hours=query.time_window_hours
            )

        sessions = self.db.get_sessions(
            limit=100,
            start_time=start_time_filter,
        )

        # 1.1 Idempotently detect unsegmented events and synthesize in-progress / historical sessions
        all_stored_sessions = self.db.get_sessions(limit=500)
        processed_event_ids = {eid for s in all_stored_sessions for eid in s.event_ids}

        raw_events = self.db.get_workspace_events(limit=500, start_time=start_time_filter)
        unsegmented_events = [e for e in raw_events if e.event_id not in processed_event_ids]

        if unsegmented_events:
            from core.continuity.sessionizer import SessionSegmenter
            from core.continuity.dna import ContextDNASynthesizer

            segmenter = SessionSegmenter()
            synthesizer = ContextDNASynthesizer()

            chunks = segmenter.segment(unsegmented_events)
            existing_ids = {s.session_id for s in sessions}

            for chunk_sess in chunks:
                try:
                    self.db.record_session(chunk_sess)
                    dna = synthesizer.synthesize(chunk_sess, unsegmented_events)
                    self.db.record_context_dna(dna)
                except Exception:
                    pass
                if chunk_sess.session_id not in existing_ids:
                    sessions.append(chunk_sess)
                    existing_ids.add(chunk_sess.session_id)

        if not sessions:
            return []

        # Determine effective continuity cutoff
        auto_cutoff = self._detect_pre_orbis_cutoff()
        if query.cutoff_time and auto_cutoff:
            effective_cutoff = min(query.cutoff_time, auto_cutoff)
        else:
            effective_cutoff = query.cutoff_time or auto_cutoff

        # Filter and rank candidate sessions, strictly excluding control plane and prioritizing real user workspace
        sessions_sorted = self._filter_and_rank_workspace_sessions(
            sessions,
            effective_cutoff,
            start_bound=query.start_time,
            end_bound=query.end_time,
        )
        if not sessions_sorted:
            return []

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

    def _detect_pre_orbis_cutoff(self) -> Optional[datetime]:
        """
        Inspects recent workspace events to determine if an ORBIS control-plane
        interaction is currently active at the head of the chronological event stream.

        If so, finds the start timestamp of this ORBIS interaction sequence and returns
        the timestamp of the immediately preceding user workspace event (or the start of the ORBIS span)
        as the natural continuity cutoff.

        Returns None if no active ORBIS control-plane interaction is detected at the stream head.
        """
        from core.continuity.extractor import is_orbis_control_plane

        recent_events = self.db.get_workspace_events(limit=50)
        if not recent_events:
            return None

        sorted_events = sorted(recent_events, key=lambda e: (e.timestamp, e.event_id))
        if not sorted_events:
            return None

        # Check from the newest event backwards
        newest = sorted_events[-1]
        newest_exe = (
            newest.metadata.get("executable_path")
            if isinstance(newest.metadata, dict)
            else None
        )
        if not is_orbis_control_plane(newest.process_name, newest.window_title, newest_exe):
            return None

        # Scan backwards across the contiguous ORBIS interaction cluster
        last_user_event_time: Optional[datetime] = None
        orbis_cluster_start_time = newest.timestamp

        for e in reversed(sorted_events):
            e_exe = (
                e.metadata.get("executable_path")
                if isinstance(e.metadata, dict)
                else None
            )
            if is_orbis_control_plane(e.process_name, e.window_title, e_exe):
                orbis_cluster_start_time = e.timestamp
            else:
                last_user_event_time = e.timestamp
                break

        return last_user_event_time or orbis_cluster_start_time

    def _is_pure_control_plane_session(self, session: Session) -> bool:
        """
        Checks whether a session consists purely of ORBIS assistant control-plane activity.
        """
        if session.session_metadata and session.session_metadata.get("is_control_plane"):
            return True

        if not session.applications:
            return False

        # If all applications are electron/orbis
        if all(
            a.lower() in ("electron.exe", "electron", "orbis.exe", "orbis")
            for a in session.applications
        ):
            # Check if there are any non-control-plane resources or projects
            if session.resources:
                return False
            if session.session_metadata and session.session_metadata.get("projects"):
                return False
            return True

        return False

    def _filter_and_rank_workspace_sessions(
        self,
        sessions: List[Session],
        cutoff_time: Optional[datetime],
        start_bound: Optional[datetime] = None,
        end_bound: Optional[datetime] = None,
    ) -> List[Session]:
        """
        Filters out sessions that occurred after the continuity cutoff or are purely
        ORBIS control-plane sessions, and ranks remaining sessions preferring genuine
        user workspace sessions (with projects/resources/user apps) over empty transient sessions.
        Strictly enforces start_bound and end_bound when requested (e.g. for 'yesterday').
        """
        valid_sessions: List[Session] = []
        c_time = None
        if cutoff_time is not None:
            c_time = cutoff_time if cutoff_time.tzinfo is not None else cutoff_time.replace(tzinfo=timezone.utc)

        s_bound = None
        if start_bound is not None:
            s_bound = start_bound if start_bound.tzinfo is not None else start_bound.replace(tzinfo=timezone.utc)

        e_bound = None
        if end_bound is not None:
            e_bound = end_bound if end_bound.tzinfo is not None else end_bound.replace(tzinfo=timezone.utc)

        for s in sessions:
            s_start = s.start_time if s.start_time.tzinfo is not None else s.start_time.replace(tzinfo=timezone.utc)
            s_end = s.end_time if s.end_time.tzinfo is not None else s.end_time.replace(tzinfo=timezone.utc)

            if c_time is not None and s_start > c_time:
                continue
            if s_bound is not None and s_end < s_bound:
                continue
            if e_bound is not None and s_start > e_bound:
                continue
            if self._is_pure_control_plane_session(s):
                continue
            valid_sessions.append(s)

        # Fallback to non-control plane sessions only if temporal bounds were NOT explicitly specified
        if not valid_sessions and s_bound is None and e_bound is None:
            valid_sessions = [s for s in sessions if not self._is_pure_control_plane_session(s)]
            if not valid_sessions:
                valid_sessions = list(sessions)

        def _workspace_priority(s: Session) -> tuple:
            has_resources = 1 if s.resources else 0
            has_projects = 1 if (s.session_metadata and s.session_metadata.get("projects")) else 0
            is_meaningful = 1 if (has_resources or has_projects or len(s.event_ids) >= 3 or s.duration_seconds >= 20.0) else 0
            s_end = s.end_time if s.end_time.tzinfo is not None else s.end_time.replace(tzinfo=timezone.utc)
            return (is_meaningful, s_end.timestamp())

        return sorted(valid_sessions, key=_workspace_priority, reverse=True)

    def _enrich_session(
        self, session: Session, vector_similarity: Optional[float]
    ) -> RetrievedContextEvidence:
        """
        Enriches a raw Session with ContextDNA, Knowledge Graph connections,
        and genuine provenance IDs.
        """
        # Strip ORBIS control plane from workspace applications list
        filtered_apps = []
        is_cp_session = bool(session.session_metadata and session.session_metadata.get("is_control_plane"))
        for a in session.applications:
            if a.lower() in ("electron.exe", "electron", "orbis.exe", "orbis"):
                if len(session.applications) > 1 or is_cp_session:
                    continue
            filtered_apps.append(a)

        apps = filtered_apps if filtered_apps else list(session.applications)

        # Context DNA lookup
        dna = self.db.get_context_dna(session.session_id)
        primary_app: Optional[str] = None
        if dna and dna.application_context:
            primary_app = dna.application_context.get("primary_application")
            if primary_app and primary_app.lower() in ("electron.exe", "electron", "orbis.exe", "orbis") and apps and apps[0].lower() not in ("electron.exe", "electron", "orbis.exe", "orbis"):
                primary_app = apps[0]

        from core.continuity.extractor import WorkspaceResourceExtractor, is_protected_resource
        resources = [r for r in session.resources if not is_protected_resource(r)]
        projects = list(session.session_metadata.get("projects", [])) if session.session_metadata else []
        if dna and dna.resource_context:
            for p in dna.resource_context.get("projects", []):
                if p and p not in projects:
                    projects.append(p)
            for r in dna.resource_context.get("observed_resource_paths", []):
                if r and not is_protected_resource(r) and r not in resources:
                    resources.append(r)

        # Backfill from DNA ordered_event_sequence or database events for historical sessions
        if not resources or not projects:
            if dna and dna.activity_context and dna.activity_context.get("ordered_event_sequence"):
                for ev_dict in dna.activity_context["ordered_event_sequence"]:
                    ev = WorkspaceResourceExtractor.extract(
                        ev_dict.get("process_name"), ev_dict.get("window_title")
                    )
                    if not ev.is_protected and not is_protected_resource(ev.name):
                        if ev.name and ev.name not in resources:
                            resources.append(ev.name)
                    if ev.project and ev.project not in projects:
                        projects.append(ev.project)
            elif session.event_ids:
                try:
                    raw_events = self.db.get_workspace_events(limit=500)
                    sess_eids = set(session.event_ids)
                    for e in raw_events:
                        if e.event_id in sess_eids:
                            ev = WorkspaceResourceExtractor.extract(e.process_name, e.window_title)
                            if not ev.is_protected and not is_protected_resource(ev.name):
                                if ev.name and ev.name not in resources:
                                    resources.append(ev.name)
                            if ev.project and ev.project not in projects:
                                projects.append(ev.project)
                except Exception:
                    pass

        # Final protection sweep
        resources = [r for r in resources if not is_protected_resource(r)]

        # Knowledge Graph enrichment
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
                    if not is_protected_resource(r) and r not in resources:
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
            projects=projects,
            event_count=session.event_count,
            vector_similarity=vector_similarity,
            provenance=provenance,
        )
