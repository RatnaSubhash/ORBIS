/**
 * RestorationView — Declarative Multi-Application Workspace Restoration Center (Phase 10 & 11).
 *
 * Implements full-fidelity workspace reconstruction:
 *   - Live observed candidate sessions with application and resource discovery
 *   - Preflight plan generator with canonical path containment and executable allowlists
 *   - PID-bound multi-monitor window layout placement inspector
 *   - Granular step selector allowing partial or full workspace execution
 *   - Failure isolation with independent post-action verification reports
 *   - Genuine Windows native process foreground activation
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useAgent } from '../../state/AgentContext';
import agentApi from '../../services/api';
import FluidGlass from '../ui/FluidGlass';
import './RestorationView.css';

// Application icon / glyph map
const APP_GLYPHS = {
  vscode: '📝',
  antigravity: '⚛️',
  terminal: '💻',
  chrome: '🌐',
  edge: '🧭',
  notepad: '📄',
  explorer: '📁',
  spotify: '🎵',
  calculator: '🔢',
};

export default function RestorationView() {
  const { gatewayOnline, executeTaskPrompt, isSubmitting } = useAgent();

  const [sessions, setSessions] = useState([]);
  const [isLoadingSessions, setIsLoadingSessions] = useState(false);
  const [selectedSessionId, setSelectedSessionId] = useState(null);

  const [plan, setPlan] = useState(null);
  const [isLoadingPlan, setIsLoadingPlan] = useState(false);
  const [selectedStepIds, setSelectedStepIds] = useState(new Set());

  const [naturalQuery, setNaturalQuery] = useState('');
  const [isExecuting, setIsExecuting] = useState(false);
  const [executionResult, setExecutionResult] = useState(null);
  const [error, setError] = useState(null);

  // 1. Fetch Candidate Restorable Sessions
  const loadSessions = useCallback(async () => {
    if (!gatewayOnline) return;
    setIsLoadingSessions(true);
    setError(null);
    try {
      const res = await agentApi.getRestorationSessions(12);
      const list = res.sessions || [];
      setSessions(list);
      // Auto-select latest session if none selected
      if (list.length > 0 && !selectedSessionId) {
        handleSelectSession(list[0]);
      }
    } catch (err) {
      console.error('Failed to load restoration sessions:', err);
      setError(err.message || 'Could not query candidate sessions.');
    } finally {
      setIsLoadingSessions(false);
    }
  }, [gatewayOnline, selectedSessionId]);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  // 2. Fetch / Preflight Restoration Plan for Selected Session
  const handleSelectSession = useCallback(async (session) => {
    if (!session?.session_id) return;
    setSelectedSessionId(session.session_id);
    setIsLoadingPlan(true);
    setError(null);
    setExecutionResult(null);

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
        setError(res.message || 'No valid restoration plan could be constructed.');
      }
    } catch (err) {
      console.error('Failed to preview restoration plan:', err);
      setError(err.message || 'Failed to generate plan.');
      setPlan(null);
    } finally {
      setIsLoadingPlan(false);
    }
  }, []);

  // 3. Handle Custom Natural Language Query Plan Generation
  const handleGenerateFromQuery = async (e) => {
    if (e) e.preventDefault();
    if (!naturalQuery.trim()) return;

    setIsLoadingPlan(true);
    setError(null);
    setExecutionResult(null);
    setSelectedSessionId(null);

    try {
      const res = await agentApi.previewRestorationPlan({
        query: naturalQuery.trim(),
      });
      if (res.plan) {
        setPlan(res.plan);
        const validIds = new Set(
          res.plan.steps
            .filter((s) => s.status !== 'SKIPPED_INVALID')
            .map((s) => s.step_id)
        );
        setSelectedStepIds(validIds);
      } else {
        setPlan(null);
        setError(res.message || 'No workspace session matched your description.');
      }
    } catch (err) {
      setError(err.message || 'Plan generation failed.');
      setPlan(null);
    } finally {
      setIsLoadingPlan(false);
    }
  };

  // 4. Toggle Step Checkbox
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

  // 5. Execute Plan
  const handleExecuteRestoration = async (dryRun = false) => {
    if (!plan) return;
    setIsExecuting(true);
    setError(null);
    setExecutionResult(null);

    try {
      const stepIdsList = Array.from(selectedStepIds);
      const res = await agentApi.executeRestorationPlan({
        sessionId: plan.session_id,
        selectedStepIds: stepIdsList,
        approved: true,
      });
      setExecutionResult(res);
    } catch (err) {
      console.error('Restoration execution error:', err);
      setError(err.message || 'Restoration execution encountered an error.');
    } finally {
      setIsExecuting(false);
    }
  };

  // Format relative timestamp
  const formatSessionTime = (iso) => {
    if (!iso) return 'Recent';
    try {
      const d = new Date(iso);
      const now = new Date();
      const diffMs = now - d;
      const diffMins = Math.floor(diffMs / 60000);
      if (diffMins < 1) return 'Just now';
      if (diffMins < 60) return `${diffMins}m ago`;
      const diffHours = Math.floor(diffMins / 60);
      if (diffHours < 24) return `${diffHours}h ago`;
      return d.toLocaleDateString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch {
      return iso;
    }
  };

  const executableStepCount = plan ? plan.steps.filter((s) => selectedStepIds.has(s.step_id)).length : 0;

  return (
    <div className="orbis-restoration-workspace">
      {/* ── View Header ── */}
      <div className="orbis-restoration-header">
        <div className="orbis-restoration-title-group">
          <h2>
            <span>⚡</span> Workspace Restoration
          </h2>
          <span className="orbis-restoration-sub">
            Declarative Multi-Application Layout & Context Reconstruction with Preflight Verification
          </span>
        </div>

        <div className="orbis-restoration-header-actions">
          <button
            type="button"
            className="orbis-restoration-btn secondary"
            onClick={loadSessions}
            disabled={isLoadingSessions}
            title="Refresh candidate sessions"
          >
            {isLoadingSessions ? 'Scanning…' : '↻ Refresh Sessions'}
          </button>

          <button
            type="button"
            className="orbis-restoration-btn primary"
            disabled={isSubmitting || !gatewayOnline}
            onClick={() => executeTaskPrompt('Continue where I left off', false)}
            title="Automatically infer intent and restore latest active workspace"
          >
            {isSubmitting ? 'Restoring…' : '⚡ Autonomous Resume ↵'}
          </button>
        </div>
      </div>

      {/* ── Natural Query Bar ── */}
      <form onSubmit={handleGenerateFromQuery} className="orbis-restoration-prompt-bar">
        <input
          type="text"
          className="orbis-restoration-input"
          placeholder="Describe what to restore (e.g. 'Restore my VS Code and Edge research from this afternoon') or select a session below…"
          value={naturalQuery}
          onChange={(e) => setNaturalQuery(e.target.value)}
        />
        <button
          type="submit"
          className="orbis-restoration-btn primary"
          style={{ padding: '6px 16px', fontSize: '0.8rem' }}
          disabled={isLoadingPlan || !naturalQuery.trim()}
        >
          {isLoadingPlan ? 'Analyzing…' : 'Plan Workspace ↵'}
        </button>
      </form>

      {/* ── Error Banner ── */}
      {error && (
        <div className="orbis-step-error-msg" style={{ margin: '0' }}>
          <strong>Notice:</strong> {error}
        </div>
      )}

      {/* ── Master/Detail Grid ── */}
      <div className="orbis-restoration-grid">
        {/* ── Left Column: Candidate Sessions ── */}
        <div className="orbis-restoration-sessions-col">
          <div className="orbis-sessions-col-header">
            <span>Observed Sessions</span>
            <span className="orbis-badge-count">{sessions.length} candidates</span>
          </div>

          <div className="orbis-sessions-list">
            {sessions.map((s) => {
              const isSelected = selectedSessionId === s.session_id;
              return (
                <div
                  key={s.session_id}
                  className={`orbis-session-card ${isSelected ? 'selected' : ''}`}
                  onClick={() => handleSelectSession(s)}
                >
                  <div className="orbis-session-card-header">
                    <span className="orbis-session-time">{formatSessionTime(s.start_time)}</span>
                    {s.project_name && (
                      <span className="orbis-session-project">📁 {s.project_name}</span>
                    )}
                  </div>

                  <div className="orbis-session-apps-row">
                    {s.applications.map((app, idx) => (
                      <span
                        key={idx}
                        className={`orbis-app-pill ${app.supported ? 'supported' : ''}`}
                        title={app.process_name}
                      >
                        <span>{APP_GLYPHS[app.identifier] || '📦'}</span>
                        {app.display_name}
                      </span>
                    ))}
                  </div>

                  <div className="orbis-session-foot">
                    <span>
                      {s.resources?.length > 0
                        ? `${s.resources.length} target${s.resources.length > 1 ? 's' : ''}`
                        : 'Pure layout'}
                    </span>
                    <span>Confidence {Math.round((s.confidence || 1) * 100)}%</span>
                  </div>
                </div>
              );
            })}

            {sessions.length === 0 && !isLoadingSessions && (
              <div style={{ textAlign: 'center', padding: '30px 10px', color: '#71717a' }}>
                No recorded workspace sessions found.
              </div>
            )}
          </div>
        </div>

        {/* ── Right Column: Preflight Plan & Execution Console ── */}
        <div className="orbis-restoration-plan-col">
          {isLoadingPlan ? (
            <div style={{ textAlign: 'center', padding: '60px 20px', color: '#33ccdd' }}>
              <div style={{ fontSize: '1.2rem', marginBottom: '8px' }}>⚙</div>
              <div>Generating preflight-validated restoration plan…</div>
            </div>
          ) : plan ? (
            <>
              {/* Plan Metadata Card */}
              <FluidGlass borderRadius={12} className="orbis-glass-card-wrapper">
                <div className="orbis-plan-meta-card">
                  <div className="orbis-plan-meta-header">
                    <div>
                      <h3 className="orbis-plan-goal">{plan.goal_description}</h3>
                      <span style={{ fontSize: '0.72rem', color: '#71717a', fontFamily: 'monospace' }}>
                        Session: {plan.session_id}
                      </span>
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

                      {plan.requires_user_approval && (
                        <span className="orbis-status-badge approval">Approval Enforced</span>
                      )}
                    </div>
                  </div>

                  {plan.preflight_errors?.length > 0 && (
                    <div className="orbis-step-error-msg">
                      <strong>Preflight Safety Isolation:</strong>
                      <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
                        {plan.preflight_errors.map((err, i) => (
                          <li key={i}>{err}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              </FluidGlass>

              {/* Declarative Steps List */}
              <div className="orbis-steps-container">
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    fontSize: '0.78rem',
                    color: '#a1a1aa',
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.08em',
                  }}
                >
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
                            <span>{step.display_name}</span>
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

                        {step.target_value && (
                          <div className="orbis-step-target-row">
                            <span>Target:</span>
                            <span className="orbis-target-badge">{step.target_value}</span>
                            <span>({step.target_type})</span>
                          </div>
                        )}

                        {step.window_bounds && (
                          <div className="orbis-step-bounds-row">
                            📐 Window Geometry: {step.window_bounds.width}x{step.window_bounds.height} at (
                            {step.window_bounds.x}, {step.window_bounds.y}) •{' '}
                            {step.window_placement || 'Normal'}
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

              {/* Execution Actions Bar */}
              <div className="orbis-restoration-actions-bar">
                <button
                  type="button"
                  className="orbis-restoration-btn primary"
                  disabled={isExecuting || executableStepCount === 0}
                  onClick={() => handleExecuteRestoration(false)}
                >
                  {isExecuting
                    ? 'Restoring Applications & Window Layout…'
                    : `Execute Workspace Restoration (${executableStepCount} App${
                        executableStepCount !== 1 ? 's' : ''
                      }) ↵`}
                </button>
              </div>

              {/* Live Execution Result Card */}
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
            </>
          ) : (
            <div style={{ textAlign: 'center', padding: '60px 20px', color: '#71717a' }}>
              Select a recorded session on the left or enter a prompt above to construct a restoration plan.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
