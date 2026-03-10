/**
 * Signal bus with 50ms leading-edge debounce and priority dedup.
 *
 * Collects SecuritySignals from multiple sources and flushes them
 * to the threat detector in batched, deduplicated form.
 */

import type { SecuritySignal, SecurityThreat } from './types';
import { DEBOUNCE_MS, MAX_THREATS_PER_REPORT } from './types';
import { snapshotThreat } from './report';

type FlushCallback = (threats: SecurityThreat[]) => void;

export class SignalBus {
  private _pending: SecuritySignal[] = [];
  private _timer: ReturnType<typeof setTimeout> | null = null;
  private _onFlush: FlushCallback;
  private _signalCount = 0;

  constructor(onFlush: FlushCallback) {
    this._onFlush = onFlush;
  }

  /** Push signals for batched processing. */
  push(signals: SecuritySignal[]): void {
    if (signals.length === 0) return;
    this._pending.push(...signals);
    this._signalCount += signals.length;

    // Leading-edge debounce: flush immediately on first signal, then wait
    if (!this._timer) {
      this._flush();
      this._timer = setTimeout(() => {
        this._timer = null;
        if (this._pending.length > 0) {
          this._flush();
        }
      }, DEBOUNCE_MS);
    }
  }

  /** Force immediate flush (for final report). */
  flushNow(): void {
    if (this._timer) {
      clearTimeout(this._timer);
      this._timer = null;
    }
    if (this._pending.length > 0) {
      this._flush();
    }
  }

  get signalCount(): number {
    return this._signalCount;
  }

  private _flush(): void {
    const signals = this._dedup(this._pending);
    this._pending = [];

    const threats = signals
      .map(snapshotThreat)
      .slice(0, MAX_THREATS_PER_REPORT);

    if (threats.length > 0) {
      this._onFlush(threats);
    }
  }

  /** Deduplicate by element XPath + technique, keeping highest confidence. */
  private _dedup(signals: SecuritySignal[]): SecuritySignal[] {
    const map = new Map<string, SecuritySignal>();
    for (const signal of signals) {
      const key = `${signal.technique}:${signal.text.slice(0, 50)}`;
      const existing = map.get(key);
      if (!existing || signal.confidence > existing.confidence) {
        map.set(key, signal);
      }
    }
    return Array.from(map.values());
  }
}
