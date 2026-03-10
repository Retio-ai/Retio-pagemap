/**
 * Type definitions and constants for the security scanner.
 */

export type SignalSource = 'initial_scan' | 'mutation' | 'intersection' | 'iframe' | 'shadow_dom';

export type ThreatTechnique =
  | 'color-transparent'
  | 'display-none'
  | 'visibility-hidden'
  | 'opacity-zero'
  | 'zero-size'
  | 'offscreen'
  | 'clip-hidden'
  | 'text-indent'
  | 'font-zero'
  | 'z-negative'
  | 'aria-hidden-text'
  | 'template-tag'
  | 'svg-fill-opacity';

export type Severity = 'critical' | 'high' | 'medium' | 'low';

export interface SecuritySignal {
  element: HTMLElement;
  technique: ThreatTechnique;
  text: string;
  confidence: number;
  source: SignalSource;
}

export interface SecurityThreat {
  element_xpath: string;
  technique: ThreatTechnique;
  text_preview: string;
  confidence: number;
  source: SignalSource;
  severity: Severity;
}

export interface ScannerError {
  message: string;
  location: string;
  timestamp: number;
}

export interface SecurityReport {
  threats: SecurityThreat[];
  errors: ScannerError[];
  signals_processed: number;
  scan_duration_ms: number;
  is_safe_mode: boolean;
  truncated: boolean;
  scanner_version: string;
}

// Constants
export const SCANNER_VERSION = '0.1.0';
export const SCAN_BUDGET_MS = 200;
export const IFRAME_BUDGET_MS = 50;
export const DEBOUNCE_MS = 50;
export const SAFE_MODE_THRESHOLD = 3;
export const SAFE_MODE_WINDOW_MS = 60_000;
export const MAX_SHADOW_DEPTH = 5;
export const MAX_THREATS_PER_REPORT = 100;

export const MUTATION_ATTRIBUTE_FILTER: string[] = [
  'style', 'class', 'hidden',
  'aria-label', 'aria-hidden', 'title',
  'srcdoc',
  'data-content', 'data-text', 'data-prompt',
];
