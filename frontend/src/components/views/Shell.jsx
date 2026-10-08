/**
 * OrbisShell — Master Coherent Application Shell (Phase C).
 *
 * Enforces the unified architecture:
 *   - ONE persistent WebGL Globe instance across the entire application.
 *   - Large central Globe on Home view.
 *   - Ambient, subtle, non-blocking background Globe on all secondary views.
 *   - Dynamic view routing driven by AgentContext activeView.
 *   - Direct left-side OptionWheel navigation.
 *   - AwakeningSequence presentation layer coordination.
 */

import { useAgent } from '../../state/AgentContext';
import { useAwakening } from '../core/AwakeningSequence';
import { OrbisGlobe } from '../core/OrbisGlobe';
import DecryptedText from '../ui/DecryptedText';
import OptionWheelNav from '../navigation/OptionWheelNav';
import HomeView from './HomeView';
import CommandView from './CommandView';
import LiveFlowView from './LiveFlowView';
import WorkspaceView from './WorkspaceView';
import ToolsView from './ToolsView';
import {
  RestorationView,
  SystemControlView,
  ResearchView,
  SettingsView,
} from './SecondaryViews';
import { ViewType } from '../../types';
import ErrorBoundary from '../ui/ErrorBoundary';

export function OrbisShell() {
  const { activeView, visualState, gatewayOnline, handleGlobeWake } = useAgent();
  const { phase: awakeningPhase, isAwakening, offsetY, wordmarkRef } = useAwakening();

  const isHome = activeView === ViewType.HOME;

  // Awakening presentation transforms
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

  // Active view renderer
  const renderActiveView = () => {
    switch (activeView) {
      case ViewType.HOME:
        return <HomeView />;
      case ViewType.COMMAND:
        return <CommandView />;
      case ViewType.LIVE_FLOW:
        return <LiveFlowView />;
      case ViewType.WORKSPACE:
      case ViewType.RESTORATION:
        return <WorkspaceView />;
      case ViewType.TOOLS:
        return <ToolsView />;
      case ViewType.SYSTEM_CONTROL:
        return <SystemControlView />;
      case ViewType.RESEARCH:
        return <ResearchView />;
      case ViewType.SETTINGS:
        return <SettingsView />;
      default:
        return <HomeView />;
    }
  };

  return (
    <div
      className={`orbis-shell ${isHome ? 'view-home' : 'view-secondary'} ${
        isAwakening ? `is-awakening phase-${awakeningPhase}` : ''
      }`}
    >
      {/* ── Persistent Identity Header ── */}
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
        <div
          className={`orbis-gateway-dot ${gatewayOnline ? 'online' : 'offline'}`}
          title={gatewayOnline ? 'Gateway online' : 'Gateway offline'}
        />
      </header>

      {/* ── Single Persistent Globe Layer ── */}
      <div
        className={`orbis-globe-stage ${isHome ? 'home-globe' : 'secondary-globe'}`}
        data-state={visualState}
        style={{
          pointerEvents: isHome && !isAwakening ? 'auto' : 'none',
        }}
      >
        <OrbisGlobe orbisState={visualState} onWake={handleGlobeWake} />
      </div>

      {/* ── Active View Content Area ── */}
      <main className="orbis-main-content">
        <ErrorBoundary key={activeView} title={`Active view (${activeView}) error isolated.`}>
          {renderActiveView()}
        </ErrorBoundary>
      </main>

      {/* ── Direct Left-Side OptionWheel Navigation ── */}
      <OptionWheelNav />
    </div>
  );
}

export default OrbisShell;
