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
  startVoiceCapture,
  stopVoiceCapture,
  cancelVoiceCapture,
  speakText,
} from '../api/gateway';

import { OrbisGlobe } from './core/OrbisGlobe';
import { useAwakening } from './core/AwakeningSequence';
import DecryptedText from './ui/DecryptedText';
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
  const [responseAnswer, setResponseAnswer] = useState('');
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [events, setEvents] = useState([]);
  const [pendingApproval, setPendingApproval] = useState(null);
  const [gatewayOnline, setGatewayOnline] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState(null);

  // Visual presentation state (subordinate to AgentEvent lifecycle)
  const [visualState, setVisualState] = useState('idle');

  // Interaction state
  const [inputFocused, setInputFocused] = useState(false);

  // Refs for task boundaries, voice tracking, and deduplication
  const seenEventIdsRef = useRef(new Set());
  const wsCleanupRef = useRef(null);
  const currentTaskIdRef = useRef(null);
  const visualStateRef = useRef('idle');
  const settleTimerRef = useRef(null);
  const isVoiceTaskRef = useRef(false);

  // Keep ref synchronized for immediate callback checks
  visualStateRef.current = visualState;

  // Derive the ORBIS visual state (falls back to idle if gateway is offline)
  const orbisState = gatewayOnline ? visualState : 'idle';

  // Awakening presentation state
  const { phase: awakeningPhase, isAwakening, offsetY, wordmarkRef } = useAwakening();

  let wordmarkTransform = 'none';
  let wordmarkOpacity = 1;

  if (isAwakening) {
    if (awakeningPhase === 'black') {
      wordmarkTransform = `translateY(${offsetY}px) scale(3)`;
      wordmarkOpacity = 0;
    } else if (awakeningPhase === 'decrypting' || awakeningPhase === 'holding') {
      wordmarkTransform = `translateY(${offsetY}px) scale(3)`;
      wordmarkOpacity = 1;
    } else if (awakeningPhase === 'gliding') {
      wordmarkTransform = 'translateY(0px) scale(1)';
      wordmarkOpacity = 1;
    }
  }

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

  // ── WebSocket & Voice cleanup on unmount ──────────────────────────────────
  const isListeningRef = useRef(false);
  isListeningRef.current = visualState === 'listening';

  useEffect(() => {
    return () => {
      if (wsCleanupRef.current) {
        wsCleanupRef.current();
        wsCleanupRef.current = null;
      }
      if (isListeningRef.current) {
        cancelVoiceCapture().catch(() => {});
      }
    };
  }, []);

  // ── Cancel voice capture on gateway disconnect ────────────────────────────
  useEffect(() => {
    if (!gatewayOnline && visualState === 'listening') {
      cancelVoiceCapture().catch(() => {});
      setVisualState('idle');
    }
  }, [gatewayOnline, visualState]);

  // ── Central Task Submission Pipeline ──────────────────────────────────────
  // ── Central Task Submission Pipeline ──────────────────────────────────────
  const executeTaskPrompt = useCallback(async (promptText, isVoice = false) => {
    const cleanPrompt = (promptText || '').trim();
    if (!cleanPrompt || isSubmitting) return;

    isVoiceTaskRef.current = isVoice;
    setErrorMessage(null);
    setEvents([]);
    seenEventIdsRef.current.clear();
    setPendingApproval(null);
    setTaskSummary('');
    setResponseAnswer('');
    setIsSubmitting(true);
    setTaskStatus('SUBMITTING');

    // Cancel any pending settle timer from previous tasks
    settleTimerRef.current?.cancel();

    if (wsCleanupRef.current) {
      wsCleanupRef.current();
      wsCleanupRef.current = null;
    }

    try {
      const submitRes = await submitTask(cleanPrompt, false);
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

          // Authoritative user-facing answer extraction on TASK_COMPLETED
          if (event.event_type === 'TASK_COMPLETED') {
            const answer = event.metadata?.answer || event.safe_user_message || '';
            if (answer) {
              setResponseAnswer(answer);
            }

            if (isVoiceTaskRef.current) {
              // Voice request lifecycle:
              // TASK_COMPLETED -> safe answer received -> display answer -> responding -> SAPI5 TTS -> TTS finished -> idle
              settleTimerRef.current?.cancel();
              setVisualState('responding');
              setIsSpeaking(true);

              speakText(answer)
                .catch((err) => {
                  console.warn('SAPI5 TTS vocalization failed:', err);
                  // Safety invariant 5: keep visual answer, do not erase, do not expose raw error, return to idle
                })
                .finally(() => {
                  setIsSpeaking(false);
                  setVisualState('idle');
                  isVoiceTaskRef.current = false;
                });
              return;
            }
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
              const answer = st.answer || st.summary;
              if (st.status === 'COMPLETED') {
                if (answer) setResponseAnswer(answer);
                if (isVoiceTaskRef.current) {
                  settleTimerRef.current?.cancel();
                  setVisualState('responding');
                  setIsSpeaking(true);
                  speakText(answer)
                    .catch((err) => {
                      console.warn('SAPI5 TTS vocalization failed:', err);
                    })
                    .finally(() => {
                      setIsSpeaking(false);
                      setVisualState('idle');
                      isVoiceTaskRef.current = false;
                    });
                } else if (visualStateRef.current !== 'completed') {
                  setVisualState('completed');
                  settleTimerRef.current?.schedule(taskId);
                }
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
  }, [isSubmitting]);

  const handleSubmit = useCallback((e) => {
    e.preventDefault();
    if (!prompt.trim() || isSubmitting) return;
    executeTaskPrompt(prompt, false);
  }, [prompt, isSubmitting, executeTaskPrompt]);

  // ── Globe Wake Interaction ───────────────────────────────────────────────
  const handleGlobeWake = useCallback(async () => {
    // 1. Awakening sequence protection: cannot wake during black, decrypting, holding, or gliding
    if (isAwakening || awakeningPhase !== 'complete') {
      return;
    }

    // 2. Active task protection: do not interrupt or create duplicate tasks
    const activeStates = ['thinking', 'planning', 'executing', 'responding', 'approval', 'completed'];
    if (activeStates.includes(orbisState) || isSubmitting) {
      return;
    }

    // 3. Second click while listening: stop capture and transcribe
    if (orbisState === 'listening') {
      try {
        const result = await stopVoiceCapture();
        if (result && !result.empty && result.transcript) {
          // Voice result reaches existing task pipeline (AgentEvents authoritatively drive visual state)
          executeTaskPrompt(result.transcript, true);
        } else {
          // Empty speech result returns cleanly to idle
          setVisualState('idle');
          setTaskSummary('');
        }
      } catch (err) {
        setVisualState('idle');
        setErrorMessage(err.message || 'Microphone access is unavailable.');
      }
      return;
    }

    // 4. Idle wake: start voice capture
    if (orbisState === 'idle') {
      if (!gatewayOnline) {
        setErrorMessage('Gateway offline');
        return;
      }

      setErrorMessage(null);
      setVisualState('listening');
      setTaskSummary('');

      try {
        await startVoiceCapture();
      } catch (err) {
        // Voice failure returns safely to idle with user-safe error message
        setVisualState('idle');
        setErrorMessage(err.message || 'Microphone access is unavailable.');
      }
    }
  }, [isAwakening, awakeningPhase, orbisState, isSubmitting, gatewayOnline, executeTaskPrompt]);


  // ─── Render ───────────────────────────────────────────────────────────────
  return (
    <div className={`orbis-core ${isAwakening ? `is-awakening phase-${awakeningPhase}` : ''}`}>

      {/* ── Identity header ── */}
      <header className="orbis-identity">
        <h1
          ref={wordmarkRef}
          className={`orbis-wordmark ${isAwakening ? 'is-awakening' : ''}`}
          data-phase={awakeningPhase}
          style={
            isAwakening
              ? {
                  transform: wordmarkTransform,
                  opacity: wordmarkOpacity,
                }
              : undefined
          }
        >
          {isAwakening ? (
            <DecryptedText
              text="ORBIS"
              animateOn="view"
              sequential
              revealDirection="center"
              speed={240}
              maxIterations={10}
              characters="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*~"
              useOriginalCharsOnly={false}
              className="orbis-decrypted-char"
              encryptedClassName="orbis-encrypted-char"
              parentClassName="orbis-decrypted-parent"
            />
          ) : (
            'ORBIS'
          )}
        </h1>
        <div className={`orbis-gateway-dot ${gatewayOnline ? 'online' : 'offline'}`}
          title={gatewayOnline ? 'Gateway connected' : 'Gateway offline'}
        />
      </header>

      {/* ── Globe — the visual core of ORBIS ── */}
      <div className="orbis-globe-stage" data-state={orbisState}>
        <OrbisGlobe orbisState={orbisState} onWake={handleGlobeWake} />
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

      {/* ── Dedicated ORBIS response area ── */}
      {responseAnswer && (
        <div className="orbis-response-area" id="orbis-response-area">
          <div className="orbis-response-header">
            <span className="orbis-response-tag">ORBIS</span>
            {isSpeaking && (
              <span className="orbis-speaking-indicator" id="orbis-speaking-indicator">
                <span className="orbis-speaking-dot" />
                Vocalizing
              </span>
            )}
          </div>
          <div className="orbis-response-text" id="orbis-response-text">
            {responseAnswer}
          </div>
        </div>
      )}

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
