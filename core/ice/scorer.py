"""
Continuity Scorer for ORBIS Phase 9 Intent Continuity Engine (ICE).
Computes transparent, explainable heuristic continuity scores in [0.0, 1.0] across
multi-signal dimensions: temporal recency, resource overlap, normalized semantic similarity,
task state weighting, graph proximity, application continuity, and explicit prompt cues.
"""

from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Optional, Set, Tuple

from core.ice.models import TaskContinuityState


class ContinuityScorer:
    """
    Computes normalized heuristic continuity scores and performs deterministic tie-breaking.
    NOTE: All computed values are explainable heuristic scores in [0.0, 1.0],
    NOT empirically calibrated probabilities.
    """

    DEFAULT_WEIGHTS = {
        "recency": 0.25,
        "resource": 0.25,
        "semantic": 0.20,
        "state": 0.15,
        "graph": 0.10,
        "application": 0.05,
    }

    STATE_WEIGHTS = {
        TaskContinuityState.INTERRUPTED: 1.0,
        TaskContinuityState.ACTIVE: 0.9,
        TaskContinuityState.UNKNOWN: 0.4,
        TaskContinuityState.COMPLETED: 0.2,
        TaskContinuityState.ABANDONED: 0.1,
    }

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        half_life_hours: float = 24.0,
        explicit_cue_bonus: float = 0.15,
    ) -> None:
        self.weights = weights or dict(self.DEFAULT_WEIGHTS)
        self.half_life_hours = max(0.5, half_life_hours)
        self.explicit_cue_bonus = explicit_cue_bonus
        # Decay constant lambda = ln(2) / half_life
        self._decay_lambda = math.log(2.0) / self.half_life_hours

    @staticmethod
    def normalize_cosine_similarity(raw_cosine: Optional[float]) -> float:
        """
        Maps raw cosine similarity from [-1.0, 1.0] into [0.0, 1.0].
        Formula: max(0.0, min(1.0, (raw_cosine + 1.0) / 2.0))
        -1.0 (anti-correlated) -> 0.0
         0.0 (orthogonal)      -> 0.5
         1.0 (identical)       -> 1.0
        """
        if raw_cosine is None:
            return 0.5  # Neutral default for absent vector comparison
        clamped = max(-1.0, min(1.0, float(raw_cosine)))
        return round((clamped + 1.0) / 2.0, 4)

    def compute_recency_score(
        self, last_event_time: datetime, current_time: Optional[datetime] = None
    ) -> float:
        """
        Computes exponential temporal decay score based on elapsed hours.
        S_recency = e^(-lambda * delta_t_hours)
        """
        now = current_time or datetime.now(timezone.utc)
        if last_event_time.tzinfo is None:
            last_event_time = last_event_time.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        delta_seconds = max(0.0, (now - last_event_time).total_seconds())
        delta_hours = delta_seconds / 3600.0

        score = math.exp(-self._decay_lambda * delta_hours)
        return round(max(0.0, min(1.0, score)), 4)

    @staticmethod
    def compute_resource_overlap(
        candidate_resources: List[str], active_resources: List[str]
    ) -> float:
        """
        Computes Jaccard overlap between candidate resources and currently active resources.
        J(A, B) = |A cap B| / |A cup B|
        """
        if not candidate_resources and not active_resources:
            return 0.0

        def _norm_path(p: str) -> str:
            return p.replace("\\", "/").strip().lower()

        set_c: Set[str] = {_norm_path(r) for r in candidate_resources if r}
        set_a: Set[str] = {_norm_path(r) for r in active_resources if r}

        if not set_c or not set_a:
            return 0.0

        intersection = len(set_c.intersection(set_a))
        union = len(set_c.union(set_a))

        if union == 0:
            return 0.0

        return round(intersection / union, 4)

    @staticmethod
    def compute_application_overlap(
        candidate_apps: List[str], active_apps: List[str]
    ) -> float:
        """Computes overlap ratio of active application processes."""
        if not candidate_apps or not active_apps:
            return 0.0

        set_c = {a.lower().strip() for a in candidate_apps if a}
        set_a = {a.lower().strip() for a in active_apps if a}

        if not set_c or not set_a:
            return 0.0

        intersection = len(set_c.intersection(set_a))
        union = len(set_c.union(set_a))
        return round(intersection / union, 4) if union > 0 else 0.0

    def compute_state_score(self, task_state: TaskContinuityState) -> float:
        """Returns heuristic prioritization weight corresponding to the task state."""
        return self.STATE_WEIGHTS.get(task_state, 0.4)

    def check_explicit_prompt_cue(
        self, prompt: str, candidate_resources: List[str], candidate_apps: List[str]
    ) -> bool:
        """
        Determines if user's prompt explicitly mentions any resource filename,
        keyword, or application associated with this candidate.
        """
        if not prompt or not prompt.strip():
            return False

        lower_prompt = prompt.lower()
        for r in candidate_resources:
            norm_r = r.replace("\\", "/").lower()
            name = norm_r.split("/")[-1]
            if name and len(name) >= 3 and name in lower_prompt:
                return True
            stem = name.split(".")[0]
            if stem and len(stem) >= 3:
                if stem in lower_prompt:
                    return True
                spaced_stem = stem.replace("_", " ").replace("-", " ")
                if spaced_stem in lower_prompt:
                    return True
                # Also check words within stem if length >= 4
                for part in stem.replace("_", " ").replace("-", " ").split():
                    if len(part) >= 4 and part in lower_prompt:
                        return True

        for a in candidate_apps:
            clean_app = a.lower().replace(".exe", "").strip()
            if clean_app and len(clean_app) >= 3 and clean_app in lower_prompt:
                return True

        return False

    def score_candidate(
        self,
        last_event_time: datetime,
        task_state: TaskContinuityState,
        candidate_resources: List[str],
        active_resources: List[str],
        candidate_apps: List[str],
        active_apps: List[str],
        raw_cosine_similarity: Optional[float] = None,
        graph_proximity: float = 0.0,
        user_prompt: str = "",
        current_time: Optional[datetime] = None,
    ) -> Tuple[float, Dict[str, float]]:
        """
        Computes composite heuristic continuity score and detailed breakdown.
        """
        s_recency = self.compute_recency_score(last_event_time, current_time)
        s_resource = self.compute_resource_overlap(candidate_resources, active_resources)
        s_semantic = self.normalize_cosine_similarity(raw_cosine_similarity)
        s_state = self.compute_state_score(task_state)
        s_graph = max(0.0, min(1.0, float(graph_proximity)))
        s_app = self.compute_application_overlap(candidate_apps, active_apps)

        # Weighted sum
        composite = (
            self.weights["recency"] * s_recency
            + self.weights["resource"] * s_resource
            + self.weights["semantic"] * s_semantic
            + self.weights["state"] * s_state
            + self.weights["graph"] * s_graph
            + self.weights["application"] * s_app
        )

        # Explicit prompt cue bonus
        has_cue = self.check_explicit_prompt_cue(user_prompt, candidate_resources, candidate_apps)
        cue_bonus = self.explicit_cue_bonus if has_cue else 0.0
        final_score = min(1.0, composite + cue_bonus)
        final_score = round(max(0.0, final_score), 4)

        breakdown = {
            "recency": s_recency,
            "resource": s_resource,
            "semantic": s_semantic,
            "state": s_state,
            "graph": s_graph,
            "application": s_app,
            "cue_bonus": cue_bonus,
            "composite": final_score,
        }

        return final_score, breakdown

    @staticmethod
    def tie_break_key(candidate_tuple: Tuple[Any, float, Dict[str, float], str]) -> Tuple[float, float, float, str]:
        """
        Deterministic tie-breaking key for sorting candidate intents descending:
        1. Composite score (descending)
        2. Recency score (descending)
        3. Resource score (descending)
        4. session_id (ascending lexicographical)
        """
        _, score, breakdown, session_id = candidate_tuple
        return (
            score,
            breakdown.get("recency", 0.0),
            breakdown.get("resource", 0.0),
            # Invert session_id for descending sort so smaller session_id wins
            # Using tuple sorting with negation where appropriate
        )
