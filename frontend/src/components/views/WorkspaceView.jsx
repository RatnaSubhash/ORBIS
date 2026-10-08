/**
 * WorkspaceView — Workspace Continuity & Declarative Restoration Center.
 *
 * Clean, subtle, dark obsidian aesthetic matching user specifications:
 *   - Current Context DNA status & reconstructed intent
 *   - Knowledge Graph & Semantic Topology 4-stat grid & motto
 *   - Observed Resources explorer (Folder)
 *   - Detected Workspace Sessions list with exact time range and duration
 *   - Integrated Preflight Restoration Console with selectable steps & verified execution
 *   - Continuous passive recording and live 3-second heartbeat telemetry
 */

import React, { useState, useCallback, useMemo, useEffect } from 'react';
import { useAgent } from '../../state/AgentContext';
import agentApi from '../../services/api';
import Folder from '../ui/Folder';
import AnimatedList from '../ui/AnimatedList';

// Application icon / glyph map
const APP_GLYPHS = {
  vscode: '📝',
  code: '📝',
  antigravity: '⚛️',
  terminal: '💻',
  powershell: '💻',
  cmd: '💻',
  chrome: '🌐',
  edge: '🧭',
  msedge: '🧭',
  notepad: '📄',
  explorer: '📁',
  spotify: '🎵',
  calculator: '🔢',
  calc: '🔢',
};

function getAppGlyph(app) {
  const id = (typeof app === 'string' ? app : app.identifier || app.process_name || '').toLowerCase();
  for (const [key, glyph] of Object.entries(APP_GLYPHS)) {
    if (id.includes(key)) return glyph;
  }
  return '📦';
}

function getAppDisplayName(app) {
  if (typeof app === 'string') return app;
  return app.display_name || app.process_name || app.identifier || 'Application';
}

export function WorkspaceView() {
  const {
    continuityContext,
    isLoadingContinuity,
    refreshContinuity,
    gatewayOnline,
  } = useAgent();

  // Tab mode in right column: 'restoration' (plan & preflight) or 'events' (raw audit trail)
  const [activeTab, setActiveTab] = useState('restoration');

  // Candidate sessions & selection
  const [selectedSessionId, setSelectedSessionId] = useState(null);
  const [sessionEvents, setSessionEvents] = useState([]);
  const [isLoadingEvents, setIsLoadingEvents] = useState(false);
  const [eventError, setEventError] = useState(null);

  // Restoration plan & execution state
  const [plan, setPlan] = useState(null);
  const [isLoadingPlan, setIsLoadingPlan] = useState(false);
  const [selectedStepIds, setSelectedStepIds] = useState(new Set());
  const [isExecuting, setIsExecuting] = useState(false);
  const [executionResult, setExecutionResult] = useState(null);
  const [planError, setPlanError] = useState(null);

  const observation = continuityContext?.observation;
  const currentDna = continuityContext?.current_dna;
  const sessions = continuityContext?.recent_sessions || [];
  const kg = continuityContext?.knowledge_graph;

  // Handle selecting a session: loads preflight restoration plan and raw activity events
  const handleSelectSession = useCallback(async (session) => {
    if (!session?.session_id) return;
    setSelectedSessionId(session.session_id);
    setIsLoadingPlan(true);
    setIsLoadingEvents(true);
    setPlanError(null);
    setEventError(null);
    setExecutionResult(null);

    // 1. Fetch preflight restoration plan
    try {
      const res = await agentApi.previewRestorationPlan({
        sessionId: session.session_id,
        query: `Restore session ${session.session_id}`,
      });
      if (res.plan) {
        setPlan(res.plan);
        // Select all valid steps by default
        const validIds = new Set(
          res.plan.steps
            .filter((s) => s.status !== 'SKIPPED_INVALID')
            .map((s) => s.step_id)
        );
        setSelectedStepIds(validIds);
      } else {
        setPlan(null);
        if (res.message) setPlanError(res.message);
      }
    } catch (err) {
      console.error('Failed to preview restoration plan:', err);
      setPlan(null);
      setPlanError(err.message || 'Failed to construct preflight restoration plan.');
    } finally {
      setIsLoadingPlan(false);
    }

    // 2. Fetch raw session events
    try {
      const res = await agentApi.getSessionEvents(session.session_id);
      setSessionEvents(res.events || []);
    } catch (err) {
      setEventError(err.message || 'Failed to load session events');
      setSessionEvents([]);
    } finally {
      setIsLoadingEvents(false);
    }
  }, []);

  // Auto-select latest session if none selected and sessions exist
  useEffect(() => {
    if (sessions.length > 0 && !selectedSessionId) {
      handleSelectSession(sessions[0]);
    }
  }, [sessions, selectedSessionId, handleSelectSession]);

  // Toggle step selection checkbox
  const toggleStep = (stepId) => {
    setSelectedStepIds((prev) => {
      const next = new Set(prev);
      if (next.has(stepId)) {
        next.delete(stepId);
      } else {
        next.add(stepId);
      }
      return next;
    });
  };

  // Execute the selected restoration plan steps
  const handleExecuteRestoration = async () => {
    if (!plan || selectedStepIds.size === 0) return;
    setIsExecuting(true);
    setPlanError(null);
    setExecutionResult(null);

    try {
      const stepIdsList = Array.from(selectedStepIds);
      const res = await agentApi.executeRestorationPlan({
        sessionId: plan.session_id,
        selectedStepIds: stepIdsList,
        approved: true,
      });
      setExecutionResult(res);
      // Refresh continuity to reflect newly activated processes
      refreshContinuity();
    } catch (err) {
      console.error('Restoration execution error:', err);
      setPlanError(err.message || 'Restoration execution encountered an error.');
    } finally {
      setIsExecuting(false);
    }
  };

  // Aggregate observed resources across Context DNA and sessions
  const observedResources = useMemo(() => {
    const set = new Set();
    if (currentDna?.resources_accessed) {
      currentDna.resources_accessed.forEach((r) => r && set.add(r));
    }
    sessions.forEach((s) => {
      if (Array.isArray(s.resources)) {
        s.resources.forEach((r) => r && set.add(r));
      }
    });
    return Array.from(set);
  }, [currentDna, sessions]);

  // Folder component items (top 3 resources)
  const folderItems = useMemo(() => {
    if (observedResources.length === 0) {
      return ['No resources', 'Observed yet', 'Ready'];
    }
    return observedResources.slice(0, 3).map((res) => {
      const parts = res.split(/[\\/]/);
      return parts[parts.length - 1] || res;
    });
  }, [observedResources]);

  const selectedSession = useMemo(() => {
    return (
      sessions.find((s) => s.session_id === selectedSessionId) ||
      (sessions.length > 0 ? sessions[0] : null)
    );
  }, [sessions, selectedSessionId]);

  // Clear date & time formatters
  const formatEventDateTime = (iso) => {
    if (!iso) return '—';
    try {
      const d = new Date(iso);
      const datePart = d.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
      const timePart = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      return `${datePart} • ${timePart}`;
    } catch {
      return iso;
    }
  };

  const formatSessionRange = (startIso, endIso) => {
    if (!startIso) return 'Recent';
    try {
      const dStart = new Date(startIso);
      const datePart = dStart.toLocaleDateString([], {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      });
      const startStr = dStart.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });
      if (!endIso) return `${datePart} • ${startStr}`;
      const dEnd = new Date(endIso);
      const endStr = dEnd.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });
      return `${datePart} • ${startStr} – ${endStr}`;
    } catch {
      return startIso;
    }
  };

  const formatDuration = (secs) => {
    if (typeof secs !== 'number' || isNaN(secs)) return '0s';
    if (secs < 60) return `${Math.round(secs)}s`;
    const mins = Math.floor(secs / 60);
    const remSecs = Math.round(secs % 60);
    return `${mins}m ${remSecs}s`;
  };

  const executableStepCount = plan
    ? plan.steps.filter((s) => selectedStepIds.has(s.step_id)).length
    : 0;

  return (
    <div className="orbis-workspace-view">
      {/* ── Section A: Top Telemetry & Header Controls ── */}
      <div className="orbis-workspace-header">
        <div className="orbis-workspace-title-group">
          <span className="orbis-workspace-badge">CONTINUITY INTELLIGENCE</span>
          <h2 className="orbis-workspace-title">Workspace Memory & Context Reconstruction</h2>
        </div>

        <div className="orbis-workspace-controls">
          <div className="orbis-obs-pill">
            <span
              className={`orbis-obs-dot ${
                gatewayOnline && observation?.active ? 'active' : 'standby'
              }`}
            />
            <span className="orbis-obs-text">
              {gatewayOnline && observation?.active
                ? `OBSERVING (${observation.polling_interval}s heartbeat)`
                : gatewayOnline
                ? 'STANDBY'
                : 'GATEWAY OFFLINE'}
            </span>
          </div>

          <div className="orbis-stat-pill" title="Total workspace events recorded continuously in SQLite">
            <span className="stat-label">Events:</span>
            <span className="stat-value">{observation?.total_events ?? 0}</span>
          </div>

          <button
            type="button"
            className="orbis-refresh-btn"
            onClick={refreshContinuity}
            disabled={isLoadingContinuity || !gatewayOnline}
            title="Refresh continuity telemetry from backend"
          >
            {isLoadingContinuity ? 'SYNCING…' : 'REFRESH'}
          </button>
        </div>
      </div>

      {/* ── Master 2-Column Grid ── */}
      <div className="orbis-workspace-grid">
        {/* ── Left Column: Context DNA + Knowledge Graph + Folder ── */}
        <div className="orbis-workspace-col">
          {/* Section B: Current Context DNA Card */}
          <div className="orbis-dna-wrapper">
            <div className="orbis-card-label">CURRENT CONTEXT DNA</div>
            <div className="orbis-clean-card orbis-dna-card">
              {currentDna ? (
                <>
                  <div className="orbis-dna-header">
                    <div className="orbis-dna-identity">
                      <span className="dna-chip">DNA v{currentDna.version || '1.0'}</span>
                      <span className="dna-freshness">{currentDna.freshness?.toUpperCase() || 'PERSISTED'}</span>
                    </div>
                  </div>

                  <div className="orbis-dna-task">
                    <span className="task-sub">RECONSTRUCTED INTENT</span>
                    <h3 className="task-desc">
                      {currentDna.task_summary || 'Active desktop development & research workflow'}
                    </h3>
                  </div>

                  <div className="orbis-dna-details">
                    <div className="dna-detail-row">
                      <span className="k">Project:</span>
                      <span className="v font-mono">{currentDna.project_root || 'ORBIS'}</span>
                    </div>

                    <div className="dna-detail-row">
                      <span className="k">Primary App:</span>
                      <span className="v">{currentDna.primary_application || 'Antigravity IDE.exe'}</span>
                    </div>

                    <div className="dna-detail-row">
                      <span className="k">Apps:</span>
                      <div className="dna-tags">
                        {(currentDna.active_applications && currentDna.active_applications.length > 0
                          ? currentDna.active_applications
                          : ['Antigravity IDE.exe', 'chrome.exe']
                        ).map((app) => (
                          <span key={app} className="dna-tag">
                            {app}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                </>
              ) : (
                <div className="orbis-dna-empty">
                  <span className="empty-title">NO ACTIVE CONTEXT YET</span>
                  <p className="empty-msg">
                    ORBIS passive observation is active. Context DNA will appear as application focus settles.
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* Section C: Knowledge Graph & Semantic Topology */}
          <div className="orbis-kg-wrapper">
            <div className="orbis-card-label">KNOWLEDGE GRAPH & SEMANTIC TOPOLOGY</div>
            <div className="orbis-clean-card orbis-kg-card">
              <div className="orbis-kg-stats-grid">
                <div className="orbis-kg-stat">
                  <span className="kg-num">{kg?.total_nodes ?? 0}</span>
                  <span className="kg-label">NODES</span>
                </div>
                <div className="orbis-kg-stat">
                  <span className="kg-num">{kg?.total_edges ?? 0}</span>
                  <span className="kg-label">EDGES</span>
                </div>
                <div className="orbis-kg-stat">
                  <span className="kg-num">{kg?.application_count ?? 0}</span>
                  <span className="kg-label">APPS</span>
                </div>
                <div className="orbis-kg-stat">
                  <span className="kg-num">{kg?.file_count ?? 0}</span>
                  <span className="kg-label">FILES</span>
                </div>
              </div>

              <div className="orbis-kg-motto">
                ORBIS connects active applications, touched files, temporal sessions, and reconstructed
                intents into factual, explainable memory.
              </div>

              {/* Section D: Observed Resources (Folder) */}
              <div className="orbis-folder-section">
                <div className="orbis-folder-desc">
                  <span className="folder-title">OBSERVED RESOURCES ({observedResources.length || 1})</span>
                  <span className="folder-sub">Click folder to reveal tracked files</span>
                </div>
                <div className="orbis-folder-container">
                  <Folder
                    color="#1a2230"
                    size={0.85}
                    items={folderItems}
                    className="orbis-workspace-folder"
                  />
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* ── Right Column: Sessions & Restoration Console ── */}
        <div className="orbis-workspace-col">
          <div className="orbis-clean-card orbis-sessions-panel">
            <div className="orbis-sessions-header">
              <div className="orbis-card-label">
                DETECTED WORKSPACE SESSIONS ({sessions.length})
              </div>
              <span className="sessions-sub">
                Click a session to inspect raw sanitized events or execute restoration
              </span>
            </div>

            {/* Segmented Session Cards List */}
            {sessions.length > 0 ? (
              <AnimatedList
                items={sessions}
                onItemSelect={handleSelectSession}
                className="orbis-session-list"
                itemClassName="orbis-session-item-wrap"
                renderItem={(sess) => {
                  const isSelected = selectedSessionId === sess.session_id;
                  const appsList = Array.isArray(sess.applications) ? sess.applications : [];
                  return (
                    <div
                      className={`orbis-session-card ${isSelected ? 'selected active-session' : ''}`}
                    >
                      <div className="orbis-session-card-header">
                        <span className="orbis-session-time">
                          {formatSessionRange(sess.start_time, sess.end_time)}
                        </span>
                        <span className="session-duration">
                          {formatDuration(sess.duration_seconds)}
                        </span>
                      </div>

                      <div className="orbis-session-apps-row">
                        {appsList.slice(0, 4).map((app, idx) => (
                          <span key={idx} className="orbis-app-pill">
                            {getAppDisplayName(app)}
                          </span>
                        ))}
                        {appsList.length > 4 && (
                          <span className="orbis-app-pill">+{appsList.length - 4}</span>
                        )}
                      </div>

                      <div className="orbis-session-foot">
                        <span>{sess.event_count ?? 0} events</span>
                        {typeof sess.confidence === 'number' && (
                          <span>{Math.round(sess.confidence * 100)}% conf</span>
                        )}
                      </div>
                    </div>
                  );
                }}
              />
            ) : (
              <div className="orbis-sessions-empty">
                <p>No workspace sessions segmented yet.</p>
                <span>Sessions will appear here as windows and focus changes occur.</span>
              </div>
            )}

            {/* ── Tabs for Selected Session: Restoration Plan vs Activity Events ── */}
            <div className="orbis-workspace-subnav">
              <button
                type="button"
                className={`orbis-subnav-btn ${activeTab === 'restoration' ? 'active' : ''}`}
                onClick={() => setActiveTab('restoration')}
              >
                Restoration Plan
              </button>
              <button
                type="button"
                className={`orbis-subnav-btn ${activeTab === 'events' ? 'active' : ''}`}
                onClick={() => setActiveTab('events')}
              >
                Session Events ({sessionEvents.length})
              </button>
            </div>

            {/* ── Sub-Panel: Restoration Plan Console ── */}
            {activeTab === 'restoration' && (
              <div className="orbis-restoration-console">
                {isLoadingPlan ? (
                  <div className="orbis-plan-loading">
                    <div className="spinner-icon">⚙</div>
                    <div>Generating preflight-validated restoration plan…</div>
                  </div>
                ) : plan ? (
                  <div className="orbis-plan-body">
                    {/* Plan Header */}
                    <div className="orbis-plan-meta-header">
                      <div>
                        <div className="orbis-plan-goal">{plan.goal_description}</div>
                        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '3px', flexWrap: 'wrap' }}>
                          <span style={{ fontSize: '0.74rem', color: '#33ccdd', fontWeight: 600 }}>
                            📅 {selectedSession ? formatSessionRange(selectedSession.start_time, selectedSession.end_time) : 'Active Session'}
                          </span>
                          <span className="orbis-plan-sid">
                            ID: {plan.session_id?.slice(0, 16)}…
                          </span>
                        </div>
                      </div>

                      <div className="orbis-plan-badges">
                        <span
                          className={`orbis-status-badge ${
                            plan.overall_risk === 'LOW'
                              ? 'risk-low'
                              : plan.overall_risk === 'MEDIUM'
                              ? 'risk-medium'
                              : 'warning'
                          }`}
                        >
                          {plan.overall_risk} RISK
                        </span>

                        {plan.is_preflight_valid ? (
                          <span className="orbis-status-badge valid">✓ Preflight Validated</span>
                        ) : (
                          <span className="orbis-status-badge warning">Preflight Notice</span>
                        )}
                      </div>
                    </div>

                    {plan.preflight_errors?.length > 0 && (
                      <div className="orbis-step-error-msg">
                        <strong>Preflight Safety Notice:</strong>
                        <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
                          {plan.preflight_errors.map((err, i) => (
                            <li key={i}>{err}</li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {/* Step Cards with App Selection Checkboxes */}
                    <div className="orbis-steps-container">
                      <div className="orbis-steps-header-row">
                        <span>Restoration Sequence ({plan.steps.length} Steps)</span>
                        <span>Select apps to restore</span>
                      </div>

                      {plan.steps.map((step) => {
                        const isChecked = selectedStepIds.has(step.step_id);
                        const isSkipped = step.status === 'SKIPPED_INVALID';
                        return (
                          <div
                            key={step.step_id}
                            className={`orbis-step-card ${isSkipped ? 'skipped' : ''}`}
                          >
                            <input
                              type="checkbox"
                              className="orbis-step-checkbox"
                              checked={isChecked && !isSkipped}
                              disabled={isSkipped || isExecuting}
                              onChange={() => toggleStep(step.step_id)}
                            />

                            <div className="orbis-step-content">
                              <div className="orbis-step-top">
                                <div className="orbis-step-app-name">
                                  <span>{APP_GLYPHS[step.app_identifier] || '📦'}</span>
                                  <span>{getAppDisplayName(step.app_identifier)}</span>
                                  <span className="orbis-step-app-exe">
                                    {step.command_executable ? `(${step.command_executable})` : ''}
                                  </span>
                                </div>

                                <div>
                                  {isSkipped ? (
                                    <span className="orbis-status-badge warning">Skipped Safely</span>
                                  ) : (
                                    <span className="orbis-status-badge valid">Ready</span>
                                  )}
                                </div>
                              </div>

                              {step.resource?.raw_target && (
                                <div className="orbis-step-target-row">
                                  <span>Target:</span>
                                  <span className="orbis-target-badge">{step.resource.raw_target}</span>
                                  <span>({step.resource.target_type})</span>
                                </div>
                              )}

                              {step.target_window_state?.bounds && (
                                <div className="orbis-step-bounds-row">
                                  📐 Layout: {step.target_window_state.bounds.width}x{step.target_window_state.bounds.height} at (
                                  {step.target_window_state.bounds.x}, {step.target_window_state.bounds.y}) •{' '}
                                  {step.target_window_state.placement || 'Normal'}
                                </div>
                              )}

                              {step.status_message && (
                                <div className="orbis-step-error-msg">{step.status_message}</div>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>

                    {/* Execution Action Button */}
                    <div className="orbis-restoration-actions-bar">
                      <button
                        type="button"
                        className="orbis-restoration-btn primary"
                        style={{ width: '100%', justifyContent: 'center' }}
                        disabled={isExecuting || executableStepCount === 0}
                        onClick={handleExecuteRestoration}
                      >
                        {isExecuting
                          ? 'Restoring Applications & Window Layout…'
                          : `Execute Workspace Restoration (${executableStepCount} App${
                              executableStepCount !== 1 ? 's' : ''
                            }) ↵`}
                      </button>
                    </div>

                    {/* Live Execution Result Verification Card */}
                    {executionResult && (
                      <div
                        className={`orbis-restoration-result-card ${
                          executionResult.success ? 'success' : ''
                        }`}
                      >
                        <div className="orbis-result-summary-text">
                          {executionResult.success ? '✓ ' : 'ℹ '}
                          {executionResult.summary}
                        </div>

                        <div className="orbis-verification-items">
                          {Object.entries(executionResult.step_results || {}).map(([sid, res]) => (
                            <div key={sid} className="orbis-verif-line">
                              <span>{res.launched ? '🟢' : '🔴'}</span>
                              <strong style={{ color: '#ffffff' }}>{res.app_identifier}:</strong>
                              <span>
                                {res.launched
                                  ? `PID ${res.process_id} spawned • Window placed: ${
                                      res.window_placed ? 'Yes' : 'Preserved'
                                    } (${Math.round(res.execution_time_ms)}ms)`
                                  : `Launch failed (${res.error})`}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="orbis-plan-empty">
                    Select a recorded workspace session above to construct a preflight plan.
                  </div>
                )}
              </div>
            )}

            {/* ── Sub-Panel: Raw Activity Events Drawer ── */}
            {activeTab === 'events' && (
              <div className="orbis-session-events-drawer">
                <div className="drawer-header">
                  <div>
                    <span className="drawer-title">
                      Session Activity Log ({sessionEvents.length} events)
                    </span>
                    {selectedSession && (
                      <div style={{ fontSize: '0.7rem', color: '#94a3b8', marginTop: '2px' }}>
                        📅 Recorded: {formatSessionRange(selectedSession.start_time, selectedSession.end_time)}
                      </div>
                    )}
                  </div>
                </div>

                {isLoadingEvents ? (
                  <div className="drawer-loading">Loading sanitized session events…</div>
                ) : eventError ? (
                  <div className="drawer-error">{eventError}</div>
                ) : sessionEvents.length === 0 ? (
                  <div className="drawer-empty">No events found for this session.</div>
                ) : (
                  <div className="drawer-event-stream">
                    {sessionEvents.map((ev) => (
                      <div key={ev.event_id} className="drawer-event-row">
                        <span className="ev-time" style={{ minWidth: '150px' }}>
                          {formatEventDateTime(ev.timestamp)}
                        </span>
                        <span className="ev-proc">{ev.process_name || 'System'}</span>
                        <span className="ev-title" title={ev.window_title}>
                          {ev.window_title || ev.resource_path || 'Window activity'}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default WorkspaceView;
