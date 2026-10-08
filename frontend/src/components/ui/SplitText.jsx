import React, { useMemo } from 'react';

/**
 * SplitText — Resilient React Bits SplitText Implementation.
 *
 * Renders individual character spans managed directly by React Fiber,
 * completely avoiding DOM-mutating libraries that trigger unhandled
 * reconciliation crashes (e.g. NotFoundError removeChild) during fast
 * multi-turn chat stream updates.
 */
const SplitText = ({
  text = '',
  className = '',
  delay = 40,
  duration = 0.5,
  tag = 'p',
  textAlign = 'left',
  onLetterAnimationComplete,
}) => {
  if (!text) return null;
  const Tag = tag || 'p';
  const chars = useMemo(() => Array.from(String(text)), [text]);

  return (
    <Tag
      className={`split-parent ${className}`}
      style={{
        display: 'inline-flex',
        flexWrap: 'wrap',
        textAlign,
        overflow: 'hidden',
      }}
    >
      {chars.map((char, index) => {
        const isSpace = char === ' ';
        return (
          <span
            key={index}
            className="split-char"
            style={{
              display: 'inline-block',
              whiteSpace: 'pre',
              animation: `orbisCharFadeIn ${duration}s cubic-bezier(0.16, 1, 0.3, 1) ${index * (delay / 1000)}s both`,
              willChange: 'transform, opacity',
            }}
            onAnimationEnd={
              index === chars.length - 1 && onLetterAnimationComplete
                ? onLetterAnimationComplete
                : undefined
            }
          >
            {isSpace ? '\u00A0' : char}
          </span>
        );
      })}
    </Tag>
  );
};

export default SplitText;
