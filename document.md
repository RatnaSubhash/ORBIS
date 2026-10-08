# ORBIS: Autonomous Local-First Desktop Continuity & Workspace Intelligence
## Comprehensive Technical Architecture & Engineering Specification

---

## 1. Executive Summary & Vision

**ORBIS** is a local-first, privacy-preserving desktop intelligence and workspace continuity agent designed for Windows. Operating without reliance on remote cloud telemetry, ORBIS bridges the gap between passive user activity, semantic comprehension, and declarative workspace restoration.

### The Problem Space
Modern desktop knowledge work is fragmented across dozens of disconnected tools: browser tabs, text editors, terminals, project folders, and communication channels. When a workflow is interrupted by a reboot, system crash, or context switch, reconstructing the exact mental context and active window arrangement incurs substantial cognitive friction and downtime. Existing assistants either require intrusive full-screen video streaming to the cloud (creating severe privacy and latency concerns) or lack the low-level OS hooks needed to accurately restore window layouts, documents, and application states.

### The ORBIS Solution
ORBIS operates as a background observer and continuous intelligence plane:
1. **Passive Desktop Observation:** Non-intrusively samples active foreground windows, process identifiers, and window bounds using native Win32 APIs at a continuous 3-second heartbeat.
2. **Deterministic Privacy Sanitization:** Scrubs API keys, passwords, hashes, personal data, and internal OS tokens prior to local SQLite persistence.
3. **Temporal Sessionization:** Uses gap-based temporal clustering to segment continuous window switching into coherent, contiguous work sessions.
4. **Context DNA Synthesis:** Synthesizes raw sessions into explainable, structured "Context DNA" capturing reconstructed task intents, primary applications, touched resources, and focus intensity scores.
5. **Intent Continuity Engine (ICE):** Resolves ambiguous queries (e.g. *"Continue where I left off"*) using multi-dimensional evidence retrieval across temporal, lexical, and topological memory.
6. **Preflight-Validated Workspace Restoration:** Reconstructs application instances and places windows with pixel-exact multi-monitor geometry using isolated subprocesses, canonical executable allowlists, and Win32 layout management.

---

## 2. High-Level Architecture & System Flow

```mermaid
flowchart TB
    subgraph OS [Windows Desktop OS]
        W1[Active Application Windows]
        W2[File System & Projects]
        W3[Desktop Display Geometry]
    end

    subgraph ObsLayer [Passive Observation Layer (Phase 6)]
        WO[WindowsWorkspaceObserver<br/>Win32 user32.dll Hooks]
        ES[EventSanitizer<br/>Regex & Credential Redactor]
        WOS[WorkspaceObservationService<br/>Async 3s Heartbeat Loop]
    end

    subgraph Memory [Authoritative Memory Engine (Phase 2 & 9)]
        DB[(SQLite Persistent Store<br/>WAL Mode)]
        KG[Knowledge Graph Topology<br/>Nodes & Edges]
    end

    subgraph Continuity [Continuity & Intent Layer (Phases 7 & 8)]
        SB[IncrementalSessionBuilder<br/>Temporal Gap Clustering]
        DNA[ContextDNASynthesizer<br/>Intent & Focus Synthesis]
        ICE[IntentContinuityEngine<br/>Hybrid Evidence Retrieval]
    end

    subgraph Restoration [Restoration Engine (Phase 10)]
        REG[ApplicationRegistry<br/>Canonical Safe Binaries]
        VAL[PreflightValidator<br/>Containment & SSRF Gate]
        WM[WindowsWindowManager<br/>PID-Bound Layout API]
        ENG[RestorationEngine<br/>Isolated Subprocess Launcher]
    end

    subgraph Gateway [FastAPI Gateway (Phase 5)]
        REST[RESTful Endpoints]
        WS[WebSocket Live Telemetry]
    end

    subgraph UI [Frontend Electron Client]
        ELEC[Electron Shell / Preload Bridge]
        REACT[React Single-Page Dashboard]
        WSV[Workspace Continuity View]
        CMD[Command Interface]
        LFV[Live Flow Canvas]
    end

    W1 -->|Sample Focus & RECT| WO
    WO --> ES
    ES --> WOS
    WOS -->|Append Events| DB
    WOS -->|Stream Events| SB
    SB -->|Finalized Sessions| DB
    SB --> DNA
    DNA -->|Persist DNA| DB
    DB --> KG

    REST <--> DB
    REST <--> ICE
    REST <--> ENG
    WS <--> WOS

    ENG --> REG
    ENG --> VAL
    ENG --> WM
    WM -->|SetWindowPos & ShowWindow| OS

    ELEC <--> REST
    ELEC <--> WS
    REACT --> WSV
    REACT --> CMD
    REACT --> LFV
```

---

## 3. The 10 Engineering Phases in Depth

### Phase 1: Configuration, Settings & System Containment
- **Module:** `core/config.py`
- Centralized configuration built on `pydantic-settings` (`BaseSettings`).
- **Security Invariant:** All file targets and directory operations are bound to `allowed_workspace_root`. Traversal attacks (`../`) and Windows device paths (`CON`, `NUL`, `COM1`) are rejected at the settings boundary.
- **Key Parameters:**
  - `db_path`: Canonical path to `orbis.db`.
  - `observation_interval`: 3.0 seconds heartbeat.
  - `session_gap_minutes`: 5.0 minutes threshold for temporal session finalization.
  - `restoration_require_approval`: Strict policy gate requiring approval before restoring multiple concurrent applications.

### Phase 2: Authoritative Database Architecture
- **Module:** `core/memory/db.py`
- Pure local SQLite implementation running in **Write-Ahead Logging (WAL)** mode with `PRAGMA foreign_keys = ON` and `PRAGMA synchronous = NORMAL`.
- **Relational Schema:**
  - `workspace_events`: High-frequency audit trail storing `timestamp`, `process_name`, `window_title`, `executable_path`, and `window_bounds_json`.
  - `sessions`: Segmented temporal blocks storing `start_time`, `end_time`, `duration_seconds`, `event_count`, `applications_json`, `resources_json`, and `confidence`. Sorted descending (`ORDER BY start_time DESC`) to guarantee latest sessions appear first.
  - `context_dna`: High-level cognitive snapshots storing `synthesized_at`, `task_summary`, `focus_score`, `primary_application`, and raw serialization payload.
  - `knowledge_nodes` & `knowledge_edges`: Topological graph connecting applications, files, URLs, and sessions.
  - `tasks`: Agent orchestration history with status and execution logs.

### Phase 3: Real-Time Event Bus
- **Module:** `core/events/bus.py`
- Asynchronous, in-memory publisher/subscriber system with topic filtering and queue isolation.
- Dispatches continuous observation ticks, agent reasoning steps, and restoration state transitions to connected WebSockets without blocking the main event loop.

### Phase 4: Agent Core & Tool Execution Sandbox
- **Modules:** `core/agent/orchestrator.py`, `core/agent/llm/`
- Multi-provider LLM abstraction supporting Google Gemini, OpenAI, Anthropic, Ollama, and deterministic Mock providers.
- Coordinates multi-step reasoning cycles:
  1. User prompt reception and session context injection.
  2. Tool selection and JSON-schema argument validation.
  3. Execution with failure isolation and output streaming.

### Phase 5: Secure REST & WebSocket Gateway
- **Modules:** `core/gateway/app.py`, `core/gateway/routes.py`
- FastAPI framework served by `uvicorn`.
- Enforces strict CORS (`resolve_cors_origins`) forbidding unrestricted wildcard access.
- Implements an async lifespan context manager that starts the desktop observation daemon on boot and flushes pending sessions on shutdown.

### Phase 6: Passive Desktop Observation Engine
- **Modules:** `core/observation/windows.py`, `core/observation/observer.py`, `core/observation/sanitizer.py`
- **Native OS Hooks:** Uses Win32 ctypes calls:
  - `GetForegroundWindow()`: Retrieves active top-level HWND.
  - `GetWindowThreadProcessId(hwnd, byref(pid))`: Correlates window to PID.
  - `GetWindowTextW()`: Extracts native window title string.
  - `GetWindowRect()`: Samples exact pixel coordinates `(left, top, right, bottom)`.
- **System Process Exclusion:** Automatically filters out Windows internal host noise (`SearchHost.exe`, `ShellExperienceHost.exe`, `LockApp.exe`, `dwm.exe`).
- **Data Sanitization:** Regex-based scrubbers strip sensitive patterns (OAuth tokens, API keys, passwords, GUIDs, and file paths containing private user namespaces).

### Phase 7: Temporal Sessionization & Context DNA Synthesis
- **Modules:** `core/continuity/sessionizer.py`, `core/continuity/dna.py`
- **Temporal Clustering Algorithm:**
  1. Ingests events continuously. If the elapsed time between the current event and previous event exceeds `session_gap_seconds`, the active session is finalized and persisted.
  2. Computes aggregate application distribution and extracts candidate resources (file paths and URLs from window text).
- **Context DNA Synthesis Formula:**
  - Identifies the primary application by computing total dwell time.
  - Focus intensity score ($F$):
    $$F = \frac{\text{Dwell Time in Dominant App}}{\text{Total Session Duration}} \times \left(1.0 - \min\left(0.5, \frac{\text{App Switches}}{\text{Total Events}}\right)\right)$$
  - Generates deterministic task summaries (e.g. *"Active desktop development & research workflow"*).

### Phase 8: Intent Continuity Engine (ICE)
- **Module:** `core/ice/engine.py`
- Hybrid retrieval engine resolving conversational continuity queries.
- Combines lexical keyword matching with temporal proximity clustering to locate the exact session provenance needed to restore a user's past workspace.

### Phase 9: Semantic Knowledge Graph & Topology Engine
- **Module:** `core/continuity/topology.py`
- Represents workspace entities as a directional graph:
  - Nodes: Applications (`APP`), Files (`FILE`), URLs (`URL`), and Sessions (`SESSION`).
  - Edges: Relationships such as `ACCESSED`, `CONTAINS`, `CO_OCCURRED_WITH`.
- Computes node centrality to identify primary project resources and recurring application clusters.

### Phase 10: Declarative Workspace Restoration Engine
- **Modules:** `core/restoration/engine.py`, `core/restoration/registry.py`, `core/restoration/validator.py`, `core/restoration/window_manager.py`
- **The Application Registry:** Approved canonical binaries allowlist:
  - VS Code, Antigravity IDE, Google Chrome, Microsoft Edge, Windows Terminal, File Explorer, Notepad, Spotify, Snipping Tool.
  - Binaries are resolved strictly through verified canonical directory paths and Windows Registry App Paths. Ambient `PATH` lookups are forbidden.
- **Forced Window Launch Arguments:**
  - Browsers: Spawned with `--new-window <url>` (or `--new-window about:blank`) ensuring a visible new window opens even if Chrome/Edge is already running in the background.
  - Explorer: Spawned with the target workspace root directory to ensure a file explorer window appears.
  - IDEs: Spawned with project directory arguments.
- **Preflight Safety Validator:**
  - Validates path containment (rejects directory traversal `../`).
  - Enforces URL SSRF safety: strictly `http://` or `https://`; blocks prohibited schemes (`file:`, `javascript:`, `data:`).
  - Validates and clamps window layout coordinates within connected display monitors.
- **Deterministic Step Identifiers:**
  - Step IDs are formulated deterministically: `rstep_{session_prefix}_{step_index}_{app_identifier}`. Eliminates step ID desynchronization between plan preview and plan execution.
- **Isolated Subprocess Execution & PID-Bound Layout Placement:**
  - Launches each application with `shell=False` and `creationflags=CREATE_NEW_PROCESS_GROUP`.
  - Polls for the spawned process's top-level HWND.
  - Applies position and dimensions via `SetWindowPos` and activates the window using `ShowWindow(hwnd, SW_SHOWNORMAL)` and `SetForegroundWindow(hwnd)`.
  - Verifies process liveness via `psutil` with single-instance delegation fallbacks.

---

## 4. Security & Safety Model

| Threat Category | Mitigation Architecture | Implementation |
| :--- | :--- | :--- |
| **Command Injection** | Discrete argument arrays; `shell=False` strictly enforced across all process spawns. | `engine.py: _default_process_launcher` |
| **Arbitrary Binary Execution** | Closed allowlist in `ApplicationRegistry`; ambient PATH lookups strictly rejected. | `registry.py: resolve_trusted_executable` |
| **Directory Traversal** | Canonical path resolution; strict boundary checks against `allowed_workspace_root`. | `validator.py: _validate_local_path` |
| **SSRF & Malicious Schemes** | Strict `http`/`https` scheme check; URI scheme allowlist rejecting `file:`, `data:`, `javascript:`. | `validator.py: _validate_web_url` |
| **Sensitive Data Exposure** | In-flight event sanitizer scrubbing credentials, tokens, and private paths before SQLite insertion. | `sanitizer.py: EventSanitizer` |
| **Focus Hijacking & Glitches** | Windows placed using `SWP_NOZORDER` and bounds clamped to valid virtual desktop metrics. | `window_manager.py: set_window_bounds` |

---

## 5. REST & WebSocket API Specification

### 1. `GET /api/health`
- **Description:** Verifies service liveness and database connectivity.
- **Response:**
  ```json
  {
    "status": "healthy",
    "timestamp": "2026-09-25T00:28:51Z",
    "version": "1.0.0"
  }
  ```

### 2. `GET /api/continuity/context`
- **Description:** Retrieves active telemetry, observation status, latest Context DNA, recent sessions, and knowledge graph statistics.
- **Response Structure:**
  ```json
  {
    "observation": {
      "active": true,
      "polling_interval": 3.0,
      "total_events": 825
    },
    "recent_sessions": [
      {
        "session_id": "bbed87b791e749f798f55f61c22ea7ec",
        "start_time": "2026-09-24T18:07:19.245Z",
        "end_time": "2026-09-24T18:14:11.890Z",
        "duration_seconds": 412.6,
        "event_count": 34,
        "applications": ["Antigravity IDE.exe", "chrome.exe", "explorer.exe"],
        "resources": ["c:\\Projects\\ORBIS", "https://github.com"],
        "confidence": 1.0
      }
    ],
    "current_dna": {
      "version": "1.0",
      "task_summary": "Active desktop development & research workflow",
      "primary_application": "Antigravity IDE.exe",
      "active_applications": ["Antigravity IDE.exe", "chrome.exe"],
      "project_root": "ORBIS",
      "freshness": "persisted"
    },
    "knowledge_graph": {
      "total_nodes": 18,
      "total_edges": 24,
      "application_count": 5,
      "file_count": 12
    }
  }
  ```

### 3. `POST /api/restoration/preview`
- **Description:** Constructs a preflight-validated restoration plan for a session ID or natural language query.
- **Request:**
  ```json
  {
    "session_id": "bbed87b791e749f798f55f61c22ea7ec",
    "query": "Restore previous workspace"
  }
  ```
- **Response Structure:**
  ```json
  {
    "plan": {
      "plan_id": "rplan_a4f91b0c",
      "session_id": "bbed87b791e749f798f55f61c22ea7ec",
      "goal_description": "Restore previous workspace",
      "overall_risk": "LOW",
      "requires_user_approval": true,
      "is_preflight_valid": true,
      "steps": [
        {
          "step_id": "rstep_bbed87b7_0_antigravity",
          "app_identifier": "antigravity",
          "display_name": "Antigravity IDE",
          "command_executable": "C:\\Users\\kratn\\AppData\\Local\\Programs\\Antigravity IDE\\Antigravity IDE.exe",
          "status": "PENDING",
          "risk_level": "LOW"
        }
      ]
    },
    "message": "Plan created and preflight-validated successfully."
  }
  ```

### 4. `POST /api/restoration/execute`
- **Description:** Executes selected restoration steps with process spawning and window positioning.
- **Request:**
  ```json
  {
    "session_id": "bbed87b791e749f798f55f61c22ea7ec",
    "selected_step_ids": ["rstep_bbed87b7_0_antigravity", "rstep_bbed87b7_2_chrome"],
    "approved": true
  }
  ```
- **Response Structure:**
  ```json
  {
    "plan_id": "rplan_a4f91b0c",
    "success": true,
    "is_partial_restoration": false,
    "completed_steps": ["rstep_bbed87b7_0_antigravity", "rstep_bbed87b7_2_chrome"],
    "failed_steps": [],
    "skipped_steps": [],
    "summary": "Restoration completed successfully (2/2 steps).",
    "step_results": {
      "rstep_bbed87b7_0_antigravity": {
        "launched": true,
        "process_id": 36040,
        "window_placed": true,
        "execution_time_ms": 842.1
      }
    }
  }
  ```

---

## 6. Frontend Presentation Architecture

```
frontend/
├── electron/
│   ├── main.cjs               # Electron main entry; manages BrowserWindow & security
│   └── preload.cjs            # Context-isolated safe IPC bridge
└── src/
    ├── App.jsx                # Core shell; view router & awakening controller
    ├── state/
    │   └── AgentContext.jsx   # Global reactive store; 3s continuous polling heartbeat
    ├── components/
    │   ├── ui/                # Custom design system tokens
    │   │   ├── Dock.jsx       # Floating navigation dock
    │   │   ├── Folder.jsx     # Observed resources interactive folder
    │   │   └── AnimatedList.jsx # Smooth list transitions
    │   └── views/
    │       ├── WorkspaceView.jsx  # Unified Continuity & Restoration center
    │       ├── LiveFlowView.jsx   # Knowledge graph & tool execution canvas
    │       ├── CommandView.jsx    # Multi-modal conversation & task terminal
    │       └── ToolsView.jsx      # High-density tool capability catalog
    └── index.css              # Subdued, matte dark obsidian styling
```

### Aesthetic & UI Design System
- **Palette:** Matte obsidian (`#0b0f16` / `#0e131b`) with muted borders (`rgba(255, 255, 255, 0.07)`) and controlled cyan accents (`#33ccdd`). Eliminates over-bright neon glare and distracting specular reflections.
- **Clear Date & Timestamp Formatting:**
  - Session cards show explicit dates and durations: `Sep 25, 2026 • 12:19:45 AM – 12:26:07 AM (6m 22s)`.
  - Restoration header shows exact provenance date: `📅 Recorded: Sep 25, 2026 • 12:19:45 AM`.
  - Session Activity Log displays full date and seconds per event: `Sep 25, 2026 • 12:19:45 AM`.

---

## 7. Verification & Quality Assurance Runbook

### 1. Build Verification
```powershell
# Navigate to frontend directory and run production bundle build
cd c:\Projects\ORBIS\frontend
npm run build
```
*Expected Result:* Clean compilation of modules into `dist/` with zero CSS or JS syntax errors.

### 2. Backend Daemon Launch
```powershell
# Start Uvicorn gateway server on localhost:8000
cd c:\Projects\ORBIS
.\.venv\Scripts\uvicorn.exe core.gateway.app:create_app --factory --host 127.0.0.1 --port 8000 --reload
```
*Expected Result:* Uvicorn running on `http://127.0.0.1:8000`, application startup complete, background observation service started.

### 3. Automated End-to-End Test
```powershell
# Run automated verification in Electron
cd c:\Projects\ORBIS\frontend
npx electron scripts/verify_workspace_clean_ui.cjs
```
*Expected Result:*
- Successful awakening sequence.
- Navigation to Workspace view.
- Live telemetry verification: `Events: 825+`, `OBSERVING (3s heartbeat)`.
- Verified top session formatted with full date and time.
- Capture of verified screenshot to `scratch/clean_workspace_verified.png`.

---

## 8. Conclusion

ORBIS delivers a reliable, secure, local-first continuity agent. By combining Win32 passive observation, privacy-safe temporal sessionization, explainable Context DNA synthesis, and preflight-validated declarative window restoration, ORBIS eliminates cognitive switching friction while maintaining absolute user control and privacy.
