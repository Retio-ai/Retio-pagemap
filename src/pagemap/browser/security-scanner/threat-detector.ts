/**
 * Core threat detection with TreeWalker + time budget.
 *
 * v2: Uses TreeWalker instead of querySelectorAll('*') for efficiency.
 * 200ms time budget ensures 50K+ DOM nodes don't block the main thread.
 * textContent check before getComputedStyle reduces expensive style lookups.
 */

import type { SecuritySignal, SignalSource } from './types';
import { SCAN_BUDGET_MS, MUTATION_ATTRIBUTE_FILTER } from './types';
import { isTrusted } from './trusted-regions';
import { checkHiddenText } from './hidden-text-checks';
import { scanIframes } from './iframe-scanner';
import { scanShadowDom, findShadowHosts } from './shadow-dom';

/**
 * Scan a DOM subtree with a time budget.
 *
 * Returns signals found within the budget. Sets truncated=true
 * on the report if the budget was exhausted before completion.
 */
export function scanWithBudget(
  root: Node,
  budgetMs: number = SCAN_BUDGET_MS,
  source: SignalSource = 'initial_scan',
): SecuritySignal[] {
  const deadline = performance.now() + budgetMs;
  const signals: SecuritySignal[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);

  let node: Node | null;
  while ((node = walker.nextNode()) && performance.now() < deadline) {
    const el = node as HTMLElement;
    if (isTrusted(el)) continue;

    // textContent check before getComputedStyle (cheap → expensive)
    if (!el.textContent?.trim()) continue;

    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    signals.push(...checkHiddenText(el, style, rect, source));
  }

  return signals;
}

/** Check if scan completed within budget. */
export function didExceedBudget(startTime: number, budgetMs: number): boolean {
  return performance.now() - startTime > budgetMs;
}

/**
 * Full document scan: main document + iframes + shadow DOM.
 */
export function fullScan(): { signals: SecuritySignal[]; truncated: boolean } {
  const startTime = performance.now();
  const totalBudgetMs = SCAN_BUDGET_MS + 100; // 200ms main + 100ms for iframe/shadow
  const signals: SecuritySignal[] = [];

  // 1. Main document scan
  const mainSignals = scanWithBudget(document.body, SCAN_BUDGET_MS, 'initial_scan');
  signals.push(...mainSignals);

  const mainTruncated = didExceedBudget(startTime, SCAN_BUDGET_MS);

  // 2. iframe scan (only if overall budget remains)
  if (!didExceedBudget(startTime, totalBudgetMs)) {
    try {
      signals.push(...scanIframes('iframe'));
    } catch { /* iframe access error */ }
  }

  // 3. Shadow DOM scan (only if overall budget remains)
  if (!didExceedBudget(startTime, totalBudgetMs)) {
    try {
      const hosts = findShadowHosts(document);
      for (const host of hosts) {
        if (didExceedBudget(startTime, totalBudgetMs)) break;
        signals.push(...scanShadowDom(host, 'shadow_dom'));
      }
    } catch { /* shadow DOM error */ }
  }

  return { signals, truncated: mainTruncated || didExceedBudget(startTime, totalBudgetMs) };
}

/**
 * Create a MutationObserver that watches for DOM changes.
 *
 * Returns the observer (caller manages lifecycle).
 */
/** Critical tags whose injection indicates severe DOM tampering. */
const _CRITICAL_TAGS = new Set(['SCRIPT', 'IFRAME', 'OBJECT', 'EMBED']);

export function createMutationWatcher(
  onSignals: (signals: SecuritySignal[]) => void,
): MutationObserver {
  const observer = new MutationObserver((mutations) => {
    // S8-2: Classify mutation severity for cache invalidation
    // 0 = no change, 1 = normal mutation, 2 = critical (script/iframe/form-action)
    let severity: number = (window as any).__pagemap_mutation_severity as number || 0;
    for (const m of mutations) {
      if (m.type === 'childList') {
        for (const added of m.addedNodes) {
          if (added.nodeType === Node.ELEMENT_NODE) {
            const tag = (added as Element).tagName;
            if (_CRITICAL_TAGS.has(tag)) {
              severity = 2;
              break;
            }
          }
        }
      }
      if (
        m.type === 'attributes' &&
        m.attributeName === 'action' &&
        (m.target as Element).tagName === 'FORM'
      ) {
        severity = 2;
      }
      if (severity < 2) severity = Math.max(severity, 1);
    }
    (window as any).__pagemap_mutation_severity = severity;

    const signals: SecuritySignal[] = [];

    for (const mutation of mutations) {
      // New nodes added
      if (mutation.type === 'childList') {
        for (const added of mutation.addedNodes) {
          if (added.nodeType === Node.ELEMENT_NODE) {
            const el = added as HTMLElement;
            // Quick scan of added subtree (50ms budget per mutation batch)
            signals.push(...scanWithBudget(el, 50, 'mutation'));
          }
        }
      }

      // Attribute changes (style, class, hidden, etc.)
      if (mutation.type === 'attributes' && mutation.target.nodeType === Node.ELEMENT_NODE) {
        const el = mutation.target as HTMLElement;
        if (!isTrusted(el) && el.textContent?.trim()) {
          const style = getComputedStyle(el);
          const rect = el.getBoundingClientRect();
          signals.push(...checkHiddenText(el, style, rect, 'mutation'));
        }
      }
    }

    if (signals.length > 0) {
      onSignals(signals);
    }
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: MUTATION_ATTRIBUTE_FILTER,
  });

  return observer;
}
