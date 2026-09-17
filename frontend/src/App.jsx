import React, { useState, useEffect, useRef } from 'react';
import {
  checkGatewayHealth,
  submitTask,
  getTaskStatus,
  connectTaskWebSocket,
  GATEWAY_HTTP_BASE,
} from './api/gateway';

export function App() {
  const [prompt, setPrompt] = useState('');
  const [currentTaskId, setCurrentTaskId] = useState(null);
  const [taskStatus, setTaskStatus] = useState('IDLE');
  const [taskSummary, setTaskSummary] = useState('');
  const [events, setEvents] = useState([]);
  const [pendingApproval, setPendingApproval] = useState(null);
  const [gatewayOnline, setGatewayOnline] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState(null);

  // Ref to track seen event IDs to guarantee deduplication
  const seenEventIdsRef = useRef(new Set());
  const wsCleanupRef = useRef(null);

  // Periodically check gateway health
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
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  // Cleanup active WebSocket on unmount
  useEffect(() => {
    return () => {
      if (wsCleanupRef.current) {
        wsCleanupRef.current();
        wsCleanupRef.current = null;
      }
    };
  }, []);

  // Handle task submission
  async function handleSubmit(e) {
    e.preventDefault();
    if (!prompt.trim() || isSubmitting) return;

    // Reset previous task UI state
    setErrorMessage(null);
    setEvents([]);
    seenEventIdsRef.current.clear();
    setPendingApproval(null);
    setTaskSummary('');
    setIsSubmitting(true);
    setTaskStatus('SUBMITTING');

    if (wsCleanupRef.current) {
      wsCleanupRef.current();
      wsCleanupRef.current = null;
    }

    try {
      const submitRes = await submitTask(prompt, false);
      const taskId = submitRes.task_id;
      setCurrentTaskId(taskId);
      setTaskStatus(submitRes.status || 'ACCEPTED');

      // Establish real-time WebSocket connection to receive AgentEvents
      const cleanup = connectTaskWebSocket(taskId, {
        onOpen: () => {
          setErrorMessage(null);
        },
        onEvent: (event) => {
          if (!event || !event.event_id) return;

          // Deduplicate: ignore if this event_id has already been added
          if (seenEventIdsRef.current.has(event.event_id)) {
            return;
          }
          seenEventIdsRef.current.add(event.event_id);

          setEvents((prev) => [...prev, event]);

          // Update operational status
          if (event.status) {
            setTaskStatus(event.status);
          }

          // Update summary if message is informative
          if (event.safe_user_message) {
            setTaskSummary(event.safe_user_message);
          }

          // Detect approval-required milestone
          if (event.event_type === 'APPROVAL_REQUIRED') {
            setPendingApproval({
              message: event.safe_user_message,
              tool: event.tool_name,
            });
          } else if (event.event_type === 'APPROVAL_GRANTED' || event.event_type === 'APPROVAL_DENIED') {
            setPendingApproval(null);
          }
        },
        onError: () => {
          setErrorMessage('WebSocket connection error. Checking task status via REST...');
          // Poll task status as fallback
          getTaskStatus(taskId)
            .then((st) => setTaskStatus(st.status))
            .catch(() => {});
        },
        onClose: () => {
          // Final status check when WebSocket closes
          getTaskStatus(taskId)
            .then((st) => {
              if (st.status) setTaskStatus(st.status);
              if (st.summary) setTaskSummary(st.summary);
            })
            .catch(() => {});
        },
      });

      wsCleanupRef.current = cleanup;
    } catch (err) {
      setErrorMessage(err.message);
      setTaskStatus('FAILED');
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="orbis-app">
      <header className="header">
        <div className="header-title">
          <h1>ORBIS Agent</h1>
          <p>Phase 12 Desktop Shell & Real-Time Flow Integration</p>
        </div>
        <div className={`gateway-badge ${gatewayOnline ? 'online' : 'offline'}`}>
          <span className="indicator-dot"></span>
          {gatewayOnline ? 'Gateway Connected' : 'Gateway Offline'}
        </div>
      </header>

      {errorMessage && (
        <div className="error-banner">
          ⚠️ {errorMessage}
        </div>
      )}

      <div className="card">
        <form onSubmit={handleSubmit} className="prompt-form">
          <div className="input-row">
            <input
              type="text"
              className="prompt-input"
              placeholder="Enter task command (e.g., 'Say hello from the ORBIS gateway test.')..."
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              disabled={isSubmitting}
            />
            <button
              type="submit"
              className="submit-btn"
              disabled={isSubmitting || !prompt.trim() || !gatewayOnline}
            >
              {isSubmitting ? 'Dispatching...' : 'Run Task'}
            </button>
          </div>

          <div className="status-bar">
            <span>
              Status:{' '}
              <span className={`status-tag ${taskStatus}`}>
                {taskStatus}
              </span>
            </span>
            {currentTaskId && (
              <span>
                Task ID: <code>{currentTaskId}</code>
              </span>
            )}
          </div>
        </form>

        {pendingApproval && (
          <div className="approval-banner" style={{ marginTop: '14px' }}>
            <strong>Action Requires User Confirmation:</strong>
            <span>{pendingApproval.message}</span>
            <small>(Tool: {pendingApproval.tool || 'system'})</small>
          </div>
        )}

        {taskSummary && (
          <div style={{ marginTop: '10px', fontSize: '0.85rem', color: '#93c5fd' }}>
            Latest: {taskSummary}
          </div>
        )}
      </div>

      <div className="card events-section">
        <div className="events-header">
          <h2>Task Event Stream</h2>
          <span className="event-count">{events.length} events</span>
        </div>

        {events.length === 0 ? (
          <div className="empty-state">
            No events recorded yet. Submit a task above to observe real-time agent execution.
          </div>
        ) : (
          <div className="events-list">
            {events.map((ev) => (
              <div key={ev.event_id} className="event-item">
                <div className="event-meta">
                  <span className="event-type-badge">{ev.event_type}</span>
                  <span className="event-time">
                    {ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString() : ''}
                  </span>
                  {ev.risk_level && (
                    <span className={`event-risk ${ev.risk_level}`}>
                      {ev.risk_level}
                    </span>
                  )}
                </div>
                <div className="event-message">
                  {ev.safe_user_message}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default App;
