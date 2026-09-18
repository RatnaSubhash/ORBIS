/**
 * OrbisCore — the central ORBIS identity component.
 *
 * Owns:
 *   - ORBIS operational state (idle / listening / thinking / responding / executing)
 *   - Gateway connectivity awareness
 *   - Task submission and real-time event stream
 *   - The OrbisGlobe (the Globe IS ORBIS, not a decoration)
 *
 * Children receive derived state only; they do not talk to the gateway directly.
 *
 * Architecture:
 *   OrbisCore
 *     └─ OrbisGlobe          (visual state mapping)
 *          └─ GlobePulse     (COBE WebGL globe)
 *   OrbisCore
 *     └─ prompt input / status bar  (minimal, below the globe)
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import {
  checkGatewayHealth,
  submitTask,
  getTaskStatus,
  connectTaskWebSocket,
} from '../api/gateway';
import { OrbisGlobe } from './core/OrbisGlobe';
import {
  STATUS_LABELS,
  resolveVisualState,
  TaskSettleTimer,
} from '../state/visualState';

// ─── Component ────────────────────────────────────────────────────────────────

export function OrbisCore() {
  // Gateway / task state
  const [prompt, setPrompt] = useState('');
  const [currentTaskId, setCurrentTaskId] = useState(null);
  const [taskStatus, setTaskStatus] = useState('IDLE');
  const [taskSummary, setTaskSummary] = useState('');
  const [events, setEvents] = useState([]);
  const [pendingApproval, setPendingApproval] = useState(null);
  const [gatewayOnline, setGatewayOnline] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState(null);

  // Visual presentation state (subordinate to AgentEvent lifecycle)
  const [visualState, setVisualState] = useState('idle');

  // Interaction state
  const [inputFocused, setInputFocused] = useState(false);

  // Refs for task boundaries and deduplication
  const seenEventIdsRef = useRef(new Set());
  const wsCleanupRef = useRef(null);
  const currentTaskIdRef = useRef(null);
  const visualStateRef = useRef('idle');
  const settleTimerRef = useRef(null);

  // Keep ref synchronized for immediate callback checks
  visualStateRef.current = visualState;

  // Derive the ORBIS visual state (falls back to idle if gateway is offline)
  const orbisState = gatewayOnline ? visualState : 'idle';

  // ── Gateway health polling ────────────────────────────────────────────────
  useEffect(() => {
    let isMounted = true;
    async function verifyHealth() {
      try {
        await checkGatewayHealth();
        if (isMounted) setGatewayOnline(true);
      } catch {
        if (isMounted) setGatewayOnline(false);
      }
    }
    verifyHealth();
    const interval = setInterval(verifyHealth, 5000);
    return () => { isMounted = false; clearInterval(interval); };
  }, []);

  // ── Task settle timer initialization and unmount cleanup ──────────────────
  useEffect(() => {
    settleTimerRef.current = new TaskSettleTimer({
      onSettle: (settledTaskId) => {
        // Strict task check: only settle if active task still matches the settled task
        if (currentTaskIdRef.current === settledTaskId || !currentTaskIdRef.current) {
          setVisualState('idle');
        }
      },
      delay: 1500,
    });

    return () => {
      if (settleTimerRef.current) {
        settleTimerRef.current.cancel();
      }
    };
  }, []);

  // ── WebSocket cleanup on unmount ──────────────────────────────────────────
  useEffect(() => {
    return () => {
      if (wsCleanupRef.current) {
        wsCleanupRef.current();
        wsCleanupRef.current = null;
      }
    };
  }, []);

  // ── Task submission ───────────────────────────────────────────────────────
  const handleSubmit = useCallback(async (e) => {
    e.preventDefault();
    if (!prompt.trim() || isSubmitting) return;

    setErrorMessage(null);
    setEvents([]);
    seenEventIdsRef.current.clear();
    setPendingApproval(null);
    setTaskSummary('');
    setIsSubmitting(true);
    setTaskStatus('SUBMITTING');

    // Cancel any pending settle timer from previous tasks
    settleTimerRef.current?.cancel();

    // Entering new task prompt submission -> 'thinking' visual state
    setVisualState('thinking');

    if (wsCleanupRef.current) {
      wsCleanupRef.current();
      wsCleanupRef.current = null;
    }

    try {
      const submitRes = await submitTask(prompt, false);
      const taskId = submitRes.task_id;
      currentTaskIdRef.current = taskId;
      setCurrentTaskId(taskId);
      setTaskStatus(submitRes.status || 'ACCEPTED');

      const cleanup = connectTaskWebSocket(taskId, {
        onOpen: () => { setErrorMessage(null); },
        onEvent: (event) => {
          if (!event?.event_id) return;
          if (seenEventIdsRef.current.has(event.event_id)) return;
          seenEventIdsRef.current.add(event.event_id);

          setEvents((prev) => [...prev, event]);
          if (event.status) setTaskStatus(event.status);
          if (event.safe_user_message) setTaskSummary(event.safe_user_message);

          if (event.event_type === 'APPROVAL_REQUIRED') {
            setPendingApproval({ message: event.safe_user_message, tool: event.tool_name });
          } else if (
            event.event_type === 'APPROVAL_GRANTED' ||
            event.event_type === 'APPROVAL_DENIED'
          ) {
            setPendingApproval(null);
          }

          // Event-aware visual state resolution
          const resolution = resolveVisualState(
            visualStateRef.current,
            event,
            currentTaskIdRef.current
          );

          if (resolution.updated) {
            // Task boundary or new task detection: cancel any lingering settle timer
            if (resolution.isNewTask || (resolution.taskId && resolution.taskId !== currentTaskIdRef.current)) {
              settleTimerRef.current?.cancel();
              currentTaskIdRef.current = resolution.taskId;
              setCurrentTaskId(resolution.taskId);
            }
            setVisualState(resolution.state);

            // Schedule task-scoped settle timer if entering terminal state
            if (resolution.needsSettle && resolution.taskId) {
              settleTimerRef.current?.schedule(resolution.taskId);
            }
          }
        },
        onError: () => {
          setErrorMessage('Connection error. Checking status…');
          getTaskStatus(taskId)
            .then((st) => {
              if (st.status) setTaskStatus(st.status);
              if (st.status === 'FAILED') {
                setVisualState('failed');
                settleTimerRef.current?.schedule(taskId);
              }
            })
            .catch(() => {});
        },
        onClose: () => {
          getTaskStatus(taskId)
            .then((st) => {
              if (st.status) setTaskStatus(st.status);
              if (st.summary) setTaskSummary(st.summary);
              if (st.status === 'COMPLETED' && visualStateRef.current !== 'completed') {
                setVisualState('completed');
                settleTimerRef.current?.schedule(taskId);
              } else if (st.status === 'FAILED' && visualStateRef.current !== 'failed') {
                setVisualState('failed');
                settleTimerRef.current?.schedule(taskId);
              }
            })
            .catch(() => {});
        },
      });
      wsCleanupRef.current = cleanup;
    } catch (err) {
      setErrorMessage(err.message);
      setTaskStatus('FAILED');
      setVisualState('failed');
      if (currentTaskIdRef.current) {
        settleTimerRef.current?.schedule(currentTaskIdRef.current);
      }
    } finally {
      setIsSubmitting(false);
    }
  }, [prompt, isSubmitting]);

  // ─── Render ───────────────────────────────────────────────────────────────
  return (
    <div className="orbis-core">

      {/* ── Identity header ── */}
      <header className="orbis-identity">
        <h1 className="orbis-wordmark">ORBIS</h1>
        <div className={`orbis-gateway-dot ${gatewayOnline ? 'online' : 'offline'}`}
          title={gatewayOnline ? 'Gateway connected' : 'Gateway offline'}
        />
      </header>

      {/* ── Globe — the visual core of ORBIS ── */}
      <div className="orbis-globe-stage" data-state={orbisState}>
        <OrbisGlobe orbisState={orbisState} />
      </div>

      {/* ── State label below globe ── */}
      <div className="orbis-state-label" data-state={orbisState}>
        {STATUS_LABELS[orbisState] || STATUS_LABELS.idle}
      </div>

      {/* ── Minimal prompt / status ── */}
      <div className="orbis-prompt-zone">
        {errorMessage && (
          <div className="orbis-error">{errorMessage}</div>
        )}

        <form onSubmit={handleSubmit} className="orbis-form">
          <div className={`orbis-input-wrap ${inputFocused ? 'focused' : ''}`}>
            <input
              id="orbis-prompt-input"
              type="text"
              className="orbis-input"
              placeholder={gatewayOnline ? 'Issue a command…' : 'Gateway offline'}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onFocus={() => setInputFocused(true)}
              onBlur={() => setInputFocused(false)}
              disabled={isSubmitting}
              autoComplete="off"
              spellCheck={false}
            />
            <button
              id="orbis-submit-btn"
              type="submit"
              className="orbis-submit"
              disabled={isSubmitting || !prompt.trim() || !gatewayOnline}
              aria-label="Submit task"
            >
              {isSubmitting ? '…' : '↵'}
            </button>
          </div>
        </form>

        {/* Status / task meta */}
        <div className="orbis-status-row">
          {currentTaskId && (
            <span className="orbis-task-id" title={currentTaskId}>
              {currentTaskId.slice(0, 8)}…
            </span>
          )}
          {taskSummary && (
            <span className="orbis-summary">{taskSummary}</span>
          )}
        </div>

        {/* Approval notice */}
        {pendingApproval && (
          <div className="orbis-approval-notice">
            <strong>Awaiting confirmation</strong>
            <span>{pendingApproval.message}</span>
            {pendingApproval.tool && (
              <code>{pendingApproval.tool}</code>
            )}
          </div>
        )}
      </div>

      {/* ── Event stream ── */}
      {events.length > 0 && (
        <div className="orbis-event-stream">
          <div className="orbis-event-stream-header">
            <span>Event stream</span>
            <span className="orbis-event-count">{events.length}</span>
          </div>
          <div className="orbis-event-list">
            {events.map((ev) => (
              <div key={ev.event_id} className="orbis-event">
                <div className="orbis-event-meta">
                  <span className="orbis-event-type">{ev.event_type}</span>
                  {ev.timestamp && (
                    <span className="orbis-event-time">
                      {new Date(ev.timestamp).toLocaleTimeString()}
                    </span>
                  )}
                  {ev.risk_level && (
                    <span className={`orbis-event-risk risk-${ev.risk_level}`}>
                      {ev.risk_level}
                    </span>
                  )}
                </div>
                {ev.safe_user_message && (
                  <div className="orbis-event-msg">{ev.safe_user_message}</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default OrbisCore;
