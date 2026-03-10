/**
 * iframe security scanning (v2 addition).
 *
 * Scans same-origin iframes and srcdoc content for hidden text injection.
 * Cross-origin iframes are skipped (SecurityError).
 */

import type { SecuritySignal } from './types';
import { IFRAME_BUDGET_MS } from './types';
import { scanWithBudget } from './threat-detector';

export function scanIframes(source: 'iframe'): SecuritySignal[] {
  const iframes = document.querySelectorAll('iframe');
  const signals: SecuritySignal[] = [];

  for (const iframe of iframes) {
    // srcdoc injection check
    const srcdoc = iframe.getAttribute('srcdoc');
    if (srcdoc && srcdoc.length > 20) {
      signals.push({
        element: iframe as HTMLElement,
        technique: 'template-tag',
        text: srcdoc.slice(0, 200),
        confidence: 0.7,
        source,
      });
    }

    // Same-origin contentDocument traversal
    try {
      const doc = (iframe as HTMLIFrameElement).contentDocument;
      if (doc && doc.body) {
        signals.push(...scanWithBudget(doc.body, IFRAME_BUDGET_MS, source));
      }
    } catch {
      // Cross-origin → skip silently
    }
  }

  return signals;
}
