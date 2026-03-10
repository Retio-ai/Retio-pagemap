/**
 * Shadow DOM traversal for security scanning.
 *
 * Only scans open shadow roots (closed shadow roots return null).
 * Depth-limited to MAX_SHADOW_DEPTH to prevent infinite recursion.
 */

import type { SecuritySignal, SignalSource } from './types';
import { MAX_SHADOW_DEPTH } from './types';
import { isTrusted } from './trusted-regions';
import { checkHiddenText } from './hidden-text-checks';

/**
 * Recursively scan open shadow roots for hidden text.
 */
export function scanShadowDom(
  root: Element,
  source: SignalSource,
  depth: number = 0,
): SecuritySignal[] {
  if (depth >= MAX_SHADOW_DEPTH) return [];

  const signals: SecuritySignal[] = [];

  // Check this element's shadow root
  const shadowRoot = root.shadowRoot;
  if (shadowRoot) {
    const walker = document.createTreeWalker(
      shadowRoot,
      NodeFilter.SHOW_ELEMENT,
    );

    let node: Node | null;
    while ((node = walker.nextNode())) {
      const el = node as HTMLElement;
      if (isTrusted(el)) continue;
      if (!el.textContent?.trim()) continue;

      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      signals.push(...checkHiddenText(el, style, rect, 'shadow_dom'));

      // Recurse into nested shadow roots
      if (el.shadowRoot) {
        signals.push(...scanShadowDom(el, source, depth + 1));
      }
    }
  }

  return signals;
}

/**
 * Find all elements with open shadow roots in the document.
 */
export function findShadowHosts(root: Document | ShadowRoot): Element[] {
  const hosts: Element[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
  let node: Node | null;
  while ((node = walker.nextNode())) {
    if ((node as Element).shadowRoot) {
      hosts.push(node as Element);
    }
  }
  return hosts;
}
