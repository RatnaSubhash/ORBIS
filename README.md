# ORBIS

> **An Intelligent Context-Aware Desktop Agent for Workspace Continuity and Autonomous Computer Assistance**  
> *Target Platform: Windows 11 (Local-First, Privacy-Preserving, Extensible)*

---

## What ORBIS Is

ORBIS is an extensible desktop AI agent inspired by the vision of a proactive computer assistant. Unlike simple chatbots or reactive voice wrappers, ORBIS is designed around two interconnected dimensions:

1. **General Agent Capabilities**: Autonomous planning, tool-based computer interaction, research, document generation, and coding assistance.
2. **Workspace Continuity Intelligence (Primary Research Differentiator)**: Understanding what the user was working on, capturing structured **Context DNA**, maintaining a **Workspace Knowledge Graph**, and performing verifiable **Workspace Restoration**.

---

## Development Status & Phased Milestones

### Phase 1: Foundation + Tool Contract + Minimal Agent Shell (Verified)
- Core types (`RiskLevel`, `ToolResult`, `VerificationReport`, `StatusEvent`).
- SQLite DatabaseManager with WAL mode and migration tracking.
- Abstract `BaseTool` contract and in-memory `ToolRegistry`.
- `BaseLLMProvider`, `OllamaProvider`, and deterministic `MockLLMProvider`.
- Single-step `AgentOrchestrator` with safe status broadcasting.
- Demonstration `EchoTool`.

### Phase 2: Passive Workspace Observation + Base Desktop Tools (Verified)
- Passive Windows foreground window and process inspector (`WindowsWorkspaceObserver`).
- Duplicate event suppression in periodic polling.
- Privacy exclusion boundary: dropped or redacted sensitive windows; zero surveillance (no keystrokes, no passwords, no screen recordings).
- Read-only `DesktopTool` and `FileTool` with path containment enforcement.
- Migration 2: `workspace_events` table.

### Phase 3: Session Segmentation + Context DNA Engine (Verified)
- Temporal session segmentation with deterministic inactivity gap thresholding (`session_gap_minutes`).
- Strongly-typed `Session` model and explainable heuristic session-quality scoring.
- `ContextDNA` v1.0 across 6 factual dimensions (Temporal, Application, Resource, Activity, Privacy, Quality Indicators).
- Full bi-directional evidence traceability back to SQLite source `WorkspaceEvent` rows (`ORDER BY timestamp ASC, event_id ASC`).

### Phase 4: Knowledge Graph + Semantic Vector Memory (Current Phase)

> [!IMPORTANT]
> **Core Principle of Phase 4**: Phase 4 provides graph-based and semantic memory indexes. **Final hybrid retrieval and ranking are implemented in a later phase.**
> - **SQLite is authoritative**: NetworkX and FAISS serve as derived in-memory and on-disk query indexes that can be reconstructed from SQLite.
> - **Decoupled Architecture**: The Knowledge Graph (explicit observable relationships) and Semantic Vector Memory (dense cosine similarity retrieval) are built as separate, independently testable subsystems.
> - **100% Offline Testing**: The unit test suite relies on `MockEmbeddingProvider` to guarantee deterministic, offline execution with zero network calls.

#### What is Implemented in Phase 4:
- **Workspace Knowledge Graph (`core/memory/graph/`)**:
  - In-memory graph representation wrapping `networkx.DiGraph`.
  - Strongly typed node types (`NodeType`): `SESSION`, `APPLICATION`, `PROCESS`, `RESOURCE`, `WORKSPACE_EVENT`, `CONTEXT_DNA`.
  - Explicit relationship edge types (`EdgeType`):
    - `SESSION --contains--> WORKSPACE_EVENT`
    - `SESSION --uses--> APPLICATION`
    - `SESSION --observes--> RESOURCE`
    - `SESSION --has_context--> CONTEXT_DNA`
    - `WORKSPACE_EVENT --occurred_in--> SESSION`
    - `WORKSPACE_EVENT --occurred_in--> APPLICATION`
    - `WORKSPACE_EVENT --references--> RESOURCE`
  - `GraphBuilder`: Deterministically extracts nodes and edges from `Session`, `ContextDNA`, and `WorkspaceEvent` records using normalized, stable identifiers (`session:<id>`, `app:<name>`, `resource:<path>`, `event:<id>`, `dna:<id>`).
  - Topological query API: `get_node()`, `get_neighbors()`, `get_session_subgraph()`, `get_session_resources()`, `get_session_applications()`, `get_session_events()`, `get_session_context_dna()`.
  - Graph persistence: Migration 4 adds `graph_nodes` and `graph_edges` tables in SQLite; supports complete lossless graph reconstruction.
- **Semantic Vector Memory (`core/memory/vector/`)**:
  - `BaseEmbeddingProvider`: Abstract provider contract for embedding generation.
  - `MockEmbeddingProvider`: Deterministic, cryptographic hash-based embedding generator (default dimension 384) producing unit-normalized vectors for fast, offline unit testing and infrastructure verification.
  - `SentenceTransformerEmbeddingProvider`: Production provider wrapping local HuggingFace `SentenceTransformer` models with lazy model loading.
  - Embedding configuration: Configurable model name via `EMBEDDING_MODEL_NAME` (default: `"all-MiniLM-L6-v2"`).
  - Deterministic text representations: `format_session_text()` and `format_context_dna_text()` build explainable, factual context summaries without raw passwords or sensitive payloads.
  - `FaissIndexer`: Local dense index using `faiss.IndexFlatIP` on unit-normalized vectors for exact cosine similarity search. Maintains bidirectional integer-to-string ID mapping and performs parity validation upon disk reload.
  - `SemanticMemory`: Coordinates embedding, FAISS indexing, search (`top_k`), and SQLite metadata storage (`vector_records` table).
- **SQLite Extension (Migration 4)**:
  - Tables: `graph_nodes`, `graph_edges`, and `vector_records`.

### Phase 5: First Vertical-Slice MVP (Verified)
- Vertical continuity retrieval pipeline responding to "What was I working on?".
- `ContinuityRetriever` combining recency retrieval, semantic vector memory, knowledge graph traversal, and SQLite fallbacks.
- Deterministic heuristic confidence scoring and bidirectional evidence provenance.
- `ContinuityService` and `AgentOrchestrator.run_continuity()` integration.

### Phase 6: Multi-Step Agent Brain & Hierarchical Planner (Verified)
- Structured, validated hierarchical task execution (`TaskPlan`, `TaskStep`, `AgentExecutionResult`).
- `HierarchicalPlanner` providing deterministic offline DAG task decomposition by default, with optional LLM planning.
- `PlanValidator` verifying DAG acyclicity (DFS cycle detection), dependencies, parent-child hierarchies, bounds, and tool registration.
- `TaskExecutor` running topological steps with independent verification, retry limits, controlled replanning (`SKIPPED` superseded steps), and cascading failure blocking.
- Authoritative tool risk enforcement and backend approval gates for `HIGH` risk operations.

### Phase 7: Web Research & Controlled Terminal Automation (Verified)
- **Controlled Web Research (`WebResearchTool`)**:
  - `BaseSearchProvider` abstraction with `MockSearchProvider` (offline deterministic) and `ConfigurableSearchProvider` (extensible HTTP endpoint).
  - Strict SSRF prevention: restricts to `http` and `https`, rejects `localhost`, `127.0.0.1`, loopback, link-local metadata (`169.254.169.254`), private IP subnets, and strips credentials.
  - `SafeHTMLTextExtractor`: strips `<script>`, `<style>`, `<nav>`, `<footer>`, `<header>` and extracts bounded readable text.
  - **Critical Untrusted Data Boundary**: All external web data is tagged with `is_untrusted_external_data=True` and treated strictly as DATA, not instructions, to defend against indirect prompt injection.
- **Controlled Terminal Automation (`TerminalTool`)**:
  - Structured process execution without shell wrappers (strictly `shell=False` via `asyncio.create_subprocess_exec`).
  - Strict `CommandPolicy` allowlist categorizing commands into `LOW` (environment inspection), `MEDIUM` (build/test runs), `HIGH` (state-mutating/destructive operations), and `BLOCKED` (raw shells, unapproved utilities, metacharacters `;`, `&`, `|`, `>`, `<`).
  - Workspace root containment and path traversal (`..`) rejection.
  - Dynamic risk classification elevating HIGH-risk commands (`git clean`, `git commit`, `pip install`) to require explicit approval.
  - Output secret redaction (`redact_secrets`) masking API keys, GitHub tokens, AWS keys, and credentials.
- **Zero New Dependencies**: Implemented strictly with existing `httpx` and Python standard library.

### Phase 8: Content & Document Generation (Verified)
- **ContentGeneratorTool**: Pure text/document structuring and generation (Markdown and Plain Text) decoupled from filesystem persistence.
- **DocumentTool**: Controlled filesystem persistence with Windows path safety, traversal rejections, symlink/junction defenses, and dynamic overwrite approval gates.
- **Multi-Step Pipeline**: Full end-to-end integration: Research -> Content Generation -> Document Persistence.

### Phase 9: Intent Continuity Engine (ICE) (Verified)
- **Intent Inference & Continuity Projection**:
  - Shifts agent paradigm from descriptive retrospective retrieval ("What was I doing?") to normative intentionality and forward projection ("What was I trying to accomplish? What remains unfinished? What should I do next?").
  - `IntentContinuityEngine` (`core/ice/`): Central facade coordinating multi-modal evidence extraction, scoring, classification, and synthesis.
- **Multi-Signal Task State Classification**:
  - Deterministic evaluation of `ACTIVE`, `INTERRUPTED`, `COMPLETED`, `ABANDONED`, and `UNKNOWN`.
  - Goal-specific completion requirements (passing test suite for bugfixes; verified document creation for document goals; explicit user confirmation).
  - Strict invariant: Git commits represent change evidence, NOT proof of goal completion.
- **Explainable Heuristic Continuity Scoring**:
  - Transparent formula balancing recency (exponential half-life decay), resource overlap (Jaccard), normalized semantic similarity ($[-1, 1] \rightarrow [0, 1]$), task state weighting, graph proximity, application continuity, and prompt cues.
  - Formally labeled as explainable heuristic scores in $[0.0, 1.0]$, not calibrated probabilities (calibration deferred to Phase 15).
- **Candidate Next-Action Recommendations**:
  - Structured candidates (`NextActionCandidate`) with estimated risk, rationale, and supporting evidence IDs.
  - Zero autonomous execution: recommendations require explicit user submission to Phase 6 planner with authoritative risk/approval gates.
- **Authoritative Deterministic Baseline with Constrained LLM Enrichment**:
  - Deterministic evidence extraction, scoring, and state classification are authoritative.
  - Optional LLM strictly enriches natural language phrasing within `<untrusted_workspace_evidence>` safety delimiters.
  - Safe fallback on sparse or empty evidence (`has_sufficient_evidence=False`).
- **IntentContinuityTool & Orchestrator Helper**:
  - `IntentContinuityTool` registered as read-only (`RiskLevel.LOW`) tool.
  - `AgentOrchestrator.run_intent_continuity(query)` and `HierarchicalPlanner` intent routing.

### Phase 10: Multi-App & Window Layout Restoration (Verified)
- **Declarative Workspace Restoration Engine (`core/restoration/`)**:
  - Grounded in Phase 9 Intent Continuity Engine (ICE) session evidence.
  - Zero subprocess side-effects during planning; declarative `RestorationPlan` generation.
- **Trusted Application Registry (`ApplicationRegistry`)**:
  - Pre-approved desktop applications (`vscode`, `terminal`, `chrome`, `edge`, `notepad`).
  - Strict canonical installation path resolution; rejects ambient PATH lookups and arbitrary user-supplied binary paths.
  - Discrete argument lists (`shell=False`).
- **Comprehensive Preflight Boundary Validator (`PreflightValidator`)**:
  - Path traversal (`..`), UNC network path, and Windows reserved device name (`CON`, `NUL`, etc.) rejections.
  - Workspace root boundary containment enforcement and privacy policy exclusions.
  - Strict browser URI scheme allowlist: only `http:` and `https:`; rejects `file:`, `javascript:`, `chrome:`, `edge:`, etc.
  - Phase 7 SSRF protection enforcement blocking loopback and private/cloud-metadata IPs.
- **Multi-Monitor Geometry & Virtual Desktop Display Bounds**:
  - Supports Windows virtual desktop geometry spanning multiple monitors.
  - Permissive of valid negative coordinates (e.g., secondary monitor placed to the left or above the primary display).
  - Bounded clamping only when coordinates fall completely outside the virtual screen space.
- **PID-Bound Window Discovery & Isolated Manipulation (`BaseWindowManager`)**:
  - Uses Win32 `EnumWindows` + `GetWindowThreadProcessId` to match top-level HWNDs strictly to the target launched PID.
  - Never repositions, alters, or closes unrelated existing user windows.
  - Production `WindowsWindowManager` via `ctypes` and deterministic `MockWindowManager` for 100% offline testing.
- **Authoritative Risk vs. Approval Policy Separation**:
  - Pure local multi-app restoration maintains truthful intrinsic `RiskLevel.LOW`.
  - Enforces mandatory user confirmation by policy (`requires_user_approval=True`) without artificial risk inflation.
  - Isolated step execution: failures in one step do not abort independent safe steps; reports transparent partial restorations.
- **Agent Integration**:
  - `RestorationTool` exposing declarative plan creation and execution via `BaseTool` contract.
  - `AgentOrchestrator.run_restoration(query, execute, approval_callback)` helper.
  - `HierarchicalPlanner` automatic routing for workspace restoration intent prompts.

#### What is NOT Implemented Yet (Reserved for Future Phases):
- ❌ Voice Interaction & TTS (Phase 11)
- ❌ Desktop HUD & UI (Electron / React) (Phase 12)
- ❌ Model Context Protocol (MCP) Integration (Phase 13)
- ❌ Cryptographic Audit Chains (Phase 14)
- ❌ Statistical Calibration, ECE Evaluation & ORBIS-WCD Dataset (Phase 15)
- ❌ Pixel-perfect UI state restoration & arbitrary GUI automation
- ❌ Unrestricted Shell or Background Autonomy

---

## Setup & Environment

The project uses **Python 3.12** in the virtual environment at `C:\Projects\ORBIS\.venv`.

### 1. Configuration Settings

Key configuration options in `core/config.py`:
- `SEARCH_PROVIDER` (default: `mock`): Search provider mode (`mock` or `configurable`).
- `SEARCH_API_KEY`: Optional external search engine API key.
- `WEB_MAX_RESULTS` (default: `5`): Maximum search results to retrieve.
- `WEB_REQUEST_TIMEOUT` (default: `5.0`): HTTP request timeout in seconds.
- `TERMINAL_TIMEOUT_SECONDS` (default: `15.0`): Execution timeout for terminal commands.
- `TERMINAL_MAX_OUTPUT_BYTES` (default: `65536`): Maximum output capture size (64KB).
- `ALLOWED_WORKSPACE_ROOT` (default: `.`): Path boundary for safe `FileTool` and `TerminalTool` operations.
- `EMBEDDING_MODEL_NAME` (default: `all-MiniLM-L6-v2`): Local sentence-transformers model name.
- `VECTOR_INDEX_PATH` (default: `orbis_vectors.faiss`): Persistent FAISS index file path on disk.
- `SESSION_GAP_MINUTES` (default: `15.0`): Maximum minutes of inactivity before session demarcations.

---

## Running the Demonstrations

### Phase 1 Demo (Agent Shell Pipeline)
```powershell
.\.venv\Scripts\python.exe demo.py
```

### Phase 2 Demo (Observation, Deduplication, Privacy, Desktop & File Tools)
```powershell
.\.venv\Scripts\python.exe demo_phase2.py
```

### Phase 3 Demo (Session Segmentation + Context DNA Synthesis + Traceability)
```powershell
.\.venv\Scripts\python.exe demo_phase3.py
```

### Phase 4 Demo (Knowledge Graph + Semantic Vector Memory)
```powershell
.\.venv\Scripts\python.exe demo_phase4.py
```

### Phase 5 Demo (First Vertical-Slice MVP: 'What was I working on?')
```powershell
.\.venv\Scripts\python.exe demo_phase5.py
```

### Phase 6 Demo (Multi-Step Agent Brain & Hierarchical Planner)
```powershell
.\.venv\Scripts\python.exe demo_phase6.py
```

### Phase 7 Demo (Web Research & Controlled Terminal Automation)
```powershell
.\.venv\Scripts\python.exe demo_phase7.py
```

### Phase 8 Demo (Content & Document Generation)
```powershell
.\.venv\Scripts\python.exe demo_phase8.py
```

### Phase 9 Demo (Intent Continuity Engine)
```powershell
.\.venv\Scripts\python.exe demo_phase9.py
```

### Phase 10 Demo (Multi-App & Window Layout Restoration)
```powershell
.\.venv\Scripts\python.exe demo_phase10.py
```

---

## Running the Automated Test Suite

All 234 unit and integration tests run deterministically offline with zero external network calls:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

### Test Coverage Breakdown:
- `tests/test_restoration.py`: 24 tests covering Phase 10 restoration domain models, declarative plan generation (zero side-effects), ICE-guided session selection, competing stale vs active sessions, trusted canonical binary resolution, ambient PATH rejection, arbitrary executable rejection, unregistered application rejection, stale/missing local file preflight isolation, directory traversal path rejection, Windows reserved device names, UNC network path rejection, workspace containment boundary, privacy-excluded paths/titles, strict browser URL scheme validation (only http/https), browser SSRF protection, multi-monitor virtual desktop geometry with valid negative coordinates, PID-bound window discovery & unrelated window isolation, window layout modification, failure isolation across steps, transparent partial restoration reporting, risk vs approval policy separation, subprocess safety (shell=False) and credential exclusion, and Agent Orchestrator / RestorationTool integration.
- `tests/test_ice.py`: 20 tests covering Phase 9 ICE active task inference, interrupted task inference, goal-specific completion, Git commit WIP semantics, unknown state, competing intents, recency decay, normalized cosine similarity, graph evidence, Jaccard overlap, cue boost, evidence provenance, score bounds, tie-breaking, empty evidence fallback, LLM unavailable baseline, state preservation against LLM overrides, prompt injection defense, no autonomous execution, and IntentContinuityTool integration.
- `tests/test_content_generator.py`: 10 tests covering text generation, markdown formatting, prompt handling, grounding metadata, and character bounds.
- `tests/test_document_tool.py`: 10 tests covering DocumentTool file creation, plain text, markdown, traversal rejection, invalid extensions, overwrite safety gates, and physical existence checks.
- `tests/test_phase8_integration.py`: 6 tests covering end-to-end multi-step Research -> Content -> Document pipelines.
- `tests/test_web_research_tool.py`: 12 tests covering WebResearchTool, SafeHTMLTextExtractor, SSRF protection, URL validation, and secret masking.
- `tests/test_terminal_tool.py`: 14 tests covering TerminalTool command allowlisting, argument validation, timeout enforcement, output capping, and approval gates.
- `tests/test_phase7_integration.py`: 7 tests covering integrated web research and terminal automation.
- `tests/test_hierarchical_planner.py`: 10 tests covering deterministic planning templates, DAG step dependencies, and validation.
- `tests/test_task_executor.py`: 12 tests covering TaskExecutor step execution, DAG dependency resolution, approval gates, verification, and replanning.
- `tests/test_continuity_service.py`: 10 tests covering ContinuityService query routing and explainable summary generation.
- `tests/test_knowledge_graph.py`: 10 tests covering node/edge creation, duplicate prevention, GraphBuilder from Session/DNA/Events, session subgraph query, app/resource/event neighbors, SQLite persistence & reload parity, traceability, and malformed input handling.
- `tests/test_semantic_memory.py`: 9 tests covering BaseEmbeddingProvider contract, MockEmbeddingProvider determinism and normalization, text representation generation, FAISS add/search, cosine similarity interpretation (higher = more similar), empty index, invalid $k$, metadata mapping, save/reload consistency, and index/metadata mismatch detection.
- `tests/test_phase4_integration.py`: 2 tests covering end-to-end Session -> Context DNA -> Knowledge Graph -> Semantic Memory, privacy preservation, and SQLite round-trip reload of both graph and vector records.
- `tests/test_continuity_db.py`: Migration schema validation, session persistence, Context DNA storage/retrieval, chronological ordering (`timestamp ASC, event_id ASC`), time-range queries, foreign key enforcement, and end-to-end evidence traceability queries.
- `tests/test_dna.py`: Context DNA v1.0 category verification (Temporal, Application, Resource, Activity, Privacy, Quality), schema version tag, JSON roundtrip serialization/deserialization, application switch tracking, deterministic output.
- `tests/test_sessionizer.py`: Session model validation, empty lists, single-event sessions, multiple events, large inactivity gaps, small gaps, app switching, returning to previous apps, identical timestamps, out-of-order sorting, tie-breaking, resource deduplication, `IncrementalSessionBuilder` chronological contract, and malformed/empty event handling.
- `tests/test_observation.py`: Event validation/serialization, observer abstraction, process switch, title change, duplicate event suppression, privacy exclusions.
- `tests/test_desktop_tool.py`: `get_active_window`, `capture_snapshot`, independent verification, orchestrator integration.
- `tests/test_file_tool.py`: `check_exists`, `inspect_path`, bounded `list_directory`, path traversal rejection, outside-root rejection, disk verification.
- `tests/test_db_events.py`: Workspace event storage, chronological retrieval, time-range queries.
- `tests/test_db.py`: SQLite WAL mode, migrations, audit logging, transaction rollback.
- `tests/test_orchestrator.py`: Full single-step ReAct pipeline, status events.
- `tests/test_tools.py`: BaseTool contracts, registry lookup, duplicate rejection, EchoTool execution.
- `tests/test_llm.py`: MockLLMProvider deterministic generation and error simulation.

---

## Known Limitations in Phase 10
- **No Arbitrary GUI Automation**: Phase 10 intentionally reconstructs window boundaries and positions applications to reopen previously observed resources. It does not attempt pixel-perfect internal UI state replay, form filling, or synthetic mouse/keyboard events.
- **Trusted Applications Only**: Unregistered third-party binaries cannot be launched. Applications must be declared in the authoritative application registry with approved canonical installation locations.
- **Windows OS Native APIs**: On non-Windows platforms, window layout operations fall back safely to `MockWindowManager` without error. Full multi-monitor display metrics and HWND manipulation are native to Windows 10/11.
- **Zero Credentials**: Restoration intentionally excludes session cookies, login tokens, credentials, or SSH private keys.

