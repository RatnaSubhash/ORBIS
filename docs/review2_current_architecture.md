# ORBIS REVIEW-2: CURRENT RUNTIME ARCHITECTURE AUDIT

**Document Reference:** `docs/review2_current_architecture.md`  
**Target System:** ORBIS Runtime Architecture (v0.2.0)  
**Execution Environment:** Windows 11 Desktop (win32), Python 3.12.10, Node 20+, Electron 31.0.0, FastAPI 0.110.0  
**Audit Purpose:** Comprehensive component-by-component architectural blueprint detailing exact source modules, inputs, outputs, runtime contracts, and concrete evolution since Review-1.

---

## 1. High-Level Architecture Diagram

```mermaid
graph TB
    subgraph UI_Shell [Desktop Client Shell (Electron 31 + React 18 + Vite 5)]
        ELEC[Electron Main & Preload<br/><code>electron/main.cjs</code><br/><i>contextIsolation: true</i>]
        REACT[React Single-Page Dashboard<br/><code>frontend/src/App.jsx</code>]
        STATE[AgentContext State Engine<br/><code>src/state/AgentContext.jsx</code>]
        VIEWS[Specialized Views<br/>Command, Workspace, LiveFlow,<br/>Restoration, Tools, System, Settings]
    end

    subgraph Gateway [Local Service Boundary (FastAPI 0.110 + Uvicorn)]
        REST[REST API Endpoints<br/><code>core/gateway/routes.py</code>]
        WS[WebSocket Live Telemetry<br/><code>/ws/events</code>, <code>/ws/tasks/{id}</code>]
        TM[TaskManager<br/><code>core/gateway/task_manager.py</code>]
    end

    subgraph Agent_Core [Agent Orchestration & Planning Plane]
        ORCH[AgentOrchestrator<br/><code>core/agent/orchestrator.py</code>]
        PLAN[HierarchicalPlanner<br/><code>core/agent/planner/hierarchical.py</code>]
        CLASS[IntentClassifier<br/><code>core/agent/planner/classifier.py</code>]
        EXEC[TaskExecutor<br/><code>core/agent/executor/executor.py</code>]
        CONV[ConversationContextManager<br/><code>core/agent/conversation.py</code>]
        LLM[LLM Provider Factory<br/>Ollama Local / Gemini Cloud]
    end

    subgraph Memory_Continuity [Memory, Knowledge & Continuity Plane]
        OBS[Passive Win32 Observer<br/><code>core/observation/windows.py</code><br/><i>3s Heartbeat Loop</i>]
        SESS[IncrementalSessionBuilder<br/><code>core/continuity/sessionizer.py</code><br/><i>180s Temporal Gap</i>]
        DNA[ContextDNASynthesizer<br/><code>core/continuity/dna.py</code><br/><i>Focus Score Math</i>]
        ICE[Intent Continuity Engine<br/><code>core/ice/engine.py</code><br/><i>Next-Action Ranking</i>]
        RET[ContinuityRetriever<br/><code>core/continuity/retriever.py</code><br/><i>Hybrid Semantic Reranker</i>]
        KG[KnowledgeGraph<br/><code>core/memory/graph/</code><br/><i>Topological networkx</i>]
        VEC[Vector Memory<br/><code>core/memory/vector/</code><br/><i>FAISS + MiniLM Embeddings</i>]
        DB[(SQLite WAL Store<br/><code>orbis.db</code>)]
    end

    subgraph Safety_Restoration [Restoration & Policy Plane]
        VAL[PreflightValidator<br/><code>core/restoration/validator.py</code><br/><i>SSRF & Traversal Barrier</i>]
        REG[ApplicationRegistry<br/><code>core/restoration/registry.py</code><br/><i>Canonical Safe Binaries</i>]
        WM[WindowsWindowManager<br/><code>core/restoration/window_manager.py</code><br/><i>Win32 SetWindowPos</i>]
        RESTORE[RestorationEngine<br/><code>core/restoration/engine.py</code><br/><i>8-Stage Lifecycle</i>]
    end

    subgraph Tool_Ecosystem [Tool Registry & OS Execution (18+ Tools)]
        DOCS[DocumentTool<br/>DOCX, PPTX, PDF, XLSX, MD, TXT]
        SYS[SystemControlTool<br/>pycaw Master Volume & Mute]
        TERM[TerminalTool<br/>Sandboxed Shell (High Risk)]
        WEB[WebResearchTool<br/>Tavily / Gemini Search]
        APPS[AppLaunchTool<br/>Canonical Subprocess Launch]
    end

    %% Wiring
    ELEC --> REACT
    REACT --> STATE
    STATE --> VIEWS
    STATE <-->|HTTP REST| REST
    STATE <-->|WebSocket| WS

    REST <--> TM
    REST <--> ORCH
    WS <--> ORCH

    ORCH <--> PLAN
    ORCH <--> CLASS
    ORCH <--> CONV
    ORCH <--> LLM
    ORCH --> EXEC

    EXEC --> VAL
    VAL --> DOCS
    VAL --> SYS
    VAL --> TERM
    VAL --> WEB
    VAL --> APPS

    OBS -->|Sanitized Events| DB
    OBS --> SESS
    SESS -->|Completed Sessions| DB
    SESS --> DNA
    DNA -->|Context DNA| DB
    DB <--> KG
    DB <--> VEC
    DB <--> RET

    ORCH <--> ICE
    ORCH <--> RET
    ORCH <--> RESTORE

    RESTORE --> REG
    RESTORE --> VAL
    RESTORE --> WM
    WM -->|OS Win32 APIs| OBS
```

---

## 2. Component-by-Component Architectural Audit

### 1. Electron Desktop Shell
- **Name:** Electron Application Container
- **Actual File / Module:** `frontend/electron/main.cjs`, `frontend/electron/preload.cjs`
- **Responsibility:** Spawns native OS window, secures IPC boundary with `contextIsolation: true` and `nodeIntegration: false`, manages window minimizing/closing, and loads Vite renderer bundle.
- **Input:** Operating system lifecycle events, renderer IPC messages.
- **Output:** Chromium native desktop window rendering the React SPA.
- **Review-1 Equivalent:** Module M1 (Desktop UI — Electron + React or PySide6).
- **Change Since Review-1:** PySide6 was completely dropped in favor of Electron 31. Strict security sandboxing was implemented via a dedicated preload script exposing only authorized APIs to `window.electronAPI`.

### 2. React SPA Frontend
- **Name:** React Dashboard Renderer
- **Actual File / Module:** `frontend/src/App.jsx`, `frontend/src/main.jsx`
- **Responsibility:** Top-level visual rendering, route orchestration, global modal management, and layout structure adhering to the obsidian matte design system.
- **Input:** State updates from `AgentContext`.
- **Output:** Rendered DOM tree with smooth GSAP animations and responsive view transitions.
- **Review-1 Equivalent:** Module M1 (Desktop Interface).
- **Change Since Review-1:** Upgraded to React 18.3.1 with Vite 5 fast build tooling; integrated custom React Bits components (`OptionWheel`, `ElasticSlider`, `FluidGlass`).

### 3. Vite Build System & Dev Server
- **Name:** Vite Bundler & HMR Engine
- **Actual File / Module:** `frontend/vite.config.js`, `frontend/package.json`
- **Responsibility:** Local asset bundling, JSX transformation, strict port binding (port 5173), and hot module replacement.
- **Input:** Source JSX/CSS files, node_modules.
- **Output:** Optimized production bundle in `dist/` or local HMR web stream.
- **Review-1 Equivalent:** Conceptual build tool.
- **Change Since Review-1:** Configured with strict port binding (`--port 5173 --strictPort`) and multi-runtime scripts (`dev:renderer`, `dev:electron`).

### 4. FastAPI Gateway Service
- **Name:** Local API Gateway & Boundary
- **Actual File / Module:** `core/gateway/app.py`, `core/gateway/routes.py`
- **Responsibility:** Exposes RESTful endpoints for task execution, workspace context, tool inspection, and voice; hosts WebSocket pub-sub server for live streaming telemetry; injects dependencies.
- **Input:** HTTP requests from frontend (`http://127.0.0.1:8000`), WebSocket subscriptions.
- **Output:** JSON responses (`TaskStatusResponse`, `ContinuityContextResponse`), real-time event JSON stream.
- **Review-1 Equivalent:** Technology Mapping: FastAPI local service layer.
- **Change Since Review-1:** Fully realized with 1,151 lines of code; enforces schema validation (Pydantic v2), dependency injection, background observation lifecycle, and task management.

### 5. Agent Orchestrator
- **Name:** Autonomous Agent Orchestrator
- **Actual File / Module:** `core/agent/orchestrator.py` (61.8 KB)
- **Responsibility:** Coordinates execution lifecycle: classifies intent, generates DAG plans, enforces human approval gates for high-risk actions, dispatches tools, maintains conversational working memory, and returns grounded summaries.
- **Input:** User prompt string, execution parameters, task correlation ID.
- **Output:** `TaskExecutionResult`, live execution events emitted to `EventBus`.
- **Review-1 Equivalent:** Module M16 (Python orchestration layer).
- **Change Since Review-1:** Expanded into a full ReAct/DAG coordinator supporting streaming tokens, provider-agnostic LLMs, and multi-turn conversational context.

### 6. Hierarchical Planner
- **Name:** DAG Task Decomposition Planner
- **Actual File / Module:** `core/agent/planner/hierarchical.py` (81.8 KB)
- **Responsibility:** Analyzes user goals and decomposes complex multi-step tasks into directed acyclic graphs (DAGs) with explicit step dependencies, timeout configurations, and risk levels.
- **Input:** User prompt, capability class, registered tool schemas.
- **Output:** `HierarchicalPlan` containing ordered `PlanStep` nodes.
- **Review-1 Equivalent:** Conceptual multi-step planning.
- **Change Since Review-1:** Fully implemented with 81.8 KB of deterministic and LLM-assisted graph decomposition logic, supporting parallel and sequential execution steps.

### 7. Intent Classifier
- **Name:** Multi-Dimensional Intent Classifier
- **Actual File / Module:** `core/agent/planner/classifier.py` (31.8 KB)
- **Responsibility:** Fast heuristic and semantic classification of incoming user prompts into `CapabilityClass` (e.g. `WORKSPACE_CONTINUITY`, `WORKSPACE_RESTORATION`, `DOCUMENT_GENERATION`, `SYSTEM_CONTROL`, `CONVERSATION`).
- **Input:** Natural-language user prompt string.
- **Output:** `CapabilityClass` enum and extracted parameters.
- **Review-1 Equivalent:** Module M4 (Context Reconstruction Engine intent extraction).
- **Change Since Review-1:** Implements rigorous regex and keyword pattern hierarchies backed by embedding classification, enabling sub-millisecond intent routing.

### 8. Tool Registry
- **Name:** Central Tool Registry
- **Actual File / Module:** `core/tools/registry.py` (6.1 KB)
- **Responsibility:** Manages lifecycle of all agent tools, registers parameter schemas, maps tool names to instances, validates execution permissions, and generates OpenAPI-compliant tool catalogs.
- **Input:** Tool class definitions, database handle.
- **Output:** Registered tool map, tool catalog for UI explorer.
- **Review-1 Equivalent:** Module M15 (Desktop Automation tool interface).
- **Change Since Review-1:** Expanded from a simple launcher into an extensible registry supporting 18+ tools with typed Pydantic v2 schemas and risk levels.

### 9. Safety & Policy Layer
- **Name:** Security Guard & Preflight Validator
- **Actual File / Module:** `core/restoration/validator.py`, `core/tools/secret_redaction.py`, `core/events/sanitizer.py`
- **Responsibility:** Enforces path containment (`allowed_workspace_root`), blocks directory traversal (`..`), UNC paths, and SSRF attacks; redacts secrets (passwords, tokens, API keys) from logs and responses.
- **Input:** Raw file paths, URLs, command strings, observation metadata.
- **Output:** Sanitized strings or validation exceptions (`PathTraversalDenied`, `SSRFBlocked`).
- **Review-1 Equivalent:** Module M14 (Policy & Privacy Guard).
- **Change Since Review-1:** Implemented as active multi-layer middleware running across observation capture, database persistence, and tool invocation.

### 10. Task Executor
- **Name:** Sandboxed Tool Task Executor
- **Actual File / Module:** `core/agent/executor/executor.py` (23.1 KB)
- **Responsibility:** Executes individual plan steps in isolated asynchronous contexts, enforces 30-second execution timeouts, captures stderr/stdout, and triggers post-execution tool verification.
- **Input:** `PlanStep`, tool parameters, execution context.
- **Output:** `StepExecutionResult` containing `ToolResult` and `VerificationReport`.
- **Review-1 Equivalent:** Module M15 execution runner.
- **Change Since Review-1:** Implements strict verification gating where an unverified tool execution is flagged before plan completion.

### 11. Event Bus & WebSocket Subsystem
- **Name:** Asynchronous Event Bus
- **Actual File / Module:** `core/events/bus.py` (8.9 KB), `core/events/models.py`
- **Responsibility:** Pub-sub asynchronous event distributor; broadcasts observation events, plan step transitions, and token streams to in-process subscribers and WebSocket clients.
- **Input:** `AgentEvent` objects emitted by services or orchestrator.
- **Output:** Fan-out async dispatch to registered queues and connected browser sockets.
- **Review-1 Equivalent:** Conceptual event system.
- **Change Since Review-1:** Fully implemented with subscriber isolation (slow or failing subscribers never block pipeline execution) and replay buffering.

### 12. Workspace Observation Engine
- **Name:** Passive Win32 Desktop Observer
- **Actual File / Module:** `core/observation/windows.py`, `core/observation/service.py`
- **Responsibility:** Background daemon running an async 3-second heartbeat loop; queries Windows `user32.dll` for active foreground window, window title, process ID, and pixel coordinates.
- **Input:** Native OS window events via Win32 C-types.
- **Output:** Sanitized `WorkspaceEvent` objects logged to SQLite and emitted to session builder.
- **Review-1 Equivalent:** Module M2 (Observation Manager).
- **Change Since Review-1:** Native C-types Win32 bindings implemented; filters system noise; operates at <1% CPU overhead without continuous heavy OCR screen-scraping.

### 13. Temporal Sessionizer
- **Name:** Incremental Session Builder
- **Actual File / Module:** `core/continuity/sessionizer.py` (9.8 KB)
- **Responsibility:** Continuously groups raw observation events into coherent work sessions using 180s temporal gap clustering; finalizes sessions upon context switches.
- **Input:** Stream of `WorkspaceEvent` records.
- **Output:** `Session` domain objects persisted in SQLite `sessions` table.
- **Review-1 Equivalent:** Module M3 (Session Segmentation).
- **Change Since Review-1:** Fully operational incremental state machine logging live sessions into `orbis.db` (46 sessions currently recorded).

### 14. Context DNA Synthesizer
- **Name:** Context DNA Reconstruction Engine
- **Actual File / Module:** `core/continuity/dna.py` (11.6 KB), `core/continuity/models.py`
- **Responsibility:** Computes mathematical focus intensity scores ($\text{score} \in [0.0, 1.0]$) using dwell time saturation curves, extracts primary applications and files, and generates structured Context DNA records.
- **Input:** Finalized `Session` records and constituent event sequences.
- **Output:** `ContextDNA` records stored in SQLite `context_dna` table.
- **Review-1 Equivalent:** Module M4 (Context Reconstruction Engine).
- **Change Since Review-1:** Realized with concrete mathematical scoring formula ($\text{score} = \min(1.0, \frac{\text{dwell}}{600.0}) \times 0.6 + \text{app\_weight}$) and 45 live records stored.

### 15. Authoritative SQLite WAL Store
- **Name:** Database Manager
- **Actual File / Module:** `core/memory/db.py` (28.8 KB), `orbis.db`
- **Responsibility:** Local persistent storage for workspace events, sessions, Context DNA records, audit logs, and graph tables; runs in SQLite WAL mode for high concurrency.
- **Input:** Domain models, SQL queries with parameterized arguments.
- **Output:** Persisted relational records, query result lists.
- **Review-1 Equivalent:** Module M13 (Session Store).
- **Change Since Review-1:** Production SQLite engine configured with automated schema migrations, foreign keys, and indexes on temporal fields.

### 16. Workspace Knowledge Graph
- **Name:** Topological Knowledge Graph
- **Actual File / Module:** `core/memory/graph/builder.py`, `core/memory/graph/graph.py`, `core/continuity/topology.py`
- **Responsibility:** Builds an in-memory topological graph of entities (`Application`, `File`, `Session`, `Topic`) connected by typed relation edges (`CONTAINS`, `ACCESSED`, `CO_OCCURRED`); queried via networkx.
- **Input:** Session records, Context DNA entity lists.
- **Output:** Node-link graph structure, topological neighbor queries.
- **Review-1 Equivalent:** Module M5 (Workspace Knowledge Graph).
- **Change Since Review-1:** Implemented using `networkx 3.0` integrated with SQLite schema; heavy GNN embeddings deferred in favor of direct relational traversal.

### 17. FAISS Vector Index
- **Name:** Vector Indexer & Memory Store
- **Actual File / Module:** `core/memory/vector/indexer.py`, `core/memory/vector/memory.py`
- **Responsibility:** Fast approximate nearest-neighbor search over 384-dimensional dense vectors representing task summaries and resource context.
- **Input:** Dense embedding vectors from Sentence Transformers.
- **Output:** Top-k nearest candidate IDs with cosine similarity scores.
- **Review-1 Equivalent:** Module M7 (Vector Retrieval).
- **Change Since Review-1:** Integrated `faiss-cpu>=1.9.0` with metadata mapping back to SQLite primary keys.

### 18. Embedding Service
- **Name:** Sentence Transformers Embedding Service
- **Actual File / Module:** `core/memory/vector/embeddings/`
- **Responsibility:** Generates 384-dimensional dense semantic vectors from natural-language queries, task descriptions, and session summaries using `all-MiniLM-L6-v2`.
- **Input:** Text strings.
- **Output:** Normalized float32 numpy arrays.
- **Review-1 Equivalent:** Module M6 (Embedding Service).
- **Change Since Review-1:** Configured with local model caching to ensure zero external network calls during vector encoding.

### 19. Intent Continuity Engine (ICE)
- **Name:** Intent Continuity Engine
- **Actual File / Module:** `core/ice/engine.py` (7.7 KB), `core/ice/extractor.py`, `core/ice/scorer.py`
- **Responsibility:** Analyzes prior Context DNA records, identifies unfinished work (uncommitted edits, pending builds), scores candidate resumption actions, and generates explainable next-action recommendations.
- **Input:** Recent session history, unresolved task entities.
- **Output:** Ranked next-action recommendations with confidence scores.
- **Review-1 Equivalent:** Module M9 (Intent Continuity Engine).
- **Change Since Review-1:** Strategy 1 (heuristic and retrieval-based scoring) fully implemented with 20 unit tests; deep sequence modeling (Strategy 2) scheduled for future research.

### 20. Workspace Restoration Engine
- **Name:** Declarative Restoration Engine
- **Actual File / Module:** `core/restoration/engine.py` (29.8 KB), `core/restoration/validator.py`
- **Responsibility:** Orchestrates 8-stage restoration lifecycle: session selection, preflight validation, dry-run plan preview, allowlist verification, subprocess launching, and post-restoration verification.
- **Input:** Target session ID or natural-language restoration command.
- **Output:** `RestorationPlan` preview and `RestorationResult` execution report.
- **Review-1 Equivalent:** Module M16 (Workspace Restoration).
- **Change Since Review-1:** Comprehensive 29.8 KB implementation featuring preflight validation, single-instance browser window forcing (`--new-window`), and deterministic step tracking.

### 21. Windows Window Manager
- **Name:** Win32 Window Layout Manager
- **Actual File / Module:** `core/restoration/window_manager.py` (9.8 KB)
- **Responsibility:** Queries `EnumWindows` to map process IDs to top-level window handles; calls `SetWindowPos` and `ShowWindow` to restore multi-monitor pixel bounds $(x, y, w, h)$.
- **Input:** Target PID, recorded window bounds tuple.
- **Output:** Boolean status of window placement and verified window bounds.
- **Review-1 Equivalent:** Module M15 (Desktop Automation window operations).
- **Change Since Review-1:** Added PID-bound window handle resolution with fallback activation, enabling deterministic multi-monitor window arrangement.

### 22. Voice Subsystem (STT & TTS)
- **Name:** Local Speech Pipeline
- **Actual File / Module:** `core/voice/service.py` (13.4 KB), `core/voice/stt/`, `core/voice/tts/`
- **Responsibility:** Captures local microphone audio (`sounddevice`), transcribes audio on-device (`faster-whisper`), normalizes command text, and synthesizes spoken agent answers (`pyttsx3`/`edge-tts`).
- **Input:** Audio PCM stream from microphone, text string for spoken output.
- **Output:** Transcribed text command, spoken audio output.
- **Review-1 Equivalent:** Module M10 (Voice Module).
- **Change Since Review-1:** Transitioned from heavy OpenAI Whisper to `faster-whisper` for reduced latency; added local text-to-speech output with speech bound-safety.

### 23. Document Tool (Multi-Format Authoring)
- **Name:** Rich Document Generation Tool
- **Actual File / Module:** `core/tools/document_tool.py` (21.7 KB)
- **Responsibility:** Generates formatted files in 6 formats: DOCX (`python-docx`), PPTX (`python-pptx`), PDF (`reportlab`), XLSX (`openpyxl`), Markdown, and plain text; validates path containment and verifies files on disk.
- **Input:** `DocumentActionParameters` (file_path, content, format, overwrite).
- **Output:** Physical verified file written to authorized workspace root.
- **Review-1 Equivalent:** Expanded capability beyond Review-1 baseline.
- **Change Since Review-1:** Implemented complete native Python generation for Word, PowerPoint, PDF, and Excel documents with independent post-execution file verification.

### 24. System Control Tool (Audio & OS Telemetry)
- **Name:** Operating System Hardware Tool
- **Actual File / Module:** `core/tools/system_control_tool.py` (23.0 KB)
- **Responsibility:** Interfaces directly with Windows Core Audio via `pycaw` and COM interfaces to control master volume and mute state; inspects battery and network adapters.
- **Input:** Target volume percentage (0–100), mute boolean, or query flag.
- **Output:** Verified audio endpoint state, system hardware telemetry.
- **Review-1 Equivalent:** Expanded capability beyond Review-1 baseline.
- **Change Since Review-1:** Implemented native Windows audio endpoint integration with post-action verification and UI slider synchronization.

### 25. Frontend State & Context Layer
- **Name:** Reactive Agent Context
- **Actual File / Module:** `frontend/src/state/AgentContext.jsx` (23.5 KB)
- **Responsibility:** Central state management for the React application: manages WebSocket connection, syncs REST telemetry, handles optimistic task submission, tracks global volume, and triggers modals.
- **Input:** WebSocket events from Gateway, REST fetch responses, user UI actions.
- **Output:** React state hooks (`useAgent`) consumed by all views.
- **Review-1 Equivalent:** Frontend architecture layer.
- **Change Since Review-1:** Fully implemented single source of truth managing live flow tokens, pending approvals, and 3-second heartbeat telemetry synchronization.
