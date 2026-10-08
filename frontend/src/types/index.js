/**
 * ORBIS Frontend Type Definitions & Enums.
 *
 * Defines the core data models, view identifiers, agent lifecycle states,
 * and service interfaces for the ORBIS desktop agent experience.
 */

// ── Views / Navigation ────────────────────────────────────────────────────────
export const ViewType = Object.freeze({
  HOME: 'home',
  COMMAND: 'command',
  LIVE_FLOW: 'live_flow',
  WORKSPACE: 'workspace',
  TOOLS: 'tools',
  RESTORATION: 'restoration',
  SYSTEM_CONTROL: 'system_control',
  RESEARCH: 'research',
  SETTINGS: 'settings',
});

export const NAV_ITEMS = [
  { id: ViewType.HOME, label: 'Home', description: 'Core ORBIS identity & globe' },
  { id: ViewType.COMMAND, label: 'Command', description: 'Conversational agent workspace' },
  { id: ViewType.LIVE_FLOW, label: 'Live Flow', description: 'Real-time task execution lifecycle' },
  { id: ViewType.WORKSPACE, label: 'Workspace', description: 'Continuity memory & workspace restoration' },
  { id: ViewType.TOOLS, label: 'Tools', description: 'Integrated agent capabilities' },
  { id: ViewType.SYSTEM_CONTROL, label: 'System', description: 'Hardware & OS controls' },
  { id: ViewType.RESEARCH, label: 'Research', description: 'Web intelligence & documents' },
  { id: ViewType.SETTINGS, label: 'Settings', description: 'Agent preferences & safety' },
];

// ── Agent Operational States ──────────────────────────────────────────────────
export const AgentVisualState = Object.freeze({
  IDLE: 'idle',
  LISTENING: 'listening',
  THINKING: 'thinking',
  PLANNING: 'planning',
  EXECUTING: 'executing',
  RESPONDING: 'responding',
  COMPLETED: 'completed',
  FAILED: 'failed',
  APPROVAL_REQUIRED: 'approval',
});

export const STATUS_LABELS = Object.freeze({
  [AgentVisualState.IDLE]: 'Ready',
  [AgentVisualState.LISTENING]: 'Listening…',
  [AgentVisualState.THINKING]: 'Processing…',
  [AgentVisualState.PLANNING]: 'Planning task…',
  [AgentVisualState.EXECUTING]: 'Executing…',
  [AgentVisualState.RESPONDING]: 'Responding…',
  [AgentVisualState.COMPLETED]: 'Complete',
  [AgentVisualState.FAILED]: 'Action failed',
  [AgentVisualState.APPROVAL_REQUIRED]: 'Confirmation required',
});

// ── Task Lifecycle & Event Types ──────────────────────────────────────────────
export const AgentEventType = Object.freeze({
  TASK_STARTED: 'TASK_STARTED',
  PLAN_CREATED: 'PLAN_CREATED',
  STEP_STARTED: 'STEP_STARTED',
  TOOL_EXECUTION: 'TOOL_EXECUTION',
  STEP_COMPLETED: 'STEP_COMPLETED',
  VERIFICATION_STARTED: 'VERIFICATION_STARTED',
  VERIFICATION_COMPLETED: 'VERIFICATION_COMPLETED',
  TASK_COMPLETED: 'TASK_COMPLETED',
  TASK_FAILED: 'TASK_FAILED',
  APPROVAL_REQUIRED: 'APPROVAL_REQUIRED',
  APPROVAL_GRANTED: 'APPROVAL_GRANTED',
  APPROVAL_DENIED: 'APPROVAL_DENIED',
});

// ── Tool Availability State ───────────────────────────────────────────────────
export const ToolStatus = Object.freeze({
  AVAILABLE: 'available',
  CONNECTING: 'connecting',
  UNAVAILABLE: 'unavailable',
});

// ── Canonical ORBIS Tool Registry ─────────────────────────────────────────────
export const ORBIS_TOOLS = [
  {
    id: 'knowledge',
    name: 'Knowledge & Q&A',
    description: 'Local reasoning, query synthesis, and fact retrieval.',
    status: ToolStatus.AVAILABLE,
    category: 'Intelligence',
    icon: 'Brain',
  },
  {
    id: 'web_research',
    name: 'Web Research',
    description: 'Autonomous web searches, source synthesis, and evidence gathering.',
    status: ToolStatus.AVAILABLE,
    category: 'Intelligence',
    icon: 'Globe2',
  },
  {
    id: 'files',
    name: 'File System',
    description: 'Safe inspected file reading, search, and directory management.',
    status: ToolStatus.AVAILABLE,
    category: 'System',
    icon: 'FolderTree',
  },
  {
    id: 'terminal',
    name: 'Terminal Automation',
    description: 'Allowlist-constrained command execution with full output isolation.',
    status: ToolStatus.AVAILABLE,
    category: 'Automation',
    icon: 'Terminal',
  },
  {
    id: 'documents',
    name: 'Document Generation',
    description: 'Structured report generation, markdown compilation, and artifact authoring.',
    status: ToolStatus.AVAILABLE,
    category: 'Creation',
    icon: 'FileText',
  },
  {
    id: 'desktop',
    name: 'Desktop Context',
    description: 'Window observation, active application tracking, and focus telemetry.',
    status: ToolStatus.AVAILABLE,
    category: 'Context',
    icon: 'Monitor',
  },
  {
    id: 'voice',
    name: 'Voice Engine',
    description: 'Push-to-Talk Whisper STT and Windows SAPI5 TTS speech synthesis.',
    status: ToolStatus.AVAILABLE,
    category: 'Interface',
    icon: 'Mic',
  },
  {
    id: 'continuity',
    name: 'Context Continuity',
    description: 'Sessionization, Context DNA, and workspace knowledge graphs.',
    status: ToolStatus.AVAILABLE,
    category: 'Memory',
    icon: 'History',
  },
  {
    id: 'restoration',
    name: 'Workspace Restoration',
    description: 'Multi-application state reconstruction with preflight validation and safety gates.',
    status: ToolStatus.AVAILABLE,
    category: 'Automation',
    icon: 'RotateCcw',
  },
  {
    id: 'system_control',
    name: 'System Control',
    description: 'Hardware volume slider, mute toggling, and verified Windows audio controls.',
    status: ToolStatus.AVAILABLE,
    category: 'System',
    icon: 'Sliders',
  },
];
