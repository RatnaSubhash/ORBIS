/**
 * OptionWheelNav — Direct Left-Side Radial Navigation for ORBIS.
 *
 * Placed in the vertical center of the left side on every page (except during awakening).
 * Users can directly scroll, drag, or click to switch views immediately.
 * Clean, restrained, futuristic design without unnecessary glow effects.
 */

import { useCallback, useMemo } from 'react';
import { useAgent } from '../../state/AgentContext';
import { useAwakening } from '../core/AwakeningSequence';
import { NAV_ITEMS } from '../../types';
import OptionWheel from '../ui/OptionWheel';

export function OptionWheelNav() {
  const { activeView, setActiveView } = useAgent();
  const { isAwakening } = useAwakening();

  // Labels for the OptionWheel component
  const wheelLabels = useMemo(() => NAV_ITEMS.map((item) => item.label), []);

  // Find index of current active view
  const currentIndex = useMemo(() => {
    const idx = NAV_ITEMS.findIndex((item) => item.id === activeView);
    return idx >= 0 ? idx : 0;
  }, [activeView]);

  // Handle direct option selection from OptionWheel
  const handleWheelChange = useCallback(
    (index) => {
      const targetItem = NAV_ITEMS[index];
      if (targetItem && targetItem.id !== activeView) {
        setActiveView(targetItem.id);
      }
    },
    [activeView, setActiveView]
  );

  return (
    <aside
      id="orbis-left-wheel-dock"
      className={`orbis-left-wheel-dock ${isAwakening ? 'is-awakening' : ''}`}
      aria-label="Direct Navigation Wheel"
    >
      <div className="orbis-left-wheel-container">
        <div className="orbis-left-wheel-marker" aria-hidden="true" />
        <OptionWheel
          items={wheelLabels}
          selected={currentIndex}
          defaultSelected={currentIndex}
          onChange={handleWheelChange}
          side="left"
          textColor="#555d6b"
          activeColor="#33ccdd"
          fontSize={1.12}
          spacing={1.7}
          curve={1.2}
          tilt={8}
          blur={1.0}
          fade={0.3}
          minOpacity={0.16}
          smoothing={160}
          inset={24}
          draggable={true}
          className="orbis-side-wheel"
        />
      </div>
    </aside>
  );
}

export default OptionWheelNav;
