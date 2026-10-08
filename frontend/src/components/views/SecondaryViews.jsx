/**
 * SecondaryViews — Structured Views for ORBIS Destinations (Phase H).
 *
 * Implements clean, authentic interfaces adhering to the ORBIS dark obsidian aesthetic
 * for Workspace, Tools, Restoration, System Control, Research, and Settings.
 *
 * Integrates:
 *   - Official React Bits ElasticSlider for Master Audio volume & Interface preferences
 *   - Official React Bits FluidGlass for contextual glass surface cards
 */

import { useState } from 'react';
import { useAgent } from '../../state/AgentContext';
import { ORBIS_TOOLS } from '../../types';
import ElasticSlider from '../ui/ElasticSlider';
import FluidGlass from '../ui/FluidGlass';

import ToolsView from './ToolsView';
export { ToolsView };

import RestorationView from './RestorationView';
export { RestorationView };


// ── System Control View ──────────────────────────────────────────────────────
export function SystemControlView() {
  const { volume, updateVolume, isMuted, toggleMute, gatewayOnline } = useAgent();

  return (
    <div className="orbis-view-container">
      <div className="orbis-view-header">
        <h2>System Hardware Controls</h2>
        <span className="orbis-view-sub">Windows OS audio and volume controls with post-action verification</span>
      </div>

      <FluidGlass borderRadius={16} className="orbis-glass-card-wrapper">
        <div className="orbis-card">
          <h3 className="orbis-card-title">System Master Audio</h3>

          <div className="orbis-control-group">
            <div className="orbis-slider-row">
              <label htmlFor="volume-slider">Master Volume: {volume}%</label>
              <ElasticSlider
                id="volume-slider"
                value={volume}
                onChange={(val) => updateVolume(val)}
                startingValue={0}
                maxValue={100}
                disabled={!gatewayOnline}
                className="orbis-audio-elastic-slider"
              />
            </div>

            <div className="orbis-action-row">
              <button
                type="button"
                className={`orbis-action-btn ${isMuted ? 'danger' : 'secondary'}`}
                disabled={!gatewayOnline}
                onClick={toggleMute}
              >
                {isMuted ? '🔇 Audio Muted (Click to Unmute)' : '🔊 Audio Active (Click to Mute)'}
              </button>
            </div>
          </div>
        </div>
      </FluidGlass>
    </div>
  );
}

// ── Research View ───────────────────────────────────────────────────────────
export function ResearchView() {
  const { executeTaskPrompt, isSubmitting, gatewayOnline } = useAgent();

  return (
    <div className="orbis-view-container">
      <div className="orbis-view-header">
        <h2>Research & Synthesis</h2>
        <span className="orbis-view-sub">Autonomous web research, source verification, and document authoring</span>
      </div>

      <FluidGlass borderRadius={16} className="orbis-glass-card-wrapper">
        <div className="orbis-card">
          <h3 className="orbis-card-title">Research Workspace</h3>
          <p className="orbis-card-text">
            Submit deep research queries to gather multi-source evidence, verify domain authority,
            and synthesize findings into structured markdown artifacts.
          </p>

          <div className="orbis-action-row">
            <button
              type="button"
              className="orbis-action-btn primary"
              disabled={isSubmitting || !gatewayOnline}
              onClick={() => executeTaskPrompt('Research the latest developments in context-aware desktop AI agents', false)}
            >
              Run Sample Research Query ↵
            </button>
          </div>
        </div>
      </FluidGlass>
    </div>
  );
}

// ── Settings View ───────────────────────────────────────────────────────────
export function SettingsView() {
  const { gatewayOnline } = useAgent();

  const [ambientIntensity, setAmbientIntensity] = useState(() => {
    try {
      const saved = localStorage.getItem('orbis_ui_intensity');
      return saved !== null ? Number(saved) : 65;
    } catch {
      return 65;
    }
  });

  const handleIntensityChange = (val) => {
    setAmbientIntensity(val);
    try {
      localStorage.setItem('orbis_ui_intensity', String(val));
    } catch {}
  };

  return (
    <div className="orbis-view-container">
      <div className="orbis-view-header">
        <h2>Preferences & Security</h2>
        <span className="orbis-view-sub">ORBIS desktop agent operational safety, interface dynamics, and gateway telemetry</span>
      </div>

      <div className="orbis-grid-2col">
        <FluidGlass borderRadius={16} className="orbis-glass-card-wrapper">
          <div className="orbis-card">
            <h3 className="orbis-card-title">Security & Approvals</h3>
            <div className="orbis-info-row">
              <span className="label">High-Risk Gate:</span>
              <span className="value status-ok">Enforced</span>
            </div>
            <div className="orbis-info-row">
              <span className="label">Sandboxed IPC:</span>
              <span className="value status-ok">Active (contextIsolation)</span>
            </div>
            <div className="orbis-info-row">
              <span className="label">Terminal Boundaries:</span>
              <span className="value status-ok">Allowlist Constrained</span>
            </div>
          </div>
        </FluidGlass>

        <FluidGlass borderRadius={16} className="orbis-glass-card-wrapper">
          <div className="orbis-card">
            <h3 className="orbis-card-title">Gateway Connection</h3>
            <div className="orbis-info-row">
              <span className="label">Service Endpoint:</span>
              <span className="value">http://127.0.0.1:8000</span>
            </div>
            <div className="orbis-info-row">
              <span className="label">Status:</span>
              <span className={`value ${gatewayOnline ? 'status-ok' : 'status-err'}`}>
                {gatewayOnline ? 'Online (HTTP 200)' : 'Offline'}
              </span>
            </div>
          </div>
        </FluidGlass>
      </div>

      {/* Interface Dynamics with authentic ElasticSlider */}
      <div style={{ marginTop: '1rem' }}>
        <FluidGlass borderRadius={16} className="orbis-glass-card-wrapper">
          <div className="orbis-card">
            <h3 className="orbis-card-title">Interface Dynamics</h3>
            <p className="orbis-card-text">
              Calibrate UI animation response curves and ambient visual effects intensity.
            </p>
            <div className="orbis-control-group" style={{ marginTop: '0.75rem' }}>
              <ElasticSlider
                label={`Animation Intensity: ${ambientIntensity}%`}
                value={ambientIntensity}
                onChange={handleIntensityChange}
                startingValue={0}
                maxValue={100}
                className="orbis-ui-elastic-slider"
              />
            </div>
          </div>
        </FluidGlass>
      </div>
    </div>
  );
}

