# ORBIS Phase 12.8: FastAPI Gateway & Real-Time IPC Architecture

## 1. Overview & Architectural Topology

The ORBIS Gateway exposes the core agent brain (`AgentOrchestrator`) and real-time telemetry stream (`EventBus`) over local HTTP REST and WebSocket protocols. It serves as an adapter layer connecting headless agent execution to external desktop user interfaces while preserving all safety invariants, backpressure protections, and secret sanitization boundaries.

```
+-------------------------------------------------------------+
|               Future Desktop Client Layer                   |
|       (FUTURE: Electron Main / React / Live Flow UI)        |
+-------------------------------------------------------------+
               |                                ^
               | REST Requests                  | WebSocket Events
               | (POST /api/tasks,              | (/ws/tasks/{task_id})
               |  POST /api/tasks/.../approve)  |
               v                                |
+-------------------------------------------------------------+
|                  ORBIS FastAPI Gateway                      |
|                     (Phase 12.8)                            |
|  - TaskManager (lifecycle, background async tracking)       |
|  - REST Endpoints (/api/tasks, /api/health, ...)            |
|  - WebSocket Handler (bounded queues, slow-client safety)   |
|  - CORS Policy (strictly scoped localhost / Electron)       |
+-------------------------------------------------------------+
               |                                ^
               | run_task(prompt)               | subscribe()
               v                                |
+-------------------------------------------------------------+
|                    AgentOrchestrator                        |
|  - Intent classification & task coordination                |
|  - Pending approval state management                        |
+-------------------------------------------------------------+
               |                                |
               | execute(plan)                  | publish_nowait()
               v                                |
+-------------------------------------------------------------+
|                       TaskExecutor                          |
|  - Tool execution & independent verification                |
|  - Human-in-the-loop safety & approval gates                |
+-------------------------------------------------------------+
               |                                |
               | creates sanitized milestones   |
               v                                v
+-------------------------------------------------------------+
|                        EventBus                             |
|  - Non-blocking pub/sub dispatch                            |
|  - Bounded global & per-task history queues                 |
|  - Strict task isolation & subscriber exception isolation   |
+-------------------------------------------------------------+
```

> [!IMPORTANT]
> The Gateway is strictly an **adapter**, not a secondary execution engine. The frontend integration (Electron / React / Globe / OptionWheel) remains labeled as **FUTURE** and is not implemented in this phase.

---

## 2. API Endpoints Specification

### 2.1 Health Check
- **Endpoint**: `GET /api/health`
- **Description**: Verifies service responsiveness and version.
- **Response**: `200 OK`
  ```json
  {
    "status": "ok",
    "service": "orbis-gateway",
    "version": "0.2.0"
  }
  ```

### 2.2 Task Submission
- **Endpoint**: `POST /api/tasks`
- **Status Code**: `202 Accepted` (for asynchronous background execution) or `200 OK` (when `sync=true`)
- **Request Body (`TaskSubmitRequest`)**:
  ```json
  {
    "prompt": "Analyze network configuration and summarize findings",
    "correlation_id": "corr_optional_client_id",
    "metadata": { "client": "electron-ui" },
    "sync": false
  }
  ```
- **Response Body (`TaskSubmitResponse`)**:
  ```json
  {
    "task_id": "task_a1b2c3d4",
    "correlation_id": "corr_optional_client_id",
    "status": "ACCEPTED",
    "message": "Task accepted for execution."
  }
  ```

### 2.3 Task Status Retrieval
- **Endpoint**: `GET /api/tasks/{task_id}`
- **Description**: Retrieves user-safe operational status. Strictly excludes internal reasoning, hidden thoughts, prompts, raw tool dumps, and credentials.
- **Response Body (`TaskStatusResponse`)**:
  ```json
  {
    "task_id": "task_a1b2c3d4",
    "correlation_id": "corr_optional_client_id",
    "status": "BLOCKED_PENDING_APPROVAL",
    "summary": "Step 'step_create_document' (document) requires approval.",
    "pending_approval": {
      "task_id": "task_a1b2c3d4",
      "blocked_step_id": "step_create_document",
      "action_description": "Execution halted: file overwrite requires confirmation.",
      "tool_name": "document",
      "target_resource": "report.md",
      "expires_at": "2026-09-16T15:30:00Z"
    },
    "created_at": "2026-09-16T15:28:00Z",
    "updated_at": "2026-09-16T15:28:05Z",
    "error": null
  }
  ```

### 2.4 Historical Events Retrieval
- **Endpoint**: `GET /api/tasks/{task_id}/events`
- **Description**: Returns bounded historical events stored in the in-memory ring buffer for the requested task.
- **Response Body (`TaskEventsResponse`)**:
  ```json
  {
    "task_id": "task_a1b2c3d4",
    "events": [
      {
        "event_id": "evt_001",
        "correlation_id": "corr_optional_client_id",
        "task_id": "task_a1b2c3d4",
        "event_type": "TASK_STARTED",
        "status": "RUNNING",
        "safe_user_message": "Task started: Analyze network configuration...",
        "timestamp": "2026-09-16T15:28:01Z",
        "metadata": {}
      }
    ],
    "total": 1
  }
  ```

### 2.5 Action Approval / Continuation
- **Endpoint**: `POST /api/tasks/{task_id}/approve`
- **Description**: Confirms or denies an action blocked in `BLOCKED_PENDING_APPROVAL`.
- **Request Body (`ApprovalRequest`)**:
  ```json
  {
    "approved": true,
    "reason": "User confirmed overwrite of report.md"
  }
  ```
- **Response Body (`ApprovalResponse`)**:
  ```json
  {
    "task_id": "task_a1b2c3d4",
    "status": "RUNNING",
    "message": "Approval granted; task execution resumed."
  }
  ```
- **Telemetry Emitted**:
  - If `approved=true`: emits `APPROVAL_GRANTED`, updates blocked parameters (e.g. `overwrite=True`), and resumes execution.
  - If `approved=false`: emits `APPROVAL_DENIED` and `TASK_CANCELLED`, cancels execution safely, and leaves resources untouched.

---

## 3. Real-Time WebSocket IPC Protocol

- **Endpoint**: `/ws/tasks/{task_id}?replay=true`
- **Transport**: WebSocket over standard HTTP upgrade.
- **Message Format**: JSON-serialized `AgentEvent` objects.

### 3.1 Connection Protocol Flow
1. **Validation**: The gateway validates `{task_id}` syntax (`^[a-zA-Z0-9_\-]+$`).
2. **Acceptance**: Connection accepted (`await websocket.accept()`).
3. **Historical Replay**: If `replay=true` (default), bounded chronological events for that exact task are sent before live events.
4. **Subscription**: Subscribes to `EventBus` with task-specific filter (`filter_fn = lambda ev: ev.task_id == task_id`).
5. **Streaming**: As agent execution progresses, sanitized `AgentEvent` payloads are pushed immediately to the client.
6. **Disconnect / Unsubscribe**: On client disconnect or connection closure, the subscriber callback is removed immediately from `EventBus`.

### 3.2 Backpressure & Slow-Client Protection
A slow WebSocket client must never block agent task execution or accumulate unlimited memory.
- **Bounded Connection Queue**: Each active WebSocket holds a dedicated `asyncio.Queue` (default `maxsize=100`).
- **Overflow Policy (`close`)**: If a slow client fails to consume messages and its queue reaches capacity, the server initiates a controlled disconnect with WebSocket close code `1008 (Policy Violation)` and message `"Slow client disconnected to prevent memory accumulation"`.
- **Overflow Policy (`drop`)**: Alternative mode where the oldest queued event is evicted to make room for the latest milestone.

---

## 4. Security & Safety Invariants

1. **Task Isolation**: A client subscribed to `/ws/tasks/task_A` can never receive events from `task_B`. The `filter_fn` strictly isolates event routing.
2. **Authoritative Sanitization**: All events pass through `EventSanitizer.create_safe_event()` before reaching the gateway. Prohibited keys (`prompt`, `thought`, `reasoning`, `raw_html`, `env`) are eliminated, and sensitive credentials (`api_key`, `token`, `password`) are masked with `[REDACTED]`.
3. **Zero Secret Leakage in Errors**: All API exception handlers return standardized `ErrorResponse` objects with machine-readable error codes. Raw stack traces, server paths, and exception tracebacks are never exposed to clients.
4. **CORS Governance**: Narrowly scoped localhost development origins (`http://localhost:3000`, `http://localhost:5173`, `http://127.0.0.1:3000`, `http://127.0.0.1:5173`, `app://.`). Unrestricted wildcard (`*`) origins are forbidden.
5. **Authoritative Approval Gating**: Approval cannot be bypassed or fabricated. Any attempt to approve an unblocked task returns `409 Conflict`.
