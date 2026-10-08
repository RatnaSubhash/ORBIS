/**
 * LiveFlowView — Real-Time Agent Execution Lifecycle Visualizer (Phase E & Phase H).
 *
 * Consumes real WebSocket AgentEvents from AgentContext:
 *   TASK_STARTED, PLAN_CREATED, STEP_STARTED, TOOL_EXECUTION,
 *   VERIFICATION_STARTED, VERIFICATION_COMPLETED,
 *   STEP_COMPLETED, TASK_COMPLETED, TASK_FAILED
 *
 * Features:
 *   - Real-time event timeline stream
 *   - Active tool telemetry powered by official React Bits LogoLoop
 *   - Lifecycle progression visualizer powered by official React Bits Stepper
 *   - Contextual refraction surface powered by official React Bits FluidGlass
 *   - Contextual quick actions powered by official React Bits Dock
 *   - Step status & verification indicator
 *   - Task summary & approval status
 */

import { useMemo } from 'react';
import { useAgent } from '../../state/AgentContext';
import { ORBIS_TOOLS, ViewType } from '../../types';
import LogoLoop from '../ui/LogoLoop';
import Stepper, { Step } from '../ui/Stepper';
import FluidGlass from '../ui/FluidGlass';
import Dock from '../ui/Dock';
import {
  Brain,
  Globe2,
  FolderTree,
  Terminal,
  FileText,
  Monitor,
  Mic,
  History,
  RotateCcw,
  Sliders,
  Volume2,
  Wrench,
  Trash2,
  Sparkles,
} from 'lucide-react';

const CAPABILITY_META = {
  knowledge: {
    icon: Brain,
    color: '#c084fc',
    bg: 'rgba(192, 132, 252, 0.15)',
    border: 'rgba(192, 132, 252, 0.35)',
  },
  web_research: {
    icon: Globe2,
    color: '#38bdf8',
    bg: 'rgba(56, 189, 248, 0.15)',
    border: 'rgba(56, 189, 248, 0.35)',
  },
  files: {
    icon: FolderTree,
    color: '#facc15',
    bg: 'rgba(250, 204, 21, 0.15)',
    border: 'rgba(250, 204, 21, 0.35)',
  },
  terminal: {
    icon: Terminal,
    color: '#4ade80',
    bg: 'rgba(74, 222, 128, 0.15)',
    border: 'rgba(74, 222, 128, 0.35)',
  },
  documents: {
    icon: FileText,
    color: '#60a5fa',
    bg: 'rgba(96, 165, 250, 0.15)',
    border: 'rgba(96, 165, 250, 0.35)',
  },
  desktop: {
    icon: Monitor,
    color: '#a78bfa',
    bg: 'rgba(167, 139, 250, 0.15)',
    border: 'rgba(167, 139, 250, 0.35)',
  },
  voice: {
    icon: Mic,
    color: '#f472b6',
    bg: 'rgba(244, 114, 182, 0.15)',
    border: 'rgba(244, 114, 182, 0.35)',
  },
  continuity: {
    icon: History,
    color: '#2dd4bf',
    bg: 'rgba(45, 212, 191, 0.15)',
    border: 'rgba(45, 212, 191, 0.35)',
  },
  restoration: {
    icon: RotateCcw,
    color: '#33ccdd',
    bg: 'rgba(51, 204, 221, 0.15)',
    border: 'rgba(51, 204, 221, 0.35)',
  },
  system_control: {
    icon: Sliders,
    color: '#fb923c',
    bg: 'rgba(251, 146, 60, 0.15)',
    border: 'rgba(251, 146, 60, 0.35)',
  },
};

export function LiveFlowView() {
  const {
    currentTaskId,
    taskStatus,
    taskSummary,
    events,
    pendingApproval,
    resolveApproval,
    gatewayOnline,
    executeTaskPrompt,
    clearConversationUi,
    setActiveView,
  } = useAgent();

  // Active step & tool derived from the latest event in the stream
  const latestEvent = useMemo(() => {
    return events.length > 0 ? events[events.length - 1] : null;
  }, [events]);

  const activeTool = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].tool_name) {
        return events[i].tool_name;
      }
    }
    return null;
  }, [events]);

  const verificationStatus = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].event_type === 'VERIFICATION_COMPLETED') return 'COMPLETED';
      if (events[i].event_type === 'VERIFICATION_STARTED') return 'IN_PROGRESS';
    }
    return null;
  }, [events]);

  // Stepper current active step index (1-5) derived from real task status
  const currentStepIndex = useMemo(() => {
    if (taskStatus === 'SUBMITTING' || taskStatus === 'ACCEPTED') return 1;
    if (taskStatus === 'PLANNING') return 2;
    if (taskStatus === 'RUNNING' || taskStatus === 'EXECUTING') return 3;
    if (pendingApproval || taskStatus === 'VERIFYING') return 4;
    if (taskStatus === 'COMPLETED' || taskStatus === 'FAILED') return 5;
    return 1;
  }, [taskStatus, pendingApproval]);

  // Telemetry items for LogoLoop (Active capabilities with visible icons & badges)
  const toolLogos = useMemo(() => {
    return ORBIS_TOOLS.map((tool) => {
      const meta = CAPABILITY_META[tool.id] || {
        icon: Sparkles,
        color: '#33ccdd',
        bg: 'rgba(51, 204, 221, 0.15)',
        border: 'rgba(51, 204, 221, 0.35)',
      };
      const Icon = meta.icon;
      return {
        node: (
          <div className="orbis-telemetry-badge" title={`${tool.name} — ${tool.description}`}>
            <span
              className="orbis-telemetry-logo"
              style={{
                color: meta.color,
                background: meta.bg,
                borderColor: meta.border,
              }}
            >
              <Icon size={16} strokeWidth={2.2} />
            </span>
            <span className="orbis-telemetry-name">{tool.name}</span>
          </div>
        ),
        title: tool.name,
      };
    });
  }, []);

  // Contextual Dock quick actions
  const dockItems = useMemo(
    () => [
      {
        icon: <History size={18} className="dock-svg" />,
        label: 'Continue Workspace',
        onClick: () => executeTaskPrompt('Continue where I left off', false),
      },
      {
        icon: <Terminal size={18} className="dock-svg" />,
        label: 'New Command',
        onClick: () => setActiveView(ViewType.COMMAND),
      },
      {
        icon: <Volume2 size={18} className="dock-svg" />,
        label: 'System Hardware',
        onClick: () => setActiveView(ViewType.SYSTEM_CONTROL),
      },
      {
        icon: <Wrench size={18} className="dock-svg" />,
        label: 'Explore Tools',
        onClick: () => setActiveView(ViewType.TOOLS),
      },
      {
        icon: <Trash2 size={18} className="dock-svg" />,
        label: 'Clear Flow Events',
        onClick: () => clearConversationUi(),
      },
    ],
    [executeTaskPrompt, setActiveView, clearConversationUi]
  );

  return (
    <div className="orbis-live-flow-view">
      {/* Header bar */}
      <div className="orbis-live-flow-header">
        <div>
          <h2 className="orbis-live-flow-title">Live Execution Flow</h2>
          <span className="orbis-live-flow-subtitle">
            Autonomous agent pipeline & real-time telemetry
          </span>
        </div>
        <div className="orbis-flow-badges">
          {currentTaskId && (
            <span className="orbis-flow-task-id" title={currentTaskId}>
              Task: {currentTaskId.slice(0, 8)}…
            </span>
          )}
          <span className="orbis-status-badge" data-status={taskStatus}>
            {taskStatus}
          </span>
        </div>
      </div>

      {/* Telemetry Strip powered by official React Bits LogoLoop */}
      <div className="orbis-telemetry-strip-container">
        <div className="orbis-telemetry-label">Active Capabilities</div>
        <LogoLoop
          logos={toolLogos}
          speed={30}
          direction="left"
          gap={14}
          logoHeight={36}
          pauseOnHover={true}
          className="orbis-flow-logoloop"
        />
      </div>

      {/* Stepper Lifecycle Visualizer */}
      <div className="orbis-flow-stepper-wrap">
        <Stepper
          initialStep={currentStepIndex}
          currentStep={currentStepIndex}
          disableStepIndicators={true}
          showFooter={false}
          stepCircleContainerClassName="orbis-flow-stepper-panel"
        >
          <Step>
            <div className="orbis-step-desc">
              <strong>Task Accepted:</strong> {currentTaskId ? `ID ${currentTaskId.slice(0, 8)}…` : 'Awaiting task signal'}
            </div>
          </Step>
          <Step>
            <div className="orbis-step-desc">
              <strong>Plan Construction:</strong> {taskSummary || 'Formulating action plan'}
            </div>
          </Step>
          <Step>
            <div className="orbis-step-desc">
              <strong>Execution:</strong> {activeTool ? `Tool active: ${activeTool}` : 'Autonomous steps running'}
            </div>
          </Step>
          <Step>
            <div className="orbis-step-desc">
              <strong>Verification:</strong> {pendingApproval ? 'High-risk gate awaiting approval' : 'Validating output state'}
            </div>
          </Step>
          <Step>
            <div className="orbis-step-desc">
              <strong>Resolution:</strong> {taskStatus === 'COMPLETED' ? 'Task completed successfully' : taskStatus === 'FAILED' ? 'Execution completed with warnings' : 'Cycle ready'}
            </div>
          </Step>
        </Stepper>
      </div>

      {/* Active Execution Metrics Grid wrapped in authentic FluidGlass */}
      <FluidGlass className="orbis-flow-glass-panel" borderRadius={14} brightness={35}>
        <div className="orbis-flow-metrics-grid">
          <div className="orbis-metric-card">
            <span className="orbis-metric-title">Active Tool</span>
            <span className="orbis-metric-value tool">
              {activeTool || (isSubmittingTask(taskStatus) ? 'Evaluating' : 'Idle')}
            </span>
          </div>
          <div className="orbis-metric-card">
            <span className="orbis-metric-title">Verification</span>
            <span className={`orbis-metric-value verification ${verificationStatus || 'none'}`}>
              {verificationStatus || 'Pending'}
            </span>
          </div>
          <div className="orbis-metric-card">
            <span className="orbis-metric-title">Total Events</span>
            <span className="orbis-metric-value count">{events.length}</span>
          </div>
          <div className="orbis-metric-card">
            <span className="orbis-metric-title">Latest Signal</span>
            <span className="orbis-metric-value signal">
              {latestEvent?.event_type || 'Awaiting Task'}
            </span>
          </div>
        </div>
      </FluidGlass>

      {/* Pending Approval Dialog if active */}
      {pendingApproval && (
        <FluidGlass className="orbis-flow-glass-panel" borderRadius={14} brightness={40}>
          <div className="orbis-flow-approval-banner">
            <div className="orbis-flow-approval-info">
              <span className="orbis-risk-badge risk-HIGH">APPROVAL REQUIRED</span>
              <strong>{pendingApproval.tool}</strong>
              <p>{pendingApproval.message}</p>
            </div>
            <div className="orbis-flow-approval-buttons">
              <button
                type="button"
                className="orbis-btn-approve"
                onClick={() => resolveApproval(true, 'Approved via Live Flow')}
              >
                Authorize
              </button>
              <button
                type="button"
                className="orbis-btn-deny"
                onClick={() => resolveApproval(false, 'Denied via Live Flow')}
              >
                Deny
              </button>
            </div>
          </div>
        </FluidGlass>
      )}

      {/* Chronological Event Stream */}
      <div className="orbis-flow-stream-section">
        <div className="orbis-flow-stream-header">
          <span>Agent Event Stream</span>
          <span className="orbis-flow-stream-status">
            {gatewayOnline ? '● WebSocket Connected' : '○ Gateway Offline'}
          </span>
        </div>

        {events.length === 0 ? (
          <div className="orbis-flow-empty">
            <p>No active execution events in current buffer. Issue a command to observe real-time agent operations.</p>
          </div>
        ) : (
          <div className="orbis-flow-events-list">
            {events.map((ev, idx) => (
              <div key={ev.event_id || idx} className="orbis-flow-event-row">
                <div className="orbis-flow-event-time">
                  {ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString() : '--:--:--'}
                </div>
                <div className="orbis-flow-event-badge-cell">
                  <span className="orbis-event-type-badge">{ev.event_type}</span>
                </div>
                {ev.risk_level && (
                  <span className={`orbis-risk-pill risk-${ev.risk_level}`}>
                    {ev.risk_level}
                  </span>
                )}
                <div className="orbis-flow-event-message">
                  {ev.safe_user_message || ev.tool_name || ev.status || 'Event recorded'}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Contextual Quick Actions Dock (Fixed at bottom center) */}
      <Dock
        items={dockItems}
        magnification={52}
        distance={110}
        panelHeight={48}
        baseItemSize={36}
        className="orbis-contextual-dock"
      />

    </div>
  );
}

function isSubmittingTask(status) {
  return ['SUBMITTING', 'ACCEPTED', 'PLANNING', 'RUNNING'].includes(status);
}

export default LiveFlowView;

