/**
 * Font metrics fingerprint defense.
 *
 * Strategy: Deterministic ±0.05px noise on measureText and getBoundingClientRect.
 * CSS-safe — visually imperceptible but breaks fingerprint correlation.
 * Font enumeration locked to prevent side-channel detection.
 */

import { spoofToString } from '../shared/spoof-tostring';
import { storeOriginal, fnv1a } from '../shared/utils';

export function applyFontDefense(rng: () => number): void {
  _patchMeasureText(rng);
  _patchGetBoundingClientRect(rng);
  _lockFontEnumeration();
}

function _patchMeasureText(rng: () => number): void {
  const proto = CanvasRenderingContext2D.prototype;
  const origMeasureText = proto.measureText;
  storeOriginal(proto, 'measureText', origMeasureText);

  proto.measureText = spoofToString(function (
    this: CanvasRenderingContext2D,
    text: string,
  ): TextMetrics {
    const metrics = origMeasureText.call(this, text);

    // Deterministic noise based on text content (session-stable)
    const textHash = fnv1a(text);
    const noise = ((textHash % 100) - 50) * 0.001; // ±0.05px

    // Wrap TextMetrics with noised values
    return new Proxy(metrics, {
      get(target, prop) {
        const val = Reflect.get(target, prop);
        if (typeof val === 'number' && typeof prop === 'string') {
          // Apply noise to dimension properties
          if (['width', 'actualBoundingBoxLeft', 'actualBoundingBoxRight',
               'actualBoundingBoxAscent', 'actualBoundingBoxDescent',
               'fontBoundingBoxAscent', 'fontBoundingBoxDescent'].includes(prop)) {
            return val + noise;
          }
        }
        return typeof val === 'function' ? val.bind(target) : val;
      },
    });
  }, 'measureText');
}

function _patchGetBoundingClientRect(rng: () => number): void {
  const origGetBCR = Element.prototype.getBoundingClientRect;
  storeOriginal(Element.prototype, 'getBoundingClientRect', origGetBCR);

  Element.prototype.getBoundingClientRect = spoofToString(function (
    this: Element,
  ): DOMRect {
    const rect = origGetBCR.call(this);

    // Deterministic noise from element's position in DOM
    const tag = this.tagName || '';
    const cls = this.className || '';
    const hash = fnv1a(tag + cls);
    const noise = ((hash % 100) - 50) * 0.001; // ±0.05px

    return new DOMRect(
      rect.x + noise,
      rect.y + noise,
      rect.width + noise,
      rect.height + noise,
    );
  }, 'getBoundingClientRect');
}

function _lockFontEnumeration(): void {
  // Block document.fonts.check to prevent font side-channel enumeration
  try {
    if (document.fonts && typeof document.fonts.check === 'function') {
      const origCheck = document.fonts.check.bind(document.fonts);
      storeOriginal(document.fonts, 'check', origCheck);
      document.fonts.check = spoofToString((_font: string, _text?: string) => {
        // Always return true — prevents enumeration by checking which fonts exist
        return true;
      }, 'check');
    }
  } catch { /* fonts API not available */ }
}
