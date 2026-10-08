# ORBIS REVIEW-2: COMMAND CATALOG & DEMONSTRATION SCENARIOS

**Document Reference:** `docs/review2_command_catalog.md`  
**Purpose:** Comprehensive natural-language command inventory and end-to-end demonstration scenarios for Review-2.  
**Execution Verification:** Every command in this catalog maps directly to registered tools, intent classifiers, and verified pipelines in `c:\Projects\ORBIS`.

---

## 1. Natural-Language Command Demonstration Catalog

| Command ID | Exact User Command | Capability Tested | Expected Inferred Intent | Primary Tool / Subsystem | Expected Execution Result | Human Approval Required? | Screenshot Linked | Review-2 Section |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **CMD-01** | *"Hey ORBIS, what can you do?"* | Conversational Capability Discovery | System capability inquiry | `AgentOrchestrator` / Direct LLM | Structured summary of capabilities: Context DNA tracking, workspace restoration, web research, document creation, system control | **No** | SCR-04 | Section VI: Conversational Agent |
| **CMD-02** | *"What was I working on?"* | Immediate Continuity Retrieval | Query prior workspace context | `continuity` / `ContinuityRetriever` | Synthesized summary of the most recent session from `orbis.db`, listing active files, applications, and task objective | **No** | SCR-12 | Section V: Context Reconstruction |
| **CMD-03** | *"What was I working on yesterday?"* | Temporal Continuity Retrieval | Historical query with temporal constraint | `continuity` / `DatabaseManager` | Time-bounded extraction querying sessions within yesterday's date window with grounded resource evidence | **No** | SCR-13 | Section V: Temporal Grounding |
| **CMD-04** | *"What changed today?"* | Workspace Delta Analysis | Query recent workspace events | `continuity` / `WorkspaceObservationService` | Summary of newly created/modified files, distinct applications focused, and session durations today | **No** | SCR-09 | Section IV: Desktop Telemetry |
| **CMD-05** | *"What task was unfinished?"* | Unfinished Task Detection | Query uncompleted tasks from prior session | `ice` / `IntentContinuityEngine` | Identification of unresolved task entities (e.g. open test file, pending terminal build) with resumption prompt | **No** | SCR-14 | Section V: Intent Continuity Engine |
| **CMD-06** | *"Continue where I left off."* | Intent Continuity & Resumption | Full resumption of unfinished context | `ice` + `restoration` | ICE identifies the primary prior session and prompts the user with a tailored resumption plan | **No (Preview)** / **Yes (Exec)** | SCR-15 | Section VIII: Workspace Continuity |
| **CMD-07** | *"Restore my workspace."* | Declarative Workspace Restoration | Restore most recent application state | `restoration` / `RestorationEngine` | Preflight-validated dry-run plan generated; upon approval, launches permitted apps and restores window geometry | **Yes (for launch/geometry)** | SCR-16, SCR-18 | Section VIII: Workspace Restoration |
| **CMD-08** | *"Restore only the editor and terminal."* | Granular Selective Restoration | Partial restoration with app filtering | `restoration` / `RestorationEngine` | Filters candidate session to exclude browser/other apps, generating plan only for VS Code and Terminal | **Yes** | SCR-16 | Section VIII: Selective Restoration |
| **CMD-09** | *"List my project files."* | Authorized Filesystem Inspection | Safe filesystem listing | `file` / `FileTool` | Lists files within `allowed_workspace_root`, enforcing directory boundary containment | **No** | SCR-04 | Section X: Filesystem Tools |
| **CMD-10** | *"Read README.md and summarize it."* | File Inspection & Synthesis | Read and summarize authorized file | `file` / `FileTool` | Reads target file, checks bounds, and generates an executive summary | **No** | SCR-04 | Section X: Filesystem Tools |
| **CMD-11** | *"Research the latest information about context-aware AI agents."* | Autonomous Multi-Source Web Research | Web research and source aggregation | `web_research` / `SearchProvider` | Emits web queries via Tavily/Gemini, parses top 5 pages, extracts key findings, and synthesizes markdown answer | **No** | SCR-22, SCR-23 | Section X: Research Engine |
| **CMD-12** | *"Research local-first vector databases, compare them, and create a report."* | Research-to-Document Pipeline | Deep synthesis and file creation | `web_research` + `document` | Conducts multi-query research and writes a structured Markdown document to the authorized workspace | **No (Create)** / **Yes (Overwrite)** | SCR-05, SCR-06 | Section X: Autonomous Pipelines |
| **CMD-13** | *"Create a Word document summarizing our system architecture."* | Rich DOCX Generation | Formatted document creation | `document` / `DocumentTool` | Generates a valid `.docx` file using `python-docx` with bold titles, headings, and bullet points | **No** | SCR-24 | Section X: Document Generation |
| **CMD-14** | *"Create a PowerPoint presentation about ORBIS."* | Rich PPTX Generation | Slide deck generation | `document` / `DocumentTool` | Generates a valid `.pptx` presentation with title slide and content layouts using `python-pptx` | **No** | SCR-26 | Section X: Document Generation |
| **CMD-15** | *"Create an Excel sheet comparing LLM model latencies."* | Rich XLSX Generation | Tabular spreadsheet generation | `document` / `DocumentTool` | Generates a formatted `.xlsx` workbook with styled header fill and auto-fit column widths using `openpyxl` | **No** | SCR-27 | Section X: Document Generation |
| **CMD-16** | *"Create a PDF report on workspace continuity."* | Rich PDF Generation | Formatted PDF document creation | `document` / `DocumentTool` | Generates a styled `.pdf` document with title, headings, and bullet points using ReportLab | **No** | SCR-25 | Section X: Document Generation |
| **CMD-17** | *"Open VS Code."* | Canonical Application Launching | Launch allowlisted application | `app_launch` / `AppLaunchTool` | Resolves `code.exe` from canonical registry allowlist, launches process, and returns verified PID | **No (Allowlisted)** | SCR-30 | Section X: Application Launching |
| **CMD-18** | *"Open Chrome."* | Browser Launching with Window Isolation | Launch web browser | `app_launch` / `AppLaunchTool` | Launches `chrome.exe` with `--new-window` flag to prevent tab collision and ensure clean window capture | **No** | SCR-30 | Section X: Application Launching |
| **CMD-19** | *"Show my current volume."* | Hardware Audio Telemetry | Inspect system volume | `system_control` / `SystemControlTool` | Queries Windows Core Audio Endpoint via `pycaw` and returns exact master volume percentage and mute state | **No** | SCR-29 | Section X: System Control |
| **CMD-20** | *"Set volume to 40%."* | Hardware Audio Modification | Modify system volume | `system_control` / `SystemControlTool` | Adjusts master volume to 40% via `pycaw` and independently verifies audio endpoint level | **No** | SCR-29 | Section X: System Control |
| **CMD-21** | *"Mute system audio."* | Hardware Audio State Change | Mute system master audio | `system_control` / `SystemControlTool` | Sets mute flag via `pycaw` and confirms muted status | **No** | SCR-29 | Section X: System Control |
| **CMD-22** | *"Show my network status."* | Operating System Network Inspection | Inspect active network adapters | `network_control` / `NetworkControlTool` | Returns active interfaces (Wi-Fi, Ethernet), assigned IP addresses, and gateway reachability | **No** | SCR-31 | Section X: Network Control |
| **CMD-23** | *"Run git status in the project directory."* | Sandboxed Terminal Automation | Execute safe command in workspace | `terminal` / `TerminalTool` | Executes `git status` inside `allowed_workspace_root`, captures stdout, and enforces 30s timeout | **Yes (Approval Gate)** | SCR-33, SCR-35 | Section X: Terminal Automation |
| **CMD-24** | *"Read file ../../../windows/system32/cmd.exe"* | Path Traversal Security Attack | Adversarial malicious directory traversal | `file` / `FileTool` | Strictly blocked by path containment validation; raises `PathTraversalDenied` error | **Blocked (Rejected)** | SCR-32 | Section IX: Safety Architecture |
| **CMD-25** | *"Delete all project files."* | High-Risk Destructive Command | Adversarial destructive operation | Policy Guard / `HierarchicalPlanner` | Rejected immediately: destructive commands outside allowlist are prohibited by security policy | **Blocked (Rejected)** | SCR-32 | Section IX: Safety Architecture |

---

## 2. Fifteen End-to-End Demonstration Scenarios

### Scenario 1: Passive Observation to Session Finalization
- **User Goal:** Work normally across VS Code and Chrome while ORBIS transparently observes and segments the activity without interruption.
- **Starting State:** ORBIS background daemon active; observation heartbeat running every 3 seconds.
- **Actions:** User opens VS Code (`c:\Projects\ORBIS`), edits a Python file, switches to Chrome to view FastAPI documentation, then pauses for 3 minutes.
- **Expected ORBIS Behavior:** `WindowsWorkspaceObserver` captures window focus events; `EventSanitizer` scrubs window titles; `IncrementalSessionBuilder` detects 180s idle gap, marks session complete, and stores record in `orbis.db`.
- **Tools Involved:** `WorkspaceObservationService`, `EventSanitizer`, `IncrementalSessionBuilder`, `DatabaseManager`.
- **Safety / Approval Behavior:** Completely passive observation; zero user interruption; zero elevated privileges required.
- **Expected Screenshots:** SCR-07 (Observation telemetry feed), SCR-09 (Session history timeline).
- **Final State:** 1 new session created in `orbis.db` with associated application and window event records.
- **Research Relevance:** Validates Gap G1 & G2: non-intrusive, privacy-preserving desktop telemetry capturing cross-application continuity.

### Scenario 2: Context DNA Synthesis & Focus Scoring
- **User Goal:** Inspect how ORBIS represents multi-application work as structured "Context DNA".
- **Starting State:** Completed work session in `orbis.db`.
- **Commands / Actions:** Navigate to Workspace View -> Select latest session -> Click "Context DNA".
- **Expected ORBIS Behavior:** `ContextDNASynthesizer` calculates dwell times, determines primary application (VS Code), applies focus score formula ($\text{score} = 0.88$), and extracts task intent.
- **Tools Involved:** `ContextDNASynthesizer`, `DatabaseManager`.
- **Safety / Approval Behavior:** Read-only inspection; no external data transmission.
- **Expected Screenshots:** SCR-08 (Context DNA Inspector card).
- **Final State:** Reconstructed Context DNA displayed with mathematical focus intensity, application weights, and entity hashes.
- **Research Relevance:** Validates Context DNA design (Section IX) as a richer, explainable representation than raw embeddings.

### Scenario 3: Semantic Continuity Retrieval (Paraphrase Resilience)
- **User Goal:** Retrieve a prior work context using natural-language paraphrases without knowing exact filenames.
- **Starting State:** Multiple historical sessions in `orbis.db`.
- **Commands:** *"Which project was I editing earlier today?"*
- **Expected ORBIS Behavior:** `QueryClassifier` routes prompt to `ContinuityRetriever`; Sentence Transformers encodes query; hybrid scorer combines vector similarity with recency; returns accurate project folder.
- **Tools Involved:** `ContinuityRetriever`, `SentenceTransformers`, `DatabaseManager`.
- **Safety / Approval Behavior:** Read-only retrieval.
- **Expected Screenshots:** SCR-11 (Semantic Continuity Retrieval card).
- **Final State:** Agent outputs grounded response: *"You were working on the ORBIS project in `c:\Projects\ORBIS`, focusing on `core/continuity/retriever.py`."*
- **Research Relevance:** Validates Gap G3 & Objective 3: semantic retrieval succeeds where keyword-only matching fails.

### Scenario 4: "What Was I Working On?" Context Reconstruction
- **User Goal:** Quickly recover working memory after a long interruption or morning startup.
- **Starting State:** User sits down at PC; ORBIS client opened.
- **Commands:** *"What was I working on?"*
- **Expected ORBIS Behavior:** Identifies the most recent closed session, queries Context DNA and active entities, synthesizes a concise, bulleted briefing.
- **Tools Involved:** `ContinuityRetriever`, `AgentOrchestrator`, `LLMClient`.
- **Safety / Approval Behavior:** Pure informational response.
- **Expected Screenshots:** SCR-12 ("What was I working on?" answer bubble).
- **Final State:** User presented with a 3-bullet recap of active files, applications, and unfinished task goals.
- **Research Relevance:** Directly addresses the cognitive friction problem formulated in Review-1 Section I & II.

### Scenario 5: Historical Temporal Grounding
- **User Goal:** Inquire about work completed on a specific prior day.
- **Starting State:** Multiple days of session data in `orbis.db`.
- **Commands:** *"What did I work on yesterday?"*
- **Expected ORBIS Behavior:** Temporal extractor parses "yesterday", calculates the exact UTC date range, queries `get_sessions(start_time, end_time)`, and formats a truthful summary. If no events exist, it honestly reports zero activity.
- **Tools Involved:** `ContinuityRetriever`, `TemporalParser`, `DatabaseManager`.
- **Safety / Approval Behavior:** Zero hallucination defense: never invents fake sessions.
- **Expected Screenshots:** SCR-13 (Temporal query answer).
- **Final State:** Grounded chronological recap of yesterday's sessions or verified empty state.
- **Research Relevance:** Validates Gap G4: temporal reasoning avoids recency bias and prevents cross-day context contamination.

### Scenario 6: Unfinished Task Detection
- **User Goal:** Discover what operations were left incomplete prior to an interruption.
- **Starting State:** Prior session terminated while editing a test file with uncommitted changes.
- **Commands:** *"What task was unfinished?"*
- **Expected ORBIS Behavior:** `IntentContinuityEngine` inspects last focused window, file modification timestamps, and terminal state to flag unfinished test execution.
- **Tools Involved:** `IntentContinuityEngine`, `extractor.py`, `models.py`.
- **Safety / Approval Behavior:** Informational recommendation.
- **Expected Screenshots:** SCR-14 (ICE Recommendation card).
- **Final State:** ICE displays: *"Unfinished Task: Test execution for `tests/test_gateway.py`. Would you like to resume?"*
- **Research Relevance:** Validates Gap G7: moves system from retrospective search to proactive task continuity.

### Scenario 7: ICE Proactive Resumption Recommendation
- **User Goal:** Receive a proactive recommendation and launch the next logical action.
- **Starting State:** User awakened ORBIS and opened Command Center.
- **Commands:** *"Continue where I left off."*
- **Expected ORBIS Behavior:** ICE retrieves top session, generates next-action candidate with 85% confidence score, and presents an interactive "Resume Workspace" action button.
- **Tools Involved:** `IntentContinuityEngine`, `RestorationEngine`.
- **Safety / Approval Behavior:** Recommends action proactively, but pauses for user authorization before launching software.
- **Expected Screenshots:** SCR-14, SCR-15.
- **Final State:** User clicks "Resume", triggering the preflight restoration pipeline.
- **Research Relevance:** Validates Section XII (ICE Strategy 1): proactive assistance with explainable rationale.

### Scenario 8: Preflight-Validated Workspace Restoration (Dry-Run Preview)
- **User Goal:** Inspect exactly what applications and files ORBIS will open before anything executes.
- **Starting State:** Target session selected for restoration.
- **Commands:** *"Restore my workspace."* (or click "Plan Restoration" in UI).
- **Expected ORBIS Behavior:** `RestorationEngine` invokes `PreflightValidator`, checks binary paths in `ApplicationRegistry`, verifies file existence, checks URL safety, and returns an ordered preview plan.
- **Tools Involved:** `RestorationEngine`, `PreflightValidator`, `ApplicationRegistry`.
- **Safety / Approval Behavior:** Mandatory dry-run preview: no subprocess is spawned until explicitly approved.
- **Expected Screenshots:** SCR-16 (Restoration Preflight Plan table).
- **Final State:** UI renders step-by-step preview showing: App (VS Code), Target (`c:\Projects\ORBIS`), Policy (`ALLOWED`), Status (`READY`).
- **Research Relevance:** Validates Gap G9 & Section XIII (Stage R1–R4): deterministic preflight safety checks.

### Scenario 9: Full Multi-Application Workspace Restoration with Geometry
- **User Goal:** Restore a development environment with VS Code and Terminal placed side-by-side.
- **Starting State:** Clean desktop; target session contained VS Code on left half and Terminal on right half.
- **Commands:** User clicks "Authorize & Execute" on validated restoration plan.
- **Expected ORBIS Behavior:** Launches `code.exe` with workspace directory; launches `wt.exe`; monitors PIDs; uses `WindowsWindowManager.set_window_bounds` to position windows at recorded $(x, y, w, h)$ coordinates.
- **Tools Involved:** `RestorationEngine`, `WindowsWindowManager`, `Win32 APIs`.
- **Safety / Approval Behavior:** User explicitly authorized the plan; execution logged to audit trail.
- **Expected Screenshots:** SCR-18 (Restored side-by-side multi-window desktop).
- **Final State:** VS Code and Windows Terminal open and positioned exactly where they were before the interruption.
- **Research Relevance:** Validates Gap G6 & Section XIII (Stage R5–R6): physical workspace reconstruction beyond pure text retrieval.

### Scenario 10: Deep Web Research and Multi-Format Report Generation
- **User Goal:** Research a technical topic and generate a formatted PDF report.
- **Starting State:** Command Center open.
- **Commands:** *"Research latest local-first LLM frameworks and create a PDF report named research_summary.pdf."*
- **Expected ORBIS Behavior:** `HierarchicalPlanner` decomposes task into DAG: `web_research` -> aggregate sources -> `document` tool (`format="pdf"`). ReportLab compiles styled PDF.
- **Tools Involved:** `HierarchicalPlanner`, `WebResearchTool`, `DocumentTool`, `ReportLab`.
- **Safety / Approval Behavior:** Automated low-risk pipeline within `allowed_workspace_root`; verifies file on disk.
- **Expected Screenshots:** SCR-05, SCR-06, SCR-25.
- **Final State:** File `research_summary.pdf` created in workspace; opened in PDF viewer displaying structured typography and source citations.
- **Research Relevance:** Demonstrates autonomous multi-step agent capability supporting knowledge workers.

### Scenario 11: Safe System Hardware Control with Live Verification
- **User Goal:** Adjust audio volume using natural language with guaranteed post-action verification.
- **Starting State:** Master volume at 80%.
- **Commands:** *"Set volume to 40%."*
- **Expected ORBIS Behavior:** `SystemControlTool` interfaces with Windows Core Audio via `pycaw`, sets master scalar to 0.40, queries endpoint to verify 40%, and returns confirmation.
- **Tools Involved:** `SystemControlTool`, `pycaw`, `comtypes`.
- **Safety / Approval Behavior:** Whitelisted safe hardware operation; bounds-checked (0–100%); verified independently.
- **Expected Screenshots:** SCR-29 (ElasticSlider at 40% with verified badge).
- **Final State:** System master volume set to 40%; UI slider dynamically updates to match physical OS level.
- **Research Relevance:** Demonstrates native Windows integration with post-action verification (Section XIII Stage R6).

### Scenario 12: High-Risk Human Approval Workflow
- **User Goal:** Execute a terminal command that requires explicit human confirmation.
- **Starting State:** Agent idle in Command Center.
- **Commands:** *"Run dir in the project directory using terminal."*
- **Expected ORBIS Behavior:** `AgentOrchestrator` detects `TerminalTool` carries `RiskLevel.HIGH`; pauses execution; sets pending approval state; pushes modal overlay to Electron UI.
- **Tools Involved:** `AgentOrchestrator`, `TaskManager`, `TerminalTool`, `routes.py`.
- **Safety / Approval Behavior:** Mandatory human confirmation gate; command blocked until user clicks "Authorize" in modal.
- **Expected Screenshots:** SCR-17, SCR-33.
- **Final State:** Upon approval, command executes, output is captured safely, and audit event is recorded.
- **Research Relevance:** Validates Section XIV (Human Approval Control) and defense against unconstrained autonomous execution.

### Scenario 13: Local Voice Command to Execution
- **User Goal:** Issue an operational command completely hands-free using local speech recognition.
- **Starting State:** Microphone enabled; voice pipeline idle.
- **Commands:** User speaks: *"Show my network status."*
- **Expected ORBIS Behavior:** `VoiceCaptureService` captures audio; Whisper transcribes audio locally; normalizer cleans text; routes command to `NetworkControlTool`; displays result and speaks audio confirmation.
- **Tools Involved:** `LiveTranscriber`, `Whisper`, `NetworkControlTool`, `TTS`.
- **Safety / Approval Behavior:** Local-only audio processing; no voice data transmitted to cloud.
- **Expected Screenshots:** SCR-28, SCR-31.
- **Final State:** Network adapters and IP addresses displayed on screen; TTS announces status.
- **Research Relevance:** Validates Section VII (M10 Voice Module) and Objective 6: accessible, privacy-first hands-free interaction.

### Scenario 14: Multi-Turn Conversational Clarification
- **User Goal:** Iteratively refine a request across multiple conversation turns while maintaining working memory.
- **Starting State:** Command Center active.
- **Commands:**  
  *Turn 1:* *"Find files related to routing."* (Agent returns 3 files).  
  *Turn 2:* *"Summarize the first one."* (Agent resolves "the first one" to `core/gateway/routes.py`).  
  *Turn 3:* *"What endpoints does it define?"* (Agent answers based on conversational history).
- **Expected ORBIS Behavior:** `ConversationContextManager` tracks entity history, resolves ordinal pronouns ("the first one"), and preserves context across turns.
- **Tools Involved:** `ConversationContextManager`, `AgentOrchestrator`, `FileTool`.
- **Safety / Approval Behavior:** Bounded in-memory context; zero cross-session leakage.
- **Expected Screenshots:** SCR-04 (Multi-turn chat bubbles).
- **Final State:** User successfully extracts endpoint list through progressive conversational refinement.
- **Research Relevance:** Demonstrates conversational memory grounded in operational desktop artifacts.

### Scenario 15: Failure Containment, Verification, & Recovery
- **User Goal:** Observe how ORBIS handles an invalid file request or missing executable gracefully without crashing.
- **Starting State:** Agent active.
- **Commands:** *"Restore workspace session from non-existent project folder."*
- **Expected ORBIS Behavior:** `PreflightValidator` flags missing directory; `RestorationEngine` marks step as `SKIPPED_MISSING_RESOURCE`; execution continues for valid apps; agent reports honest error summary with recovery options.
- **Tools Involved:** `PreflightValidator`, `RestorationEngine`, `VerificationReport`.
- **Safety / Approval Behavior:** Graceful failure containment: never hangs, never executes broken paths, alerts user transparently.
- **Expected Screenshots:** SCR-16 (Step marked skipped), SCR-32 (Safety audit card).
- **Final State:** Valid applications restored; missing resource cleanly reported to user with advice.
- **Research Relevance:** Validates Section XIII Stage R7 (Recovery) and robust real-world error containment.
