/**
 * ORBIS Visual State System
 *
 * Presentation-state logic subordinate to backend AgentEvent lifecycle.
 * Translates real backend task lifecycle events and gateway milestones into
 * semantic visual states for the ORBIS Core globe and interface.
 *
 * Supported Visual States (9 total):
 *   1. idle        — Baseline calm presence (Ready)
 *   2. listening   — Voice input or prompt capture active
 *   3. thinking    — TASK_STARTED, agent reasoning/analyzing
 *   4. planning    — PLAN_CREATED, formulating structured execution plan
 *   5. executing   — STEP_STARTED / tool milestones / APPROVAL_GRANTED
 *   6. responding  — VERIFICATION_COMPLETED / presenting response
 *   7. completed   — TASK_COMPLETED, harmonic settle
 *   8. failed      — TASK_FAILED, restrained warning settle
 *   9. approval    — APPROVAL_REQUIRED, interrupt/hold state with frozen globe
 */

export const ORBIS_VISUAL_STATES = Object.freeze({
  IDLE: 'idle',
  LISTENING: 'listening',
  THINKING: 'thinking',
  PLANNING: 'planning',
  EXECUTING: 'executing',
  RESPONDING: 'responding',
  COMPLETED: 'completed',
  FAILED: 'failed',
  APPROVAL: 'approval',
});

export const STATUS_LABELS = Object.freeze({
  idle: 'Ready',
  listening: 'Listening',
  thinking: 'Thinking',
  planning: 'Planning',
  executing: 'Executing',
  responding: 'Responding',
  completed: 'Completed',
  failed: 'Failed',
  approval: 'Awaiting Approval',
});

export const STATE_SPEEDS = Object.freeze({
  idle: 0.003,
  listening: 0.004,
  thinking: 0.005,
  planning: 0.0035,
  executing: 0.006,
  responding: 0.003,
  completed: 0.002,
  failed: 0.001,
  approval: 0.0, // Frozen: visual cue for user hold
});

/**
 * Phase progression weights used strictly to reject stale/delayed earlier
 * lifecycle events within the same task.
 * Legitimate non-linear transitions (approval hold/resume, cancellations)
 * bypass phase checks via event-aware resolution.
 */
export const LIFECYCLE_PHASES = Object.freeze({
  idle: 0,
  listening: 0,
  thinking: 1,
  planning: 2,
  executing: 3,
  responding: 4,
  completed: 5,
  failed: 5,
});

/**
 * Standard candidate mappings for execution events.
 */
const EVENT_TO_CANDIDATE_STATE = Object.freeze({
  TASK_STARTED: 'thinking',
  PLAN_CREATED: 'planning',
  STEP_STARTED: 'executing',
  STEP_COMPLETED: 'executing',
  STEP_FAILED: 'executing',
  RESEARCH_STARTED: 'executing',
  SOURCE_FOUND: 'executing',
  SOURCE_READ: 'executing',
  CONTENT_GENERATION_STARTED: 'executing',
  DOCUMENT_UPDATED: 'executing',
  APPLICATION_OPENED: 'executing',
  SYSTEM_ACTION_STARTED: 'executing',
  VERIFICATION_STARTED: 'executing',
  VERIFICATION_COMPLETED: 'responding',
  APPROVAL_GRANTED: 'executing',
  APPROVAL_REQUIRED: 'approval',
  APPROVAL_DENIED: 'failed',
  TASK_COMPLETED: 'completed',
  TASK_FAILED: 'failed',
  TASK_CANCELLED: 'idle',
});

/**
 * Event-aware visual state resolver.
 *
 * Rules:
 *   - Task boundary isolation: Events with mismatched task_id are discarded.
 *   - New task detection: TASK_STARTED resets to 'thinking' and establishes new task.
 *   - Non-linear handling:
 *       APPROVAL_REQUIRED  -> 'approval' (interrupt/hold)
 *       APPROVAL_GRANTED   -> 'executing' (resumes execution)
 *       TASK_CANCELLED     -> 'idle' (clears task)
 *   - Settle indicators: 'completed' and 'failed' indicate needsSettle: true.
 *   - Stale-event protection: Within the same task, delayed lower-phase events
 *     (e.g., late TASK_STARTED after STEP_STARTED) are rejected.
 *
 * @param {string} currentVisualState Current visual state string
 * @param {object} event AgentEvent object { event_type, task_id, ... }
 * @param {string|null} currentTaskId Currently active task ID
 * @returns {{
 *   state: string,
 *   updated: boolean,
 *   taskId: string|null,
 *   isNewTask?: boolean,
 *   needsSettle?: boolean,
 *   reason?: string
 * }}
 */
export function resolveVisualState(currentVisualState, event, currentTaskId = null) {
  if (!event || typeof event !== 'object' || !event.event_type) {
    return { state: currentVisualState, updated: false, taskId: currentTaskId };
  }

  const eventType = event.event_type;
  const eventTaskId = event.task_id || event.taskId || null;

  // 1. New Task Detection
  if (eventType === 'TASK_STARTED') {
    return {
      state: 'thinking',
      updated: true,
      taskId: eventTaskId,
      isNewTask: true,
    };
  }

  // 2. Task Boundary Isolation
  // Reject events belonging to an earlier or foreign task
  if (eventTaskId && currentTaskId && eventTaskId !== currentTaskId) {
    return {
      state: currentVisualState,
      updated: false,
      taskId: currentTaskId,
      reason: 'task_boundary_mismatch',
    };
  }

  const effectiveTaskId = currentTaskId || eventTaskId;

  // 3. Terminal State Guard for the active task
  if (currentVisualState === 'completed' || currentVisualState === 'failed') {
    // Terminal state reached; earlier lifecycle events arriving late are discarded
    return {
      state: currentVisualState,
      updated: false,
      taskId: effectiveTaskId,
      reason: 'task_already_terminal',
    };
  }

  // 4. Approval Hold State Handling
  if (eventType === 'APPROVAL_REQUIRED') {
    return {
      state: 'approval',
      updated: true,
      taskId: effectiveTaskId,
    };
  }

  if (currentVisualState === 'approval') {
    if (eventType === 'APPROVAL_GRANTED') {
      return {
        state: 'executing',
        updated: true,
        taskId: effectiveTaskId,
      };
    }
    if (eventType === 'APPROVAL_DENIED') {
      return {
        state: 'failed',
        updated: true,
        taskId: effectiveTaskId,
        needsSettle: true,
      };
    }
    if (eventType === 'TASK_CANCELLED') {
      return {
        state: 'idle',
        updated: true,
        taskId: null,
      };
    }
    if (eventType === 'TASK_FAILED') {
      return {
        state: 'failed',
        updated: true,
        taskId: effectiveTaskId,
        needsSettle: true,
      };
    }
    if (eventType === 'TASK_COMPLETED') {
      return {
        state: 'completed',
        updated: true,
        taskId: effectiveTaskId,
        needsSettle: true,
      };
    }
    // Any other event during approval hold (e.g. late STEP_STARTED) cannot break the hold
    return {
      state: 'approval',
      updated: false,
      taskId: effectiveTaskId,
      reason: 'approval_hold',
    };
  }

  // 5. Explicit Event-Aware Direct Transitions
  if (eventType === 'APPROVAL_GRANTED') {
    return {
      state: 'executing',
      updated: true,
      taskId: effectiveTaskId,
    };
  }

  if (eventType === 'TASK_CANCELLED') {
    return {
      state: 'idle',
      updated: true,
      taskId: null,
    };
  }

  if (eventType === 'TASK_COMPLETED') {
    return {
      state: 'completed',
      updated: true,
      taskId: effectiveTaskId,
      needsSettle: true,
    };
  }

  if (eventType === 'TASK_FAILED') {
    return {
      state: 'failed',
      updated: true,
      taskId: effectiveTaskId,
      needsSettle: true,
    };
  }

  // 6. Candidate State & Phase Progression Check
  const candidateState = EVENT_TO_CANDIDATE_STATE[eventType];
  if (!candidateState) {
    return {
      state: currentVisualState,
      updated: false,
      taskId: effectiveTaskId,
      reason: 'unmapped_event_type',
    };
  }

  const currentPhase = LIFECYCLE_PHASES[currentVisualState] ?? 0;
  const candidatePhase = LIFECYCLE_PHASES[candidateState] ?? 0;

  // Stale-event protection: reject delayed earlier-phase events within the same task
  if (candidatePhase < currentPhase) {
    return {
      state: currentVisualState,
      updated: false,
      taskId: effectiveTaskId,
      reason: 'stale_earlier_phase',
    };
  }

  return {
    state: candidateState,
    updated: candidateState !== currentVisualState,
    taskId: effectiveTaskId,
  };
}

/**
 * Task-Scoped Cancellable Settle Timer
 *
 * Ensures settle transitions from 'completed' or 'failed' back to 'idle'
 * are strictly tied to the originating task and cancellable immediately
 * when a new task starts, active task changes, or component unmounts.
 */
export class TaskSettleTimer {
  /**
   * @param {{ onSettle: (taskId: string) => void, delay?: number }} options
   */
  constructor({ onSettle, delay = 1500 } = {}) {
    this.onSettle = onSettle;
    this.delay = delay;
    this.timerId = null;
    this.activeTaskId = null;
  }

  /**
   * Schedule a settle callback for a specific task.
   * Cancels any prior scheduled timer.
   * @param {string} taskId
   */
  schedule(taskId) {
    this.cancel();
    if (!taskId) return;
    this.activeTaskId = taskId;
    this.timerId = setTimeout(() => {
      // Race protection: only fire if the scheduled task is still the active timer task
      if (this.activeTaskId === taskId) {
        this.timerId = null;
        this.activeTaskId = null;
        if (typeof this.onSettle === 'function') {
          this.onSettle(taskId);
        }
      }
    }, this.delay);
  }

  /**
   * Cancel any pending settle timer immediately.
   */
  cancel() {
    if (this.timerId !== null) {
      clearTimeout(this.timerId);
      this.timerId = null;
    }
    this.activeTaskId = null;
  }

  /**
   * Check if a timer is currently pending for a given task ID.
   * @param {string} taskId
   * @returns {boolean}
   */
  isScheduledFor(taskId) {
    return this.timerId !== null && this.activeTaskId === taskId;
  }

  /**
   * Get the task ID currently scheduled for settle.
   * @returns {string|null}
   */
  getScheduledTaskId() {
    return this.activeTaskId;
  }
}
