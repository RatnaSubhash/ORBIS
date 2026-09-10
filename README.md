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

#### What is NOT Implemented Yet (Reserved for Future Phases):
- ❌ Phase 8+ Desktop GUI (Electron / React)
- ❌ Voice Interaction & TTS (Phase 11)
- ❌ Browser UI Automation & DOM Scraping (Playwright / Selenium)
- ❌ Workspace Restoration & Window Repositioning (Phase 10)
- ❌ Task Intent Inference & Next-Action Prediction (Phase 9 - ICE)
- ❌ Model Context Protocol (MCP) & Cryptographic Audit Chains
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

---

## Running the Automated Test Suite

All 93 unit and integration tests run deterministically offline with zero external network calls:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

### Test Coverage Breakdown:
- `tests/test_knowledge_graph.py`: 10 tests covering node/edge creation, duplicate prevention, GraphBuilder from Session/DNA/Events, session subgraph query, app/resource/event neighbors, SQLite persistence & reload parity, traceability, and malformed input handling.
- `tests/test_semantic_memory.py`: 9 tests covering BaseEmbeddingProvider contract, MockEmbeddingProvider determinism and normalization, text representation generation, FAISS add/search, cosine similarity interpretation (higher = more similar), empty index, invalid $k$, metadata mapping, save/reload consistency, and index/metadata mismatch detection.
- `tests/test_phase4_integration.py`: 2 tests covering end-to-end Session $\to$ Context DNA $\to$ Knowledge Graph $\to$ Semantic Memory, privacy preservation, and SQLite round-trip reload of both graph and vector records.
- `tests/test_continuity_db.py`: Migration 3 schema validation, session persistence, Context DNA storage/retrieval, chronological ordering (`timestamp ASC, event_id ASC`), time-range queries, foreign key enforcement, and end-to-end evidence traceability queries.
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

## Known Limitations in Phase 4
- **Independent Indexes**: Phase 4 provides graph-based and semantic memory indexes separately. Final unified hybrid retrieval and ranking combining graph proximity, lexical match, and semantic similarity are deferred to subsequent phases.
- **No Task-Intent Inference**: Memory embeddings represent factual session and context summaries (observed apps, paths, event counts). They do not infer high-level intent (e.g. "user was implementing oauth").
- **Offline Baseline**: The default test and demonstration suite uses `MockEmbeddingProvider` for fast, reproducible verification without requiring model downloads. The production `SentenceTransformerEmbeddingProvider` is initialized dynamically when local weights are present.


---

## Setup & Environment

The project uses **Python 3.12** in the virtual environment at `C:\Projects\ORBIS\.venv`.

### 1. Configuration Settings

Key configuration options in `core/config.py`:
- `SESSION_GAP_MINUTES` (default: `15.0`): Maximum minutes of inactivity between consecutive workspace events before demarcating a new session.
- `OBSERVATION_ENABLED` (default: `true`): Toggles passive workspace observation.
- `OBSERVATION_INTERVAL` (default: `3.0`): Polling interval in seconds for window tracking.
- `ALLOWED_WORKSPACE_ROOT` (default: `.`): Path boundary for safe `FileTool` operations.
- `EXCLUDED_PROCESSES`: Process names ignored by observation (e.g. password managers).
- `EXCLUDED_WINDOW_TITLES`: Substrings in window titles that trigger privacy redaction.

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

---

## Running the Automated Test Suite

All 70 unit and integration tests run deterministically offline with zero external dependencies:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

### Test Coverage Breakdown:
- `tests/test_continuity_db.py`: Migration 3 schema validation, session persistence, Context DNA storage/retrieval, chronological ordering, time-range queries, foreign key enforcement, and end-to-end evidence traceability queries.
- `tests/test_dna.py`: Context DNA v1.0 category verification (Temporal, Application, Resource, Activity, Privacy, Quality), version tag enforcement, JSON roundtrip serialization/deserialization, application switch tracking, deterministic output.
- `tests/test_sessionizer.py`: `Session` model validation, empty lists, single-event sessions, multiple events, large inactivity gaps, small gaps, app switching, returning to previous apps, identical timestamps, out-of-order sorting, tie-breaking, resource deduplication, `IncrementalSessionBuilder`, and malformed/empty event handling.
- `tests/test_observation.py`: Event validation/serialization, observer abstraction, process switch, title change, duplicate event suppression, privacy exclusions.
- `tests/test_desktop_tool.py`: `get_active_window`, `capture_snapshot`, independent verification, orchestrator integration.
- `tests/test_file_tool.py`: `check_exists`, `inspect_path`, bounded `list_directory`, path traversal rejection, outside-root rejection, disk verification.
- `tests/test_db_events.py`: Workspace event storage, chronological retrieval, time-range queries.
- `tests/test_db.py`: SQLite WAL mode, migrations, audit logging, transaction rollback.
- `tests/test_orchestrator.py`: Full single-step ReAct pipeline, status events.
- `tests/test_tools.py`: BaseTool contracts, registry lookup, duplicate rejection, EchoTool execution.
- `tests/test_llm.py`: MockLLMProvider deterministic generation and error simulation.

---

## Known Limitations in Phase 3
- **Factual Grouping Only**: Phase 3 groups workspace events strictly by observable signals (time, process name, resource path). It makes no semantic inference about user intent (e.g. it does not label a session as "debugging authentication").
- **Rule-Based Baseline**: Segmentation relies on temporal gap thresholding and observable continuity rather than machine learning clustering.
- **Resource Depth**: Resource metadata is derived exclusively from window titles and paths observed by Phase 2. Files are not opened or crawled.
