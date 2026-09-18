/**
 * OrbisGlobe — ORBIS-facing wrapper around GlobePulse.
 *
 * This layer translates ORBIS operational state into GlobePulse visual props.
 * When the React Bits Pro Globe is available, replace GlobePulse here only;
 * OrbisCore and App remain unchanged.
 *
 * Supported ORBIS states:
 *   idle        — slow gentle rotation
 *   listening   — slightly faster rotation
 *   thinking    — paused/slow auto-rotation (subtle suggestion of concentration)
 *   responding  — standard rotation
 *   executing   — slightly elevated speed
 */

import { useMemo } from 'react';
import { GlobePulse } from './GlobePulse';
import { STATE_SPEEDS } from '../../state/visualState';

/** Default geographic pulse markers — conceptual "ORBIS nodes". */
const DEFAULT_MARKERS = [
  { id: 'pulse-1', location: [51.51, -0.13],   delay: 0 },
  { id: 'pulse-2', location: [40.71, -74.01],  delay: 0.5 },
  { id: 'pulse-3', location: [35.68, 139.65],  delay: 1 },
  { id: 'pulse-4', location: [-33.87, 151.21], delay: 1.5 },
];

/**
 * @param {{
 *   orbisState?: 'idle' | 'listening' | 'thinking' | 'planning' | 'executing' | 'responding' | 'completed' | 'failed' | 'approval',
 *   markers?: Array<{ id: string, location: [number, number], delay: number }>,
 *   className?: string,
 * }} props
 */
export function OrbisGlobe({
  orbisState = 'idle',
  markers = DEFAULT_MARKERS,
  className = '',
}) {
  const speed = useMemo(
    () => STATE_SPEEDS[orbisState] ?? STATE_SPEEDS.idle,
    [orbisState]
  );

  return (
    <GlobePulse
      markers={markers}
      className={`orbis-globe-pulse orbis-globe-${orbisState} ${className}`.trim()}
      speed={speed}
    />
  );
}

export default OrbisGlobe;
