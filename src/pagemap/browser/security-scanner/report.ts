/**
 * Report accumulation, threat snapshotting, and error logging.
 *
 * v2: Snapshot threats immediately (plain object), no element references.
 * This prevents GC blocking when DOM Guard removes elements later.
 */

import type { SecuritySignal, SecurityThreat, SecurityReport, ScannerError, Severity } from './types';
import { SCANNER_VERSION, MAX_THREATS_PER_REPORT } from './types';

/** Compute XPath for an element (best-effort). */
function getXPath(el: HTMLElement): string {
  const parts: string[] = [];
  let current: HTMLElement | null = el;
  while (current && current !== document.body) {
    let tag = current.tagName.toLowerCase();
    const parent: HTMLElement | null = current.parentElement;
    if (parent) {
      const siblings = parent.children;
      let index = 0;
      let count = 0;
      for (let i = 0; i < siblings.length; i++) {
        if (siblings[i].tagName === current.tagName) {
          count++;
          if (siblings[i] === current) index = count;
        }
      }
      if (count > 1) tag += `[${index}]`;
    }
    parts.unshift(tag);
    current = parent;
  }
  return '//' + parts.join('/');
}

/** Compute severity from technique + confidence. */
export function computeSeverity(signal: SecuritySignal): Severity {
  const { technique, confidence } = signal;
  // High-confidence intentional hiding techniques
  if (confidence >= 0.85) return 'high';
  if (technique === 'text-indent' || technique === 'font-zero') return 'high';
  if (technique === 'opacity-zero' || technique === 'color-transparent') return 'medium';
  if (confidence >= 0.7) return 'medium';
  return 'low';
}

/** Snapshot a signal into a plain threat object (no element references). */
export function snapshotThreat(signal: SecuritySignal): SecurityThreat {
  return {
    element_xpath: getXPath(signal.element),
    technique: signal.technique,
    text_preview: signal.text.slice(0, 120),
    confidence: signal.confidence,
    source: signal.source,
    severity: computeSeverity(signal),
  };
}

/** Mutable report accumulator. */
export class ReportAccumulator {
  private _threats: SecurityThreat[] = [];
  private _errors: ScannerError[] = [];
  private _signalsProcessed = 0;
  private _startTime = performance.now();
  private _isSafeMode = false;
  private _truncated = false;

  addThreats(threats: SecurityThreat[]): void {
    const remaining = MAX_THREATS_PER_REPORT - this._threats.length;
    if (remaining <= 0) {
      this._truncated = true;
      return;
    }
    this._threats.push(...threats.slice(0, remaining));
    if (this._threats.length >= MAX_THREATS_PER_REPORT) {
      this._truncated = true;
    }
  }

  addError(message: string, location: string): void {
    this._errors.push({
      message: message.slice(0, 200),
      location,
      timestamp: Date.now(),
    });
  }

  setSignalsProcessed(count: number): void {
    this._signalsProcessed = count;
  }

  setSafeMode(value: boolean): void {
    this._isSafeMode = value;
  }

  setTruncated(value: boolean): void {
    this._truncated = value;
  }

  /** Build the final immutable report. */
  toReport(): SecurityReport {
    return {
      threats: [...this._threats],
      errors: [...this._errors],
      signals_processed: this._signalsProcessed,
      scan_duration_ms: Math.round(performance.now() - this._startTime),
      is_safe_mode: this._isSafeMode,
      truncated: this._truncated,
      scanner_version: SCANNER_VERSION,
    };
  }
}
