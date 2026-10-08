/**
 * ORBIS App Root.
 *
 * Mounts:
 *   - AgentProvider (central state, task dispatch, active view navigation, voice, system controls)
 *   - AwakeningSequence (5-phase startup sequence orchestrator)
 *   - OrbisShell (single unified shell hosting the Globe, active view, and OptionWheel)
 */

import { AgentProvider } from './state/AgentContext';
import { AwakeningSequence } from './components/core/AwakeningSequence';
import { OrbisShell } from './components/views/Shell';
import ErrorBoundary from './components/ui/ErrorBoundary';

export function App() {
  return (
    <AgentProvider>
      <div className="orbis-app">
        <AwakeningSequence>
          <ErrorBoundary title="Primary Shell Guard: isolated an unhandled presentation error.">
            <OrbisShell />
          </ErrorBoundary>
        </AwakeningSequence>
      </div>
    </AgentProvider>
  );
}

export default App;
