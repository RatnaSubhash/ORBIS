# ORBIS Frontend Visual State System

## Overview

The ORBIS Visual State System connects real backend `AgentEvent` lifecycle milestones to semantic visual presentation states on top of the GlobePulse / COBE WebGL sphere.

The system is strictly presentation-only: it does **not** introduce a competing task lifecycle state machine and remains subordinate to the backend `AgentEvent` lifecycle authority.

---

## Architectural Hierarchy

```
Gateway (WebSocket /ws/tasks/{task_id})
  │
  ▼
OrbisCore.jsx ─── owns current visualState ('idle', 'thinking', ...),
  │               settleTimerRef, seenEventIdsRef, and active taskId
  │
  ├─── resolveVisualState() (visualState.js)
  │      - Task boundary isolation (drops mismatched task events)
  │      - Event-aware non-linear routing (approval hold/resume, cancellation)
  │      - Stale-event phase guard (prevents out-of-order backward regressions)
  │
  ▼
OrbisGlobe.jsx ── translates visualState to rotation speed (STATE_SPEEDS)
  │
  ▼
GlobePulse.jsx ── pure COBE WebGL renderer; speed modulated via ref (0 canvas recreations)
  │
  ▼
COBE (WebGL Canvas)
```

---

## The 9 Supported Visual States

| State | Status Label | Semantic Trigger | Globe Speed | Aura & CSS Presentation |
|---|---|---|---|---|
| **`idle`** | `Ready` | Base resting state | `0.003` (calm) | Subtle baseline cyan ambient radial glow (`rgba(51, 204, 221, 0.05)`). |
| **`listening`** | `Listening` | Voice input or mic active | `0.004` (attentive) | Soft rhythmic breathing pulse aura (`@keyframes orbis-listening-pulse`). |
| **`thinking`** | `Thinking` | `TASK_STARTED` / processing | `0.005` (elevated) | Subtle concentrated inward glow energy; faster rotation. |
| **`planning`** | `Planning` | `PLAN_CREATED` | `0.0035` (structured) | Delicate dashed orbital alignment ring around the globe stage. |
| **`executing`** | `Executing` | `STEP_STARTED` / tool activity | `0.006` (purposeful) | Active momentum with elevated radiance (`rgba(56, 189, 248, 0.18)`). |
| **`responding`** | `Responding` | `VERIFICATION_COMPLETED` / TTS | `0.003` (settling) | Calmer rotation with soft expanding harmonic breathing. |
| **`completed`** | `Completed` | `TASK_COMPLETED` | `0.002` (harmonic) | Harmonic emerald shimmer (`rgba(16, 185, 129, 0.2)`), settles to `idle`. |
| **`failed`** | `Failed` | `TASK_FAILED` / error | `0.001` (subdued) | Muted restrained amber-red aura (`rgba(239, 68, 68, 0.14)`), settles to `idle`. |
| **`approval`** | `Awaiting Approval` | `APPROVAL_REQUIRED` | `0.0` (frozen) | Globe freezes noticeably; warm amber aura (`rgba(245, 158, 11, 0.24)`). |

---

## Event-Aware Resolution & Guard Invariants

### 1. Event-Aware Non-Linear Routing (No Globally Rigid Ranking)
The task lifecycle is non-linear:
- `APPROVAL_REQUIRED` is an interrupt/hold state (`approval`).
- `APPROVAL_GRANTED` legitimately returns the state to `executing`.
- `TASK_CANCELLED` legitimately returns the state to `idle`.
- Voice `listening` can occur before task creation.
- `responding` and `completed` are distinct presentation states.

Transitions are resolved based on semantic intent rather than a rigid numeric inequality.

### 2. Stale-Event Protection Within the Same Task
Phase ordering (`thinking: 1` $\to$ `planning: 2` $\to$ `executing: 3` $\to$ `responding: 4` $\to$ `completed/failed: 5`) is used **strictly** to protect against clearly delayed or out-of-order events from regressing the active state:
- A delayed `TASK_STARTED` or `PLAN_CREATED` cannot downgrade an already `executing` or `responding` state.
- A delayed tool event cannot break an active `approval` hold.
- Any earlier event arriving after `completed` or `failed` is discarded as stale.

### 3. Task Boundary Isolation
Events carry `task_id`. If an event arrives with a `task_id` that does not match the active `currentTaskId` in `OrbisCore`, it is dropped immediately.

### 4. Task-Scoped Cancellable Settle Timers
When a task enters `completed` or `failed`, a settle timer schedules transition back to `idle`.
To prevent race conditions where a delayed settle callback from Task A forces Task B to `idle`:
1. Settle timers are explicitly cancelled whenever:
   - A new task starts (`TASK_STARTED` or submission).
   - The active task changes.
   - The component unmounts.
2. The settle callback checks that the task scheduled for settle still matches `activeTaskId` at runtime.
