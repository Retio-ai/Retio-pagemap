/**
 * 12 hidden text detection techniques.
 *
 * Checks for CSS-based text concealment used in prompt injection attacks.
 * Each check returns SecuritySignals with technique name and confidence.
 */

import type { SecuritySignal, SignalSource } from './types';
import { isFlexGridChild, isCollapsedDisclosure, isLegitimateAriaHidden } from './layout-detectors';

const MIN_TEXT_LENGTH = 5; // Skip very short text (likely single chars/icons)

export function checkHiddenText(
  el: HTMLElement,
  style: CSSStyleDeclaration,
  rect: DOMRect,
  source: SignalSource,
): SecuritySignal[] {
  const signals: SecuritySignal[] = [];
  const text = (el.textContent || '').trim();
  if (text.length < MIN_TEXT_LENGTH) return signals;

  // 1. display: none
  if (style.display === 'none') {
    if (!isCollapsedDisclosure(el)) {
      signals.push({ element: el, technique: 'display-none', text, confidence: 0.7, source });
    }
  }

  // 2. visibility: hidden
  if (style.visibility === 'hidden') {
    signals.push({ element: el, technique: 'visibility-hidden', text, confidence: 0.7, source });
  }

  // 3. opacity: 0
  if (parseFloat(style.opacity) === 0) {
    signals.push({ element: el, technique: 'opacity-zero', text, confidence: 0.8, source });
  }

  // 4. color ≈ background-color (transparent text)
  if (_isTransparentText(style)) {
    signals.push({ element: el, technique: 'color-transparent', text, confidence: 0.8, source });
  }

  // 5. zero-size (width/height: 0)
  if ((rect.width < 1 || rect.height < 1) && !isFlexGridChild(el)) {
    signals.push({ element: el, technique: 'zero-size', text, confidence: 0.6, source });
  }

  // 6. offscreen (far outside viewport)
  if (_isOffscreen(rect)) {
    signals.push({ element: el, technique: 'offscreen', text, confidence: 0.6, source });
  }

  // 7. clip / clip-path hidden
  if (_isClipHidden(style)) {
    signals.push({ element: el, technique: 'clip-hidden', text, confidence: 0.7, source });
  }

  // 8. text-indent: -9999px (classic technique)
  const textIndent = parseFloat(style.textIndent);
  if (textIndent < -999) {
    signals.push({ element: el, technique: 'text-indent', text, confidence: 0.9, source });
  }

  // 9. font-size: 0
  if (parseFloat(style.fontSize) === 0) {
    signals.push({ element: el, technique: 'font-zero', text, confidence: 0.85, source });
  }

  // 10. z-index: very negative (behind everything)
  const zIndex = parseInt(style.zIndex);
  if (!isNaN(zIndex) && zIndex < -999 && style.position !== 'static') {
    signals.push({ element: el, technique: 'z-negative', text, confidence: 0.6, source });
  }

  // 11. aria-hidden="true" with significant text content
  if (el.getAttribute('aria-hidden') === 'true' && text.length > 20) {
    if (!isLegitimateAriaHidden(el)) {
      signals.push({ element: el, technique: 'aria-hidden-text', text, confidence: 0.5, source });
    }
  }

  // 12. SVG fill-opacity: 0 (v2 addition)
  if (el.namespaceURI === 'http://www.w3.org/2000/svg') {
    const fillOpacity = parseFloat((style as unknown as Record<string, string>).fillOpacity || '1');
    if (fillOpacity === 0 && text.length > 0) {
      signals.push({ element: el, technique: 'svg-fill-opacity', text, confidence: 0.8, source });
    }
  }

  return signals;
}

function _isTransparentText(style: CSSStyleDeclaration): boolean {
  const color = style.color;
  const bg = style.backgroundColor;
  // Both RGBA with alpha = 0
  if (color.includes('rgba') && color.includes(', 0)')) return true;
  // Same color as background
  if (color === bg && color !== 'rgba(0, 0, 0, 0)') return true;
  return false;
}

function _isOffscreen(rect: DOMRect): boolean {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  // Significantly outside viewport (more than 2x screen away)
  return (
    rect.right < -vw ||
    rect.left > vw * 2 ||
    rect.bottom < -vh ||
    rect.top > vh * 2
  );
}

function _isClipHidden(style: CSSStyleDeclaration): boolean {
  // clip: rect(0, 0, 0, 0) or clip-path: inset(100%)
  const clip = style.clip;
  if (clip && clip !== 'auto' && clip.includes('rect(0')) return true;
  const clipPath = style.clipPath;
  if (clipPath && clipPath.includes('inset(100%)')) return true;
  return false;
}
