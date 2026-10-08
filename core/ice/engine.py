"""
Intent Continuity Engine (ICE) Facade for ORBIS Phase 9.
Coordinates evidence extraction, multi-signal scoring, task state classification,
and explainable intent synthesis into a coherent ICEResult.
"""

from datetime import datetime, timezone
import logging
from typing import TYPE_CHECKING, Any, List, Optional, Union

if TYPE_CHECKING:
    from core.agent.llm.base import BaseLLMProvider
from core.continuity.retriever import ContinuityRetriever
from core.ice.classifier import TaskStateClassifier
from core.ice.extractor import IntentEvidenceExtractor
from core.ice.models import (
    ICEQuery,
    ICEResult,
    IntentCandidate,
    TaskContinuityState,
)
from core.ice.scorer import ContinuityScorer
from core.ice.synthesizer import IntentSynthesizer
from core.memory.db import DatabaseManager
from core.memory.graph.graph import KnowledgeGraph
from core.memory.vector.memory import SemanticMemory

logger = logging.getLogger("orbis.ice.engine")


class IntentContinuityEngine:
    """
    Main orchestration engine for ORBIS Phase 9 Intent Continuity.
    Determines candidate goals, unfinished work, task states, and candidate next actions
    grounded in observable workspace evidence.
    """

    def __init__(
        self,
        db: DatabaseManager,
        retriever: Optional[ContinuityRetriever] = None,
        graph: Optional[KnowledgeGraph] = None,
        vector_memory: Optional[SemanticMemory] = None,
        llm_provider: Optional["BaseLLMProvider"] = None,
        scorer: Optional[ContinuityScorer] = None,
        classifier: Optional[TaskStateClassifier] = None,
        synthesizer: Optional[IntentSynthesizer] = None,
    ) -> None:
        self.db = db
        self.graph = graph
        self.vector_memory = vector_memory
        self.llm_provider = llm_provider

        self.retriever = retriever or ContinuityRetriever(
            db=db, graph=graph, vector_memory=vector_memory
        )
        self.extractor = IntentEvidenceExtractor(
            db=db, retriever=self.retriever, graph=graph, vector_memory=vector_memory
        )
        self.scorer = scorer or ContinuityScorer()
        self.classifier = classifier or TaskStateClassifier()
        self.synthesizer = synthesizer or IntentSynthesizer(
            llm_provider=llm_provider, scorer=self.scorer, classifier=self.classifier
        )

    async def infer_intent(
        self,
        query: Union[str, ICEQuery],
        active_resources: Optional[List[str]] = None,
        active_apps: Optional[List[str]] = None,
    ) -> ICEResult:
        """
        Executes end-to-end intent inference for a user query.
        """
        # 1. Structure the query
        if isinstance(query, str):
            clean_text = query.strip()
            structured_query = ICEQuery(query_text=clean_text)
        else:
            structured_query = query

        if not structured_query.query_text:
            return ICEResult(
                query="",
                has_sufficient_evidence=False,
                primary_intent=None,
                alternative_intents=[],
                explainability_summary="Empty query provided. Context is unavailable.",
                model_used="deterministic_rule_engine",
            )

        # 2. Extract multi-modal factual session evidence
        session_contexts = self.extractor.extract_evidence(structured_query)

        if not session_contexts:
            return ICEResult(
                query=structured_query.query_text,
                has_sufficient_evidence=False,
                primary_intent=None,
                alternative_intents=[],
                explainability_summary=(
                    "No recorded workspace activity or sessions found in ORBIS memory. "
                    "Evidence is insufficient to infer intent or next actions."
                ),
                model_used="deterministic_rule_engine",
            )

        # 3. Synthesize candidate intents for each extracted session
        candidates: List[IntentCandidate] = []
        act_res = active_resources or []
        act_apps = active_apps or []

        for ctx in session_contexts:
            cand = await self.synthesizer.synthesize(
                ctx=ctx,
                active_resources=act_res,
                active_apps=act_apps,
                user_prompt=structured_query.query_text,
            )
            candidates.append(cand)

        # Filter out completely UNKNOWN candidates if we have at least one informative candidate
        informative = [c for c in candidates if c.task_state != TaskContinuityState.UNKNOWN]
        eval_candidates = informative if informative else candidates

        if not eval_candidates or (len(eval_candidates) == 1 and eval_candidates[0].task_state == TaskContinuityState.UNKNOWN):
            first = candidates[0] if candidates else None
            return ICEResult(
                query=structured_query.query_text,
                has_sufficient_evidence=False,
                primary_intent=first,
                alternative_intents=[],
                explainability_summary=(
                    "Recorded sessions contain sparse or ambiguous activity events. "
                    "Evidence is insufficient to establish a confident intent hypothesis."
                ),
                model_used="deterministic_rule_engine",
            )

        # 4. Deterministic Tie-Breaking & Ranking
        def _sort_key(c: IntentCandidate):
            recency = c.score_breakdown.get("recency", 0.0)
            resource = c.score_breakdown.get("resource", 0.0)
            # Find associated session id from provenance
            sess_id = c.provenance.session_ids[0] if c.provenance.session_ids else ""
            return (c.continuity_score, recency, resource, sess_id)

        sorted_candidates = sorted(eval_candidates, key=_sort_key, reverse=True)

        primary = sorted_candidates[0]
        alternatives = sorted_candidates[1 : structured_query.max_intents]

        # 5. Build concise structured explainability summary
        summary = self._build_explainability_summary(primary, alternatives)

        model_name = "llm_enriched" if self.llm_provider is not None else "deterministic_rule_engine"

        return ICEResult(
            query=structured_query.query_text,
            has_sufficient_evidence=True,
            primary_intent=primary,
            alternative_intents=alternatives,
            explainability_summary=summary,
            model_used=model_name,
            is_inferred=True,
        )

    @staticmethod
    def _build_explainability_summary(
        primary: IntentCandidate, alternatives: List[IntentCandidate]
    ) -> str:
        """Constructs a concise, structured evidence-based reasoning summary."""
        parts: List[str] = []
        parts.append(f"Inferred Goal: {primary.goal_description}")
        parts.append(f"Task State: {primary.task_state.value} ({primary.state_reasoning})")
        parts.append(f"Heuristic Continuity Score: {primary.continuity_score:.2f}")

        if primary.unfinished_work:
            unf_strs = [u.description for u in primary.unfinished_work[:2]]
            parts.append(f"Unfinished Work: {'; '.join(unf_strs)}")

        if primary.candidate_next_actions:
            next_act = primary.candidate_next_actions[0]
            parts.append(f"Recommended Next Action: [{next_act.action_type.value}] {next_act.title}")

        if alternatives:
            alt_descs = [f"'{a.goal_description}' (score: {a.continuity_score:.2f})" for a in alternatives[:2]]
            parts.append(f"Alternative Hypotheses: {'; '.join(alt_descs)}")

        return "\n".join(parts)
