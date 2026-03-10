/**
 * Error boundary / guard for the security scanner.
 *
 * Tracks failures within a time window. If the scanner crashes
 * 3 times within 60 seconds, it enters safe mode (disables
 * MutationObserver and switches to periodic-only scanning).
 */

import { SAFE_MODE_THRESHOLD, SAFE_MODE_WINDOW_MS } from './types';

export class ScannerGuard {
  private _failures: number[] = [];
  private _safeMode = false;

  get isSafeMode(): boolean {
    return this._safeMode;
  }

  /**
   * Record a failure. Returns true if safe mode was just activated.
   */
  recordFailure(): boolean {
    const now = Date.now();
    this._failures.push(now);

    // Prune old failures outside window
    const cutoff = now - SAFE_MODE_WINDOW_MS;
    this._failures = this._failures.filter(t => t >= cutoff);

    if (this._failures.length >= SAFE_MODE_THRESHOLD && !this._safeMode) {
      this._safeMode = true;
      return true; // just entered safe mode
    }
    return false;
  }

  /**
   * Run a scanner function with error boundary.
   * Returns the result or undefined on failure.
   */
  run<T>(fn: () => T, errorLocation: string, onError?: (msg: string, loc: string) => void): T | undefined {
    try {
      return fn();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      const enteredSafe = this.recordFailure();
      if (onError) {
        onError(msg, errorLocation);
      }
      if (enteredSafe && typeof console !== 'undefined') {
        console.warn('[pagemap:scanner] Entered safe mode due to repeated errors');
      }
      return undefined;
    }
  }
}
