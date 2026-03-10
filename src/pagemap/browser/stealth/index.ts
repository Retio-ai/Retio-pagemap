/**
 * Stealth bundle entry point.
 *
 * Runs in main world via context.add_init_script().
 * Applies anti-fingerprinting defenses in a deterministic,
 * session-stable order. Seed is injected by Python before this script.
 *
 * Design principles:
 * - Standardization first, noise second (Brave farbling defeat research 2025)
 * - WebGPU disabled entirely (70% re-identification, ACM 2025)
 * - Each defense is isolated — failure in one doesn't block others
 */

import { seededRandom, getSessionSeed } from '../shared/utils';
import { installToStringProxy } from '../shared/spoof-tostring';
import { applyHardwareDefense } from './hardware';
import { applyCanvasDefense } from './canvas';
import { applyWebGLDefense } from './webgl';
import { applyFontDefense } from './font';
import { applyAudioDefense } from './audio';

(function stealthInit() {
  const seed = getSessionSeed();
  const rng = seededRandom(seed);

  // Install toString proxy first (all subsequent spoofs depend on it)
  installToStringProxy();

  // Hardware normalization (no RNG needed — static values)
  applyHardwareDefense();

  // RNG-dependent defenses — each wrapped in try/catch for resilience
  const defenses: [string, () => void][] = [
    ['canvas', () => applyCanvasDefense(rng)],
    ['webgl',  () => applyWebGLDefense(rng)],
    ['font',   () => applyFontDefense(rng)],
    ['audio',  () => applyAudioDefense(rng)],
  ];

  for (const [name, apply] of defenses) {
    try {
      apply();
    } catch (e) {
      // Silent failure — stealth should never crash the page
      if (typeof console !== 'undefined' && console.debug) {
        console.debug(`[pagemap:stealth] ${name} defense failed:`, e);
      }
    }
  }
})();
