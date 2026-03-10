/**
 * Trusted element identification data.
 *
 * Elements matching these patterns are skipped during scanning.
 * Based on common UI frameworks, analytics, and legitimate hidden content.
 */

/** CSS classes that indicate legitimate hidden content. */
export const TRUSTED_CLASSES: ReadonlySet<string> = new Set([
  // Screen reader only
  'sr-only', 'visually-hidden', 'screen-reader-text', 'screenreader-text',
  'assistive-text', 'a11y-hidden', 'sr-only-focusable',
  // Skip links
  'skip-link', 'skip-to-content', 'skipnav', 'skip-nav',
  // React / framework
  'react-aria-live-announcer',
  // Common UI frameworks
  'offscreen', 'clip-text',
]);

/** Element IDs that indicate legitimate hidden content. */
export const TRUSTED_IDS: ReadonlySet<string> = new Set([
  'skip-to-content', 'skip-nav', 'skiplink',
  'aria-live-region', 'announcements',
  '__next', '__nuxt', // framework roots
]);

/** Tag names that should never be scanned. */
export const SKIP_TAGS: ReadonlySet<string> = new Set([
  'SCRIPT', 'STYLE', 'NOSCRIPT', 'META', 'LINK', 'HEAD',
  'BR', 'HR', 'IMG', 'INPUT', 'SELECT', 'TEXTAREA',
  'VIDEO', 'AUDIO', 'CANVAS', 'IFRAME', // iframes scanned separately
  'SVG', // SVG handled in hidden-text-checks
]);

/** data-testid patterns that indicate test infrastructure. */
export const TRUSTED_TEST_ID_PREFIXES: readonly string[] = [
  'test-', 'qa-', 'e2e-', 'cypress-', 'playwright-',
];
