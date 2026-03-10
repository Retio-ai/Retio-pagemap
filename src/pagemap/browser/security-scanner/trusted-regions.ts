/**
 * 6-tier trusted region cascade.
 *
 * Determines if an element should be skipped during security scanning.
 * Based on is-protected.ts pattern with role-aware expansion.
 */

import { TRUSTED_CLASSES, TRUSTED_IDS, SKIP_TAGS, TRUSTED_TEST_ID_PREFIXES } from './trusted-data';

/**
 * Check if an element is trusted and should be skipped.
 *
 * 6-tier cascade (fast → slow):
 * 1. Tag name exclusion
 * 2. ID match
 * 3. Class match
 * 4. ARIA role check (navigation, banner, complementary → skip)
 * 5. data-testid prefix
 * 6. Ancestor check (up to 3 levels)
 */
export function isTrusted(el: HTMLElement): boolean {
  // Tier 1: Skip tag
  if (SKIP_TAGS.has(el.tagName)) return true;

  // Tier 2: Trusted ID
  if (el.id && TRUSTED_IDS.has(el.id)) return true;

  // Tier 3: Trusted class
  if (el.classList) {
    for (let i = 0; i < el.classList.length; i++) {
      if (TRUSTED_CLASSES.has(el.classList[i])) return true;
    }
  }

  // Tier 4: ARIA role — skip non-content regions
  const role = el.getAttribute('role');
  if (role === 'navigation' || role === 'banner' || role === 'complementary') {
    return true;
  }

  // Tier 5: Test infrastructure
  const testId = el.getAttribute('data-testid');
  if (testId) {
    for (const prefix of TRUSTED_TEST_ID_PREFIXES) {
      if (testId.startsWith(prefix)) return true;
    }
  }

  // Tier 6: Ancestor check (up to 3 levels)
  let parent = el.parentElement;
  for (let depth = 0; depth < 3 && parent; depth++) {
    if (parent.id && TRUSTED_IDS.has(parent.id)) return true;
    if (parent.classList) {
      for (let i = 0; i < parent.classList.length; i++) {
        if (TRUSTED_CLASSES.has(parent.classList[i])) return true;
      }
    }
    parent = parent.parentElement;
  }

  return false;
}
