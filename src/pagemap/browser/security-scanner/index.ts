/**
 * Security scanner entry point.
 *
 * Runs in a CDP Isolated World ("PageMapSecurity") — the page's
 * JavaScript cannot access window.__pagemap_security_report or
 * disconnect our MutationObserver.
 *
 * Injected via Page.addScriptToEvaluateOnNewDocument({ worldName }).
 */

import { ScannerEngine } from './engine';

(function scannerInit() {
  const engine = new ScannerEngine();

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => engine.start(), { once: true });
  } else {
    // Already loaded — scan immediately
    engine.start();
  }

  // Clean up on unload
  window.addEventListener('unload', () => engine.stop(), { once: true });
})();
