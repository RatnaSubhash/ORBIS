# ORBIS Phase 12: Backend Event Architecture & Live Flow Telemetry

## 1. Architectural Overview

Phase 12 introduces a dedicated, asynchronous, decoupled event telemetry architecture designed to power the future **ORBIS Live Flow UI** (and external API consumers) while maintaining strict isolation from the agent execution path.

```
[ User Request ]
       │
       ▼
[ AgentOrchestrator ]
       │
       ├──► [TASK_STARTED] ───┐
       ▼                      │
[ HierarchicalPlanner ]       │
       │                      │
       ├──► [PLAN_CREATED] ───┼──► [ EventBus ] ──► [ Future Gateway ] ──► [ Future Live Flow UI ]
       ▼                      │    (Non-Blocking       (SSE / WS            (Read-Only Telemetry)
[ TaskExecutor ]              │     Async Pub/Sub)      Endpoints)
       │                      │
       ├──► [STEP_STARTED] ───┤
       ├──► [APPROVAL_REQUIRED]
       ├──► [VERIFY_STARTED] ─┤
       ├──► [VERIFY_COMPLETED]
       ├──► [STEP_COMPLETED] ─┤
       └──► [STEP_FAILED] ────┤
                              │
[ Final Synthesis ]           │
       ├──► [TASK_COMPLETED] ─┘
       └──► [TASK_FAILED]
```

---

## 2. Core Separation of Concerns

### Agent Execution vs. Agent Event Telemetry

| Dimension | Agent Execution Pipeline | Agent Event Telemetry |
| :--- | :--- | :--- |
| **Responsibility** | Task planning, tool execution, safety gates, verification, state mutation. | Real-time observation, user transparency, progress reporting, Live Flow telemetry. |
| **Execution Impact** | Authoritative: failure or approval halts the pipeline. | Passive: subscriber exceptions or latency **never** block or delay execution. |
| **Delivery Model** | Synchronous/awaited coroutine DAG in topological order. | Asynchronous dispatch via event loop background tasks without awaiting subscribers. |
| **Content Scope** | Rich tool parameters, full output dictionaries, disk paths, verification details. | Strictly sanitized user-facing messages and scrubbed metadata. |

---

## 3. Domain Model: `AgentEvent`

Defined in [`core/events/models.py`](file:///c:/Projects/ORBIS/core/events/models.py):

- **`event_id`** (`str`): Unique UUID4 identifier for the individual event.
- **`correlation_id`** (`str`): End-to-end request identifier linking the entire execution hierarchy (`correlation_id` $\rightarrow$ `task_id` $\rightarrow$ `step_id`).
- **`task_id`** (`str`): Execution context task ID.
- **`parent_task_id`** (`Optional[str]`): Parent task identifier for sub-plans or nested tasks.
- **`step_id`** (`Optional[str]`): Step identifier within a multi-step `TaskPlan`.
- **`timestamp`** (`datetime`): UTC timestamp of emission.
- **`event_type`** (`AgentEventType`): One of 20 standardized lifecycle event types.
- **`status`** (`str`): Execution state (`RUNNING`, `COMPLETED`, `FAILED`, `BLOCKED_PENDING_APPROVAL`, `CANCELLED`).
- **`safe_user_message`** (`str`): Human-readable, non-technical explanation sanitized for public view.
- **`tool_name`** (`Optional[str]`): Tool associated with the step.
- **`risk_level`** (`Optional[RiskLevel]`): Risk tier of the step (`LOW`, `MEDIUM`, `HIGH`).
- **`metadata`** (`Dict[str, Any]`): Sanitized metadata dictionary.

### Standardized `AgentEventType` Milestones
- **Task Lifecycle:** `TASK_STARTED`, `PLAN_CREATED`, `TASK_COMPLETED`, `TASK_FAILED`, `TASK_CANCELLED`
- **Step Lifecycle:** `STEP_STARTED`, `STEP_COMPLETED`, `STEP_FAILED`
- **Safety & Verification:** `APPROVAL_REQUIRED`, `APPROVAL_GRANTED`, `APPROVAL_DENIED`, `VERIFICATION_STARTED`, `VERIFICATION_COMPLETED`
- **Research & Content:** `RESEARCH_STARTED`, `SOURCE_FOUND`, `SOURCE_READ`, `CONTENT_GENERATION_STARTED`, `DOCUMENT_UPDATED`
- **System & Applications:** `APPLICATION_OPENED`, `SYSTEM_ACTION_STARTED`

---

## 4. Telemetry Safety & Sanitization

Implemented in [`core/events/sanitizer.py`](file:///c:/Projects/ORBIS/core/events/sanitizer.py):

Events represent public, user-visible telemetry. The `EventSanitizer` enforces four layers of defense:
1. **Authoritative Secret Redaction:** Leverages the existing ORBIS `redact_secrets()` engine to mask API keys, private keys, AWS tokens, GitHub PATs, and Bearer tokens.
2. **Assignment Masking:** Detects key-value credential assignments (e.g. `API_KEY=secret`, `password=123456`, `token: xyz`) and replaces secret values with `[REDACTED]`.
3. **Prohibited Key Dropping:** Automatically drops sensitive internal keys from metadata dictionaries:
   - `prompt`, `system_prompt`
   - `thought`, `reasoning`, `chain_of_thought`
   - `raw_html`, `html`, `raw_content`, `raw_output`
   - `env`, `dot_env`, `env_content`
   - `cookie`, `authorization`, `bearer`
4. **HTML Scrubbing & Length Boundaries:** Replaces raw HTML tags with placeholders and caps message and string lengths to prevent data flooding or DOM injection in the UI.

---

## 5. Non-Blocking Event Bus

Implemented in [`core/events/bus.py`](file:///c:/Projects/ORBIS/core/events/bus.py):

### Architectural Invariants:
1. **Non-Blocking Delivery:** `EventBus.publish()` and `publish_nowait()` schedule background subscriber tasks on the running `asyncio` event loop. The execution path returns immediately and **never** calls `asyncio.gather()` or waits for subscriber completion.
2. **Complete Exception Isolation:** Subscriber callback exceptions are trapped and logged. A crashing subscriber cannot impact task execution.
3. **Bounded Memory:**
   - Global event ring buffer capped at 1,000 events (`deque(maxlen=1000)`).
   - Per-task event history capped at 100 events per task, with a maximum of 200 active task buffers tracked.
4. **Historical Event Catch-Up:** Late-joining subscribers (e.g. Live Flow opened mid-task) can request historical task replay via `subscribe(..., replay_task_id=task_id)`.
5. **Clean Lifecycle Shutdown:** Calling `await bus.close()` cancels active subscriber tasks and cleanly transitions to a closed state where subsequent publications are safely dropped.

---

## 6. Integration Points

1. **[`TaskExecutor`](file:///c:/Projects/ORBIS/core/agent/executor/executor.py):**
   - Emits `STEP_STARTED` on transition to `RUNNING`.
   - Emits `APPROVAL_REQUIRED` when halted at a high-risk approval gate.
   - Emits `VERIFICATION_STARTED` and `VERIFICATION_COMPLETED` around `tool.verify()`.
   - Emits `STEP_COMPLETED` on successful execution and verification.
   - Emits `STEP_FAILED` on parameter errors or execution failure.
2. **[`AgentOrchestrator`](file:///c:/Projects/ORBIS/core/agent/orchestrator.py):**
   - Owns the `EventBus` instance (injectable or defaults to in-process instance).
   - Generates and propagates `correlation_id`.
   - Emits `TASK_STARTED` upon receiving a user request.
   - Emits `PLAN_CREATED` after hierarchical decomposition.
   - Emits `APPROVAL_GRANTED`, `APPROVAL_DENIED`, or `TASK_CANCELLED` during interactive confirmation turns.
   - Emits `RESEARCH_STARTED`, `SOURCE_FOUND`, `SOURCE_READ`, and `DOCUMENT_UPDATED` during research/content pipelines.
   - Emits `TASK_COMPLETED` or `TASK_FAILED` upon completion.
   - Preserves all legacy `StatusEvent` emissions via `add_event_listener()` for backwards compatibility.
