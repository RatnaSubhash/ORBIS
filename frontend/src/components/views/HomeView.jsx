/**
 * HomeView — Core ORBIS Presence View.
 *
 * Sits beneath the prominent central Globe.
 * Minimalist, elegant, and focused on agent identity and quick prompt activation.
 */

import { useState, useCallback } from 'react';
import { useAgent } from '../../state/AgentContext';
import { ViewType } from '../../types';

export function HomeView() {
  const {
    visualState,
    statusLabel,
    gatewayOnline,
    executeTaskPrompt,
    isSubmitting,
    pendingApproval,
    resolveApproval,
    errorMessage,
    setActiveView,
  } = useAgent();

  const [prompt, setPrompt] = useState('');
  const [isFocused, setIsFocused] = useState(false);

  const handleSubmit = useCallback(
    (e) => {
      e.preventDefault();
      const clean = prompt.trim();
      if (!clean || isSubmitting) return;
      executeTaskPrompt(clean, false);
      setPrompt('');
      // Smoothly transition to Command view so the user sees the full conversation stream
      setActiveView(ViewType.COMMAND);
    },
    [prompt, isSubmitting, executeTaskPrompt, setActiveView]
  );

  return (
    <div className="orbis-home-view">
      {/* State label below central globe */}
      <div className="orbis-state-label" data-state={visualState}>
        <span className="orbis-state-pulse-dot" />
        {statusLabel}
      </div>

      {/* Voice / Interaction hint */}
      <div className="orbis-wake-hint">
        {visualState === 'listening' ? (
          <span className="orbis-hint-active">Listening for voice prompt…</span>
        ) : (
          <span>Click Globe or press <kbd>Space</kbd> to wake</span>
        )}
      </div>

      {/* Error message */}
      {errorMessage && (
        <div className="orbis-error-banner" role="alert">
          {errorMessage}
        </div>
      )}

      {/* Pending Approval Notice on Home */}
      {pendingApproval && (
        <div className="orbis-home-approval-card">
          <div className="orbis-approval-header">
            <span className="orbis-risk-badge risk-HIGH">CONFIRMATION REQUIRED</span>
            <span className="orbis-approval-tool">{pendingApproval.tool}</span>
          </div>
          <p className="orbis-approval-text">{pendingApproval.message}</p>
          <div className="orbis-approval-actions">
            <button
              type="button"
              className="orbis-btn-approve"
              onClick={() => resolveApproval(true, 'Approved via Home')}
            >
              Authorize Action
            </button>
            <button
              type="button"
              className="orbis-btn-deny"
              onClick={() => resolveApproval(false, 'Denied via Home')}
            >
              Deny
            </button>
          </div>
        </div>
      )}

      {/* Minimal quick prompt entry */}
      <form onSubmit={handleSubmit} className="orbis-home-prompt-form">
        <div className={`orbis-home-input-wrap ${isFocused ? 'focused' : ''}`}>
          <input
            id="orbis-home-prompt-input"
            type="text"
            className="orbis-home-input"
            placeholder={gatewayOnline ? 'Issue a command or inquiry…' : 'Gateway offline'}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onFocus={() => setIsFocused(true)}
            onBlur={() => setIsFocused(false)}
            disabled={isSubmitting || !gatewayOnline}
            autoComplete="off"
            spellCheck={false}
          />
          <button
            id="orbis-home-submit-btn"
            type="submit"
            className="orbis-home-submit"
            disabled={isSubmitting || !prompt.trim() || !gatewayOnline}
            aria-label="Submit command"
          >
            {isSubmitting ? '…' : '↵'}
          </button>
        </div>
      </form>
    </div>
  );
}

export default HomeView;
