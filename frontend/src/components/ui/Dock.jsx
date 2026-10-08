/**
 * Official React Bits Dock Component (Zero-Flicker & Window-Fixed Bottom Center)
 * Source: https://reactbits.dev/components/dock
 *
 * Uses React Portal to attach directly to document.body, ensuring it is unconditionally
 * fixed at the bottom center of the window regardless of parent container transforms.
 * Uses GPU-accelerated transform scaling with invariant static slots to eliminate flickering.
 */
import { motion, useMotionValue, useSpring, useTransform, AnimatePresence } from 'motion/react';
import { Children, cloneElement, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import './Dock.css';

function DockItem({
  children,
  className = '',
  onClick,
  mouseX,
  spring,
  distance,
  magnification,
  baseItemSize,
  label,
}) {
  const slotRef = useRef(null);
  const isHovered = useMotionValue(0);

  // Invariant distance calculation: Measures static slot center (no layout feedback loop)
  const mouseDistance = useTransform(mouseX, (val) => {
    if (val === Infinity || val === null || !slotRef.current) return distance;
    const rect = slotRef.current.getBoundingClientRect();
    return val - (rect.x + rect.width / 2);
  });

  const maxScale = magnification / baseItemSize;
  const targetScale = useTransform(
    mouseDistance,
    [-distance, 0, distance],
    [1, maxScale, 1]
  );
  const scale = useSpring(targetScale, spring);

  const targetY = useTransform(mouseDistance, [-distance, 0, distance], [0, -4, 0]);
  const y = useSpring(targetY, spring);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onClick?.();
    }
  };

  return (
    <div
      ref={slotRef}
      style={{
        width: baseItemSize,
        height: baseItemSize,
      }}
      className="dock-item-slot"
    >
      <motion.div
        style={{
          width: baseItemSize,
          height: baseItemSize,
          scale,
          y,
        }}
        onHoverStart={() => isHovered.set(1)}
        onHoverEnd={() => isHovered.set(0)}
        onFocus={() => isHovered.set(1)}
        onBlur={() => isHovered.set(0)}
        onClick={onClick}
        className={`dock-item ${className}`}
        tabIndex={0}
        role="button"
        aria-haspopup="true"
        aria-label={label}
        onKeyDown={handleKeyDown}
      >
        {Children.map(children, (child) => cloneElement(child, { isHovered }))}
      </motion.div>
    </div>
  );
}

function DockLabel({ children, className = '', ...rest }) {
  const { isHovered } = rest;
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    if (!isHovered) return;
    const unsubscribe = isHovered.on('change', (latest) => {
      setIsVisible(latest === 1);
    });
    return () => unsubscribe();
  }, [isHovered]);

  return (
    <AnimatePresence>
      {isVisible && (
        <motion.div
          initial={{ opacity: 0, y: 4, scale: 0.95 }}
          animate={{ opacity: 1, y: -12, scale: 1 }}
          exit={{ opacity: 0, y: 4, scale: 0.95 }}
          transition={{ duration: 0.15 }}
          className={`dock-label ${className}`}
          role="tooltip"
          style={{ x: '-50%' }}
        >
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function DockIcon({ children, className = '' }) {
  return <div className={`dock-icon ${className}`}>{children}</div>;
}

export default function Dock({
  items,
  className = '',
  spring = { mass: 0.1, stiffness: 200, damping: 18 },
  magnification = 50,
  distance = 110,
  panelHeight = 48,
  baseItemSize = 36,
}) {
  const mouseX = useMotionValue(Infinity);

  const dockContent = (
    <div className="dock-outer">
      <div
        onMouseMove={({ clientX }) => mouseX.set(clientX)}
        onMouseLeave={() => mouseX.set(Infinity)}
        onTouchStart={(e) => {
          if (e.touches && e.touches[0]) {
            mouseX.set(e.touches[0].clientX);
          }
        }}
        onTouchMove={(e) => {
          if (e.touches && e.touches[0]) {
            mouseX.set(e.touches[0].clientX);
          }
        }}
        onTouchEnd={() => mouseX.set(Infinity)}
        onTouchCancel={() => mouseX.set(Infinity)}
        className={`dock-panel ${className}`}
        style={{ height: panelHeight }}
        role="toolbar"
        aria-label="Application dock"
      >
        {items.map((item, index) => (
          <DockItem
            key={index}
            onClick={item.onClick}
            className={item.className}
            mouseX={mouseX}
            spring={spring}
            distance={distance}
            magnification={magnification}
            baseItemSize={baseItemSize}
            label={item.label}
          >
            <DockIcon>{item.icon}</DockIcon>
            <DockLabel>{item.label}</DockLabel>
          </DockItem>
        ))}
      </div>
    </div>
  );

  if (typeof document !== 'undefined') {
    return createPortal(dockContent, document.body);
  }
  return dockContent;
}

export { Dock, DockItem, DockIcon, DockLabel };
