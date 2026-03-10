/**
 * Scanner engine — orchestrates guard, threat-detector, signal-bus, and report.
 *
 * Lifecycle:
 * 1. Initial full scan on DOMContentLoaded (or immediately if already loaded)
 * 2. MutationObserver for SPA/dynamic changes
 * 3. Report stored on window.__pagemap_security_report (read via CDP)
 */

import type { SecurityThreat, SecurityReport } from './types';
import { ScannerGuard } from './guard';
import { SignalBus } from './signal-bus';
import { ReportAccumulator } from './report';
import { fullScan, createMutationWatcher } from './threat-detector';

declare const window: Window & {
  __pagemap_security_report?: SecurityReport;
};

export class ScannerEngine {
  private _guard = new ScannerGuard();
  private _report = new ReportAccumulator();
  private _observer: MutationObserver | null = null;
  private _bus: SignalBus;

  constructor() {
    this._bus = new SignalBus((threats: SecurityThreat[]) => {
      this._report.addThreats(threats);
      this._publishReport();
    });
  }

  /** Start the scanner. */
  start(): void {
    // Initial scan
    this._guard.run(
      () => {
        const { signals, truncated } = fullScan();
        if (truncated) this._report.setTruncated(true);
        this._bus.push(signals);
      },
      'initial_scan',
      (msg, loc) => this._report.addError(msg, loc),
    );

    // MutationObserver (disabled in safe mode)
    if (!this._guard.isSafeMode) {
      this._guard.run(
        () => {
          this._observer = createMutationWatcher((signals) => {
            if (this._guard.isSafeMode) {
              // Safe mode activated mid-stream — disconnect
              this._observer?.disconnect();
              this._observer = null;
              this._report.setSafeMode(true);
              this._publishReport();
              return;
            }
            this._guard.run(
              () => this._bus.push(signals),
              'mutation_handler',
              (msg, loc) => this._report.addError(msg, loc),
            );
          });
        },
        'mutation_observer_setup',
        (msg, loc) => this._report.addError(msg, loc),
      );
    } else {
      this._report.setSafeMode(true);
    }

    // Flush and publish final report
    this._bus.flushNow();
    this._report.setSignalsProcessed(this._bus.signalCount);
    this._publishReport();
  }

  /** Stop the scanner and clean up. */
  stop(): void {
    this._observer?.disconnect();
    this._observer = null;
    this._bus.flushNow();
    this._report.setSignalsProcessed(this._bus.signalCount);
    this._publishReport();
  }

  private _publishReport(): void {
    window.__pagemap_security_report = this._report.toReport();
  }
}
