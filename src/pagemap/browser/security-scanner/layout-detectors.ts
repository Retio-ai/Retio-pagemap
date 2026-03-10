/**
 * Layout-aware detection helpers.
 *
 * Understands block/flex/grid layouts to avoid false positives
 * from legitimate CSS layout techniques.
 */

/** Check if an element is in a flex/grid container where zero-size might be legitimate. */
export function isFlexGridChild(el: HTMLElement): boolean {
  const parent = el.parentElement;
  if (!parent) return false;
  const parentDisplay = getComputedStyle(parent).display;
  return parentDisplay.includes('flex') || parentDisplay.includes('grid');
}

/** Check if the element is likely a collapsed accordion/disclosure. */
export function isCollapsedDisclosure(el: HTMLElement): boolean {
  // <details> without open attribute
  if (el.tagName === 'DETAILS' && !el.hasAttribute('open')) return true;
  // aria-expanded=false
  if (el.getAttribute('aria-expanded') === 'false') return true;
  // Common collapse patterns
  const parent = el.closest('[data-state="closed"], [aria-expanded="false"]');
  return parent !== null;
}

/** Check if element has aria-hidden for a legitimate UI reason. */
export function isLegitimateAriaHidden(el: HTMLElement): boolean {
  // Icons with aria-hidden are legitimate
  if (el.tagName === 'SVG' || el.tagName === 'I' || el.tagName === 'SPAN') {
    if (el.querySelector('svg, path') || el.classList.contains('icon')) {
      return true;
    }
  }
  // Decorative images
  if (el.tagName === 'IMG' && el.getAttribute('alt') === '') return true;
  return false;
}

/** Compute the visible area of an element relative to viewport. */
export function getVisibleArea(rect: DOMRect): number {
  if (rect.width <= 0 || rect.height <= 0) return 0;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const visibleLeft = Math.max(0, rect.left);
  const visibleTop = Math.max(0, rect.top);
  const visibleRight = Math.min(vw, rect.right);
  const visibleBottom = Math.min(vh, rect.bottom);
  const w = visibleRight - visibleLeft;
  const h = visibleBottom - visibleTop;
  return w > 0 && h > 0 ? w * h : 0;
}
