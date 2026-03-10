/**
 * Hardware fingerprint normalization.
 *
 * Strategy: Static standardization to common values (4 cores, 8 GB).
 * This defeats navigator.hardwareConcurrency / deviceMemory enumeration.
 */

import { spoofToString } from '../shared/spoof-tostring';

export function applyHardwareDefense(): void {
  // navigator.hardwareConcurrency → 4 (most common laptop value)
  try {
    Object.defineProperty(navigator, 'hardwareConcurrency', {
      get: spoofToString(() => 4, 'get hardwareConcurrency'),
      configurable: true,
    });
  } catch { /* already frozen */ }

  // navigator.deviceMemory → 8 (common mid-range)
  try {
    if ('deviceMemory' in navigator) {
      Object.defineProperty(navigator, 'deviceMemory', {
        get: spoofToString(() => 8, 'get deviceMemory'),
        configurable: true,
      });
    }
  } catch { /* already frozen */ }

  // navigator.platform → consistent with User-Agent
  // Detect OS from UA to avoid UA/platform mismatch (detection vector)
  try {
    const ua = navigator.userAgent || '';
    let platform = 'Win32'; // default fallback
    if (ua.includes('Macintosh') || ua.includes('Mac OS')) {
      platform = 'MacIntel';
    } else if (ua.includes('Linux')) {
      platform = 'Linux x86_64';
    }
    Object.defineProperty(navigator, 'platform', {
      get: spoofToString(() => platform, 'get platform'),
      configurable: true,
    });
  } catch { /* already frozen */ }

  // WebGPU: disable entirely (70% re-identification rate, spoofing infeasible)
  try {
    if ('gpu' in navigator) {
      Object.defineProperty(navigator, 'gpu', {
        get: spoofToString(() => undefined, 'get gpu'),
        configurable: true,
      });
    }
  } catch { /* already frozen */ }

  // connection.downlink / rtt / effectiveType normalization
  try {
    const conn = (navigator as unknown as Record<string, unknown>).connection;
    if (conn && typeof conn === 'object') {
      const connObj = conn as Record<string, unknown>;
      Object.defineProperty(connObj, 'downlink', { get: () => 10, configurable: true });
      Object.defineProperty(connObj, 'rtt', { get: () => 50, configurable: true });
      Object.defineProperty(connObj, 'effectiveType', { get: () => '4g', configurable: true });
    }
  } catch { /* unsupported */ }
}
