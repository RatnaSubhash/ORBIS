"""
Task State Classifier for ORBIS Phase 9 Intent Continuity Engine (ICE).
Evaluates a multi-signal evidence vector to determine task continuity states:
ACTIVE, INTERRUPTED, COMPLETED, ABANDONED, or UNKNOWN.

Adheres strictly to the principle that a Git commit is observed change evidence,
NOT automatic proof of overall goal completion.
"""

from datetime import datetime, timezone
from typing import List, Optional, Tuple

from core.ice.models import TaskContinuityState


class TaskStateClassifier:
    """
    Classifies the continuity state of a candidate task based on multi-signal evidence.
    """

    def __init__(
        self,
        inactivity_threshold_minutes: float = 15.0,
        abandoned_threshold_hours: float = 72.0,
    ) -> None:
        self.inactivity_threshold_minutes = inactivity_threshold_minutes
        self.abandoned_threshold_hours = abandoned_threshold_hours

    def classify(
        self,
        event_count: int,
        last_event_time: datetime,
        applications: List[str],
        resources: List[str],
        last_terminal_exit_code: Optional[int] = None,
        has_active_process: bool = False,
        has_uncommitted_modifications: bool = False,
        has_goal_completion_evidence: bool = False,
        has_git_commit: bool = False,
        subsequent_disjoint_sessions: int = 0,
        current_time: Optional[datetime] = None,
    ) -> Tuple[TaskContinuityState, str, float]:
        """
        Classifies task state and provides structured reasoning and heuristic confidence cap.

        Returns:
            (TaskContinuityState, state_reasoning, heuristic_confidence_cap)
        """
        now = current_time or datetime.now(timezone.utc)
        if last_event_time.tzinfo is None:
            last_event_time = last_event_time.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        delta_seconds = max(0.0, (now - last_event_time).total_seconds())
        delta_minutes = delta_seconds / 60.0
        delta_hours = delta_seconds / 3600.0

        # Signal 1: Sparse or empty session -> UNKNOWN
        if event_count < 2 or (not applications and not resources):
            return (
                TaskContinuityState.UNKNOWN,
                f"Session contains sparse evidence ({event_count} events, {len(applications)} apps, "
                f"{len(resources)} resources). Context is insufficient to infer intent.",
                0.3,
            )

        # Signal 2: Goal-Specific Completion Evidence -> COMPLETED
        # NOTE: has_git_commit alone is explicitly NOT sufficient for COMPLETED.
        if has_goal_completion_evidence:
            commit_note = " (Git commit also recorded)." if has_git_commit else "."
            return (
                TaskContinuityState.COMPLETED,
                f"Verified goal completion evidence observed: target objective verified and completed{commit_note}",
                0.85,  # Heuristic confidence capped at 0.85 to acknowledge epistemic limits
            )

        # Signal 3: Long inactivity with multiple subsequent disjoint sessions -> ABANDONED
        if delta_hours >= self.abandoned_threshold_hours and subsequent_disjoint_sessions >= 3:
            return (
                TaskContinuityState.ABANDONED,
                f"Inactivity of {delta_hours:.1f}h exceeds threshold ({self.abandoned_threshold_hours}h) "
                f"and was superseded by {subsequent_disjoint_sessions} subsequent disjoint sessions.",
                0.75,
            )

        # Signal 4: Failing terminal command on final action -> INTERRUPTED
        if last_terminal_exit_code is not None and last_terminal_exit_code != 0:
            return (
                TaskContinuityState.INTERRUPTED,
                f"Workflow halted after a failing command (terminal exit code {last_terminal_exit_code}). "
                "Task has unresolved errors.",
                0.85,
            )

        # Signal 5: Active process or very recent ongoing interaction -> ACTIVE
        is_recent = delta_minutes <= self.inactivity_threshold_minutes
        if (has_active_process or is_recent) and subsequent_disjoint_sessions == 0:
            reason = (
                "Related process is actively executing."
                if has_active_process
                else f"Recent activity observed ({delta_minutes:.1f}m ago <= {self.inactivity_threshold_minutes}m threshold)."
            )
            return (
                TaskContinuityState.ACTIVE,
                f"Task is actively engaged: {reason}",
                0.90,
            )

        # Signal 6: Git commit occurred without goal verification -> INTERRUPTED or ACTIVE
        if has_git_commit and not has_goal_completion_evidence:
            return (
                TaskContinuityState.INTERRUPTED,
                "Git commit was recorded, but overall task goal lacks completion verification "
                "(commit represents intermediate progress).",
                0.80,
            )

        # Signal 7: Uncommitted modifications or draft files without completion -> INTERRUPTED
        if has_uncommitted_modifications:
            return (
                TaskContinuityState.INTERRUPTED,
                f"Uncommitted or draft resource modifications detected without subsequent closure after {delta_minutes:.1f}m.",
                0.80,
            )

        # Signal 8: General paused session beyond inactivity threshold -> INTERRUPTED
        return (
            TaskContinuityState.INTERRUPTED,
            f"Session ended {delta_minutes:.1f}m ago without explicit completion milestone.",
            0.70,
        )
