/**
 * AwakeningSequence — Presentation-level orchestrator for ORBIS startup sequence.
 *
 * Coordinates the 5-phase intelligent awakening lifecycle:
 *   black -> decrypting -> holding -> gliding -> complete
 *
 * Invariants:
 *   - Runs once per application launch/mount in memory (no localStorage).
 *   - Never replays on task creation, WebSocket reconnects, state changes, or re-renders.
 *   - DecryptedText is responsible ONLY for character decryption.
 *   - Upward movement and scaling are handled via GPU-accelerated transform: translateY / scale.
 *   - Calculates responsive viewport center on any desktop resolution.
 *   - Respects prefers-reduced-motion.
 */

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useLayoutEffect,
  useRef,
  useCallback,
} from 'react';

// Module-level in-memory flag (cleared when app process restarts)
let hasCompletedInitialAwakening = false;

/**
 * Resets the in-memory awakening flag (used strictly by test suites).
 */
export function resetAwakeningForTesting() {
  hasCompletedInitialAwakening = false;
}

export const AwakeningContext = createContext({
  phase: 'complete',
  isAwakening: false,
  offsetY: 0,
  wordmarkRef: null,
});

export function useAwakening() {
  return useContext(AwakeningContext);
}

export function AwakeningSequence({ children }) {
  const [phase, setPhase] = useState(() =>
    hasCompletedInitialAwakening ? 'complete' : 'black'
  );
  const [offsetY, setOffsetY] = useState(0);
  const wordmarkRef = useRef(null);
  const timersRef = useRef([]);

  const isAwakening = phase !== 'complete';

  // Responsive center calculation (fast, zero-reflow, transform-immune)
  const updateCenterOffset = useCallback(() => {
    const el = wordmarkRef.current;
    const header = el?.parentElement || el;
    const top = header?.offsetTop || 48;
    const height = header?.offsetHeight || 17;
    const elementCenter = top + height / 2;
    const viewportCenter = window.innerHeight / 2;
    setOffsetY(viewportCenter - elementCenter);
  }, []);

  useLayoutEffect(() => {
    if (hasCompletedInitialAwakening) return;

    updateCenterOffset();
    window.addEventListener('resize', updateCenterOffset);
    return () => window.removeEventListener('resize', updateCenterOffset);
  }, [updateCenterOffset]);

  // Phase progression lifecycle
  useEffect(() => {
    if (hasCompletedInitialAwakening) return;

    // Check prefers-reduced-motion
    const prefersReducedMotion =
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    if (prefersReducedMotion) {
      setPhase('complete');
      hasCompletedInitialAwakening = true;
      return;
    }

    const schedule = (fn, delay) => {
      const id = setTimeout(fn, delay);
      timersRef.current.push(id);
      return id;
    };

    // 1. Initial Black -> Begin Decrypting (~200ms)
    schedule(() => {
      updateCenterOffset();
      setPhase('decrypting');
    }, 200);

    // 2. Decrypting -> Holding fully revealed text (~2000ms)
    // Decrypting duration = 1800ms (allowing 5-char center-reveal at 240ms + settle)
    schedule(() => {
      setPhase('holding');
    }, 2000);

    // 3. Holding -> Gliding upward & scaling down (~2400ms)
    // Holding duration = 400ms (pure, stationary hold)
    schedule(() => {
      setPhase('gliding');
    }, 2400);

    // 4. Gliding -> Complete settled home position (~3850ms)
    // Gliding duration = 1400ms (continuous GPU-accelerated motion) + 50ms buffer
    schedule(() => {
      setPhase('complete');
      hasCompletedInitialAwakening = true;
    }, 3850);

    return () => {
      timersRef.current.forEach(clearTimeout);
      timersRef.current = [];
    };
  }, [updateCenterOffset]);

  const value = {
    phase,
    isAwakening,
    offsetY,
    wordmarkRef,
  };

  return (
    <AwakeningContext.Provider value={value}>
      {children}
    </AwakeningContext.Provider>
  );
}

export default AwakeningSequence;
