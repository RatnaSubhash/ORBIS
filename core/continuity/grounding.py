"""
Grounded Response Builder for ORBIS Phase 5.
Constructs factual, explainable, deterministic natural language summaries
grounded strictly in observed workspace evidence, preserving complete provenance.
"""

from typing import List, Optional

from core.continuity.models import (
    ContinuityResponse,
    ProvenanceSource,
    RetrievedContextEvidence,
)


class GroundedResponseBuilder:
    """
    Deterministic response generator for continuity queries.
    Synthesizes factual evidence into natural language summaries
    without hallucinating actions, files, or user intentions.
    """

    @staticmethod
    def _merge_provenance(evidence_list: List[RetrievedContextEvidence]) -> ProvenanceSource:
        """
        Aggregates genuine provenance IDs across all evidence items.
        Preserves insertion order and avoids fabricating non-existent IDs.
        """
        session_ids: List[str] = []
        dna_ids: List[str] = []
        event_ids: List[str] = []
        graph_node_ids: List[str] = []
        vector_record_ids: List[str] = []

        for ev in evidence_list:
            prov = ev.provenance
            for sid in prov.session_ids:
                if sid and sid not in session_ids:
                    session_ids.append(sid)
            for did in prov.dna_ids:
                if did and did not in dna_ids:
                    dna_ids.append(did)
            for eid in prov.event_ids:
                if eid and eid not in event_ids:
                    event_ids.append(eid)
            for gnid in prov.graph_node_ids:
                if gnid and gnid not in graph_node_ids:
                    graph_node_ids.append(gnid)
            for vid in prov.vector_record_ids:
                if vid and vid not in vector_record_ids:
                    vector_record_ids.append(vid)

        return ProvenanceSource(
            session_ids=session_ids,
            dna_ids=dna_ids,
            event_ids=event_ids,
            graph_node_ids=graph_node_ids,
            vector_record_ids=vector_record_ids,
        )

    @staticmethod
    def _compute_confidence_heuristic(
        evidence_list: List[RetrievedContextEvidence],
    ) -> float:
        """
        Computes a deterministic evidence-quality / relevance heuristic in [0.0, 1.0].
        NOTE: This is an explainable heuristic, NOT a statistically calibrated probability.
        """
        if not evidence_list:
            return 0.0

        primary = evidence_list[0]
        score = 0.4  # Base score for having an observed session

        if primary.applications:
            score += 0.2
        if primary.resources:
            score += 0.2
        if primary.provenance.dna_ids or primary.provenance.graph_node_ids:
            score += 0.1
        if primary.event_count >= 3:
            score += 0.1

        score = min(1.0, max(0.0, score))

        # If semantic similarity is available on the primary match, blend it deterministically
        if primary.vector_similarity is not None:
            sim = max(0.0, min(1.0, primary.vector_similarity))
            score = round(score * 0.7 + sim * 0.3, 3)
        else:
            score = round(score, 3)

        return score

    def build_response(
        self,
        query: str,
        evidence_list: List[RetrievedContextEvidence],
    ) -> ContinuityResponse:
        """
        Produces a grounded ContinuityResponse from retrieved evidence.
        Fails safely with explicit explanation when context is unavailable or insufficient.
        """
        # Case 1: No evidence retrieved or empty list
        if not evidence_list:
            return ContinuityResponse(
                query=query,
                has_sufficient_context=False,
                grounded_summary=(
                    "No recorded workspace activity or sessions found. "
                    "Context is insufficient to determine what you were working on."
                ),
                active_applications=[],
                primary_resources=[],
                last_activity_timestamp=None,
                confidence_score=0.0,
                evidence=[],
                provenance=ProvenanceSource(),
            )

        primary = evidence_list[0]

        # Case 2: Session exists but has zero applications, resources, and events
        if not primary.applications and not primary.resources and primary.event_count == 0:
            merged_prov = self._merge_provenance(evidence_list)
            return ContinuityResponse(
                query=query,
                has_sufficient_context=False,
                grounded_summary=(
                    f"Session {primary.session_id[:8]} was recorded but contains no observable "
                    "applications, resources, or window events. Context is insufficient."
                ),
                active_applications=[],
                primary_resources=[],
                last_activity_timestamp=primary.end_time,
                confidence_score=0.1,
                evidence=evidence_list,
                provenance=merged_prov,
            )

        # Case 3: Grounded synthesis from observable evidence
        primary_app = primary.primary_application or (
            primary.applications[0] if primary.applications else "Unknown Application"
        )
        other_apps = [a for a in primary.applications if a != primary_app]

        # Format application segment
        if other_apps:
            apps_text = f"in {primary_app}, with related activity in {', '.join(other_apps)}"
        else:
            apps_text = f"in {primary_app}"

        # Format resource segment
        if primary.resources:
            sample_resources = primary.resources[:3]
            res_str = ", ".join(sample_resources)
            if len(primary.resources) > 3:
                res_str += f" and {len(primary.resources) - 3} other resources"
            res_text = f" Observed resources include: {res_str}."
        else:
            res_text = " No specific file or URL resources were captured."

        # Format temporal segment
        start_str = primary.start_time.strftime("%Y-%m-%d %H:%M:%S UTC")
        end_str = primary.end_time.strftime("%H:%M:%S UTC")
        duration_mins = primary.duration_seconds / 60.0

        if duration_mins >= 1.0:
            dur_text = f"duration ~{duration_mins:.1f} mins"
        else:
            dur_text = f"duration {primary.duration_seconds:.1f}s"

        time_text = (
            f" The most recent recorded activity was between {start_str} and {end_str} "
            f"({dur_text}, {primary.event_count} events)."
        )

        grounded_summary = f"You were working {apps_text}.{res_text}{time_text}"

        merged_prov = self._merge_provenance(evidence_list)
        confidence = self._compute_confidence_heuristic(evidence_list)

        return ContinuityResponse(
            query=query,
            has_sufficient_context=True,
            grounded_summary=grounded_summary,
            active_applications=primary.applications,
            primary_resources=primary.resources,
            last_activity_timestamp=primary.end_time,
            confidence_score=confidence,
            evidence=evidence_list,
            provenance=merged_prov,
        )
