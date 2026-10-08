/**
 * CommandView — Primary Conversational Agent Interface (Phase D & Phase H).
 *
 * Connected directly to AgentContext and real FastAPI Gateway:
 *   - Real task prompt submission
 *   - Authoritative user response presentation with official SplitText animation
 *   - Push-to-Talk SAPI5 TTS vocalization indicator
 *   - Integrated high-risk approval resolution gate
 *   - Official React Bits Stepper lifecycle visualizer
 *   - Official React Bits FluidGlass contextual refraction panels
 *   - Official React Bits contextual Dock for quick actions
 *   - Error & offline state feedback
 */

import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useAgent } from '../../state/AgentContext';
import { ViewType } from '../../types';
import SplitText from '../ui/SplitText';
import FluidGlass from '../ui/FluidGlass';
import Dock from '../ui/Dock';
import ErrorBoundary from '../ui/ErrorBoundary';
import { History, Search, Volume2, Wrench, Trash2, Plus, PlusCircle } from 'lucide-react';

export function CommandView() {
  const {
    currentTaskId,
    taskStatus,
    taskSummary,
    responseAnswer,
    pendingApproval,
    resolveApproval,
    isSubmitting,
    errorMessage,
    gatewayOnline,
    executeTaskPrompt,
    clearConversationUi,
    setActiveView,
    isSpeaking,
    conversationId,
    messages,
    startNewChat,
  } = useAgent();

  const [inputPrompt, setInputPrompt] = useState('');
  const [lastSubmittedPrompt, setLastSubmittedPrompt] = useState('');
  const [isFocused, setIsFocused] = useState(false);
  const messagesEndRef = useRef(null);

  // Auto-scroll to bottom as answers stream in
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, responseAnswer, taskSummary, pendingApproval]);

  const handleSubmit = useCallback(
    (e) => {
      e.preventDefault();
      const clean = inputPrompt.trim();
      if (!clean || isSubmitting || !gatewayOnline) return;

      setLastSubmittedPrompt(clean);
      setInputPrompt('');
      executeTaskPrompt(clean, false);
    },
    [inputPrompt, isSubmitting, gatewayOnline, executeTaskPrompt]
  );


  // Contextual Dock items:
  // - New Chat: resets conversation context and starts clean thread
  // - Continue Workspace: intentional task action via executeTaskPrompt()
  // - Research Query: populates input for user review (does NOT auto-execute)
  // - System Audio: navigates to System Control (does NOT silently change volume)
  // - Explore Tools: navigates to Tools view
  // - Clear Conversation: clears ONLY active conversation UI (does NOT wipe persistent DB)
  const dockItems = useMemo(
    () => [
      {
        icon: <PlusCircle size={18} className="dock-svg" />,
        label: 'New Chat',
        onClick: () => {
          startNewChat();
          setLastSubmittedPrompt('');
          setInputPrompt('');
        },
      },
      {
        icon: <History size={18} className="dock-svg" />,
        label: 'Continue Workspace',
        onClick: () => executeTaskPrompt('Continue where I left off', false),
      },
      {
        icon: <Search size={18} className="dock-svg" />,
        label: 'Research Query',
        onClick: () => setInputPrompt('Research the latest developments in context-aware desktop AI agents'),
      },
      {
        icon: <Volume2 size={18} className="dock-svg" />,
        label: 'System Audio',
        onClick: () => setActiveView(ViewType.SYSTEM_CONTROL),
      },
      {
        icon: <Wrench size={18} className="dock-svg" />,
        label: 'Explore Tools',
        onClick: () => setActiveView(ViewType.TOOLS),
      },
      {
        icon: <Trash2 size={18} className="dock-svg" />,
        label: 'Clear UI',
        onClick: () => {
          clearConversationUi();
          setLastSubmittedPrompt('');
          setInputPrompt('');
        },
      },
    ],
    [startNewChat, executeTaskPrompt, setActiveView, clearConversationUi]
  );

  return (
    <div className="orbis-command-view">
      {/* Header status bar with New Chat trigger */}
      <div className="orbis-command-header">
        <div className="orbis-command-title-group">
          <span className="orbis-command-title">Command Workspace</span>
          <span className="orbis-thread-badge">
            Thread • {messages ? messages.filter((m) => m.role === 'user').length : 0} turns
          </span>
          {currentTaskId && (
            <span className="orbis-command-task-id" title={currentTaskId}>
              ID: {currentTaskId.slice(0, 8)}…
            </span>
          )}
        </div>
        <div className="orbis-command-header-actions">
          <button
            type="button"
            className="orbis-btn-new-chat"
            onClick={() => {
              startNewChat();
              setLastSubmittedPrompt('');
              setInputPrompt('');
            }}
            title="Start a fresh conversation thread with clean context"
          >
            <Plus size={14} className="orbis-btn-icon" />
            <span>New Chat</span>
          </button>
          <div className="orbis-command-status-badge" data-status={taskStatus}>
            {taskStatus}
          </div>
        </div>
      </div>

      {/* Gateway offline alert */}
      {!gatewayOnline && (
        <div className="orbis-error-banner" role="alert">
          ORBIS Gateway service is offline. Start the backend with <code>uvicorn core.gateway:create_app</code>.
        </div>
      )}

      {/* Error message */}
      {errorMessage && (
        <div className="orbis-error-banner" role="alert">
          {errorMessage}
        </div>
      )}

      {/* Conversation timeline stream */}
      <div className="orbis-conversation-stream">
        {/* Intro placeholder if no messages in thread */}
        {(!messages || messages.length === 0) && (
          <div className="orbis-command-empty">
            <span className="orbis-empty-icon">◈</span>
            <h3>Natural Language Intelligence Ready</h3>
            <p>
              Ask questions, explore context continuity ("What was I working on?"),
              request research, generate documents, or execute safe system actions.
            </p>
          </div>
        )}

        {/* Multi-turn continuous conversation thread */}
        {messages && messages.map((msg, index) => {
          if (msg.role === 'user') {
            const userText = typeof msg.content === 'string'
              ? msg.content
              : typeof msg.content === 'object' && msg.content !== null
                ? JSON.stringify(msg.content, null, 2)
                : String(msg.content ?? '');

            return (
              <div key={msg.id || index} className="orbis-message-item user-message">
                <div className="orbis-message-meta">
                  <span className="orbis-message-author">YOU</span>
                </div>
                <div className="orbis-message-body user-body">
                  {userText}
                </div>
              </div>
            );
          }

          // Agent: High-risk confirmation gate
          if (msg.pendingApproval) {
            return (
              <ErrorBoundary key={msg.id || index}>
                <FluidGlass className="orbis-glass-card-wrapper" borderRadius={14}>
                  <div className="orbis-message-item approval-card">
                    <div className="orbis-approval-header">
                      <span className="orbis-risk-badge risk-HIGH">HIGH-RISK ACTION CONFIRMATION</span>
                      <span className="orbis-approval-tool-name">{msg.pendingApproval.tool}</span>
                    </div>
                    <div className="orbis-approval-body">
                      <p>{msg.pendingApproval.message}</p>
                    </div>
                    <div className="orbis-approval-btn-group">
                      <button
                        type="button"
                        className="orbis-btn-approve"
                        onClick={() => resolveApproval(true, 'Approved via Command View')}
                      >
                        ✓ Authorize Execution
                      </button>
                      <button
                        type="button"
                        className="orbis-btn-deny"
                        onClick={() => resolveApproval(false, 'Denied via Command View')}
                      >
                        ✕ Deny & Cancel
                      </button>
                    </div>
                  </div>
                </FluidGlass>
              </ErrorBoundary>
            );
          }

          // Agent: In-progress running message
          if (msg.status === 'RUNNING' && !msg.content) {
            return (
              <div key={msg.id || index} className="orbis-message-item agent-message running">
                <div className="orbis-message-meta">
                  <span className="orbis-message-author agent">ORBIS</span>
                  <span className="orbis-agent-pulsing-badge">Processing</span>
                </div>
                <div className="orbis-message-body agent-body">
                  {msg.summary || 'Formulating action plan…'}
                </div>
              </div>
            );
          }

          // Agent: Completed or final answer
          if (msg.content) {
            const isLatest = index === messages.length - 1;
            const answerText = typeof msg.content === 'string'
              ? msg.content
              : typeof msg.content === 'object' && msg.content !== null
                ? JSON.stringify(msg.content, null, 2)
                : String(msg.content ?? '');

            return (
              <ErrorBoundary
                key={msg.id || index}
                fallback={
                  <div className="orbis-message-item agent-message completed">
                    <div className="orbis-message-meta">
                      <span className="orbis-message-author agent">ORBIS</span>
                    </div>
                    <div className="orbis-message-body agent-body response-bubble">
                      <div className="orbis-response-markdown">{answerText}</div>
                    </div>
                  </div>
                }
              >
                <FluidGlass className="orbis-glass-card-wrapper" borderRadius={14}>
                  <div className={`orbis-message-item agent-message ${msg.status === 'FAILED' ? 'failed' : 'completed'}`}>
                    <div className="orbis-message-meta">
                      <span className="orbis-message-author agent">ORBIS</span>
                      {isSpeaking && isLatest && (
                        <span className="orbis-speaking-tag">
                          <span className="orbis-speaking-dot" />
                          Vocalizing
                        </span>
                      )}
                      {msg.status === 'FAILED' && (
                        <span className="orbis-status-badge status-FAILED">Execution Warning</span>
                      )}
                    </div>
                    <div className="orbis-message-body agent-body response-bubble">
                      <div className="orbis-response-lead">
                        {isLatest ? (
                          <SplitText
                            text="Direct Response"
                            className="orbis-splittext-heading"
                            delay={40}
                            duration={0.5}
                          />
                        ) : (
                          <span className="orbis-splittext-heading">Direct Response</span>
                        )}
                      </div>
                      <div className="orbis-response-markdown">
                        {answerText}
                      </div>
                    </div>
                  </div>
                </FluidGlass>
              </ErrorBoundary>
            );
          }

          return null;
        })}

        <div ref={messagesEndRef} />
      </div>

      {/* Input zone */}
      <div className="orbis-command-input-zone">
        <form onSubmit={handleSubmit} className="orbis-command-form">
          <div className={`orbis-command-input-wrap ${isFocused ? 'focused' : ''}`}>
            <input
              id="orbis-command-prompt-input"
              type="text"
              className="orbis-command-input"
              placeholder={gatewayOnline ? 'Type an instruction or query…' : 'Gateway unavailable'}
              value={inputPrompt}
              onChange={(e) => setInputPrompt(e.target.value)}
              onFocus={() => setIsFocused(true)}
              onBlur={() => setIsFocused(false)}
              disabled={isSubmitting || !gatewayOnline}
              autoComplete="off"
              spellCheck={false}
            />
            <button
              id="orbis-command-submit-btn"
              type="submit"
              className="orbis-command-submit"
              disabled={isSubmitting || !inputPrompt.trim() || !gatewayOnline}
              aria-label="Submit instruction"
            >
              {isSubmitting ? '…' : '↵'}
            </button>
          </div>
        </form>
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

export default CommandView;

