/**
 * Shared utilities for stealth and scanner bundles.
 */

/** Simple seeded PRNG (Mulberry32). Deterministic per session. */
export function seededRandom(seed: number): () => number {
  let s = seed | 0;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Get session seed from global or generate one. */
export function getSessionSeed(): number {
  return (
    (globalThis as Record<string, unknown>).__pagemap_stealth_seed as number ??
    crypto.getRandomValues(new Uint32Array(1))[0]
  );
}

/**
 * Store a reference to the original (un-patched) function.
 * Uses a WeakMap keyed on the target object to avoid global leaks.
 */
const _originals = new WeakMap<object, Map<string, unknown>>();

export function storeOriginal<T>(target: object, key: string, value: T): void {
  let map = _originals.get(target);
  if (!map) {
    map = new Map();
    _originals.set(target, map);
  }
  if (!map.has(key)) {
    map.set(key, value);
  }
}

export function getOriginal<T>(target: object, key: string): T | undefined {
  return _originals.get(target)?.get(key) as T | undefined;
}

/** FNV-1a 32-bit hash for short strings. */
export function fnv1a(str: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    hash ^= str.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}
