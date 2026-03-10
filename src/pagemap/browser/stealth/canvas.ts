/**
 * Canvas fingerprint defense.
 *
 * Strategy: LSB 5% noise on toDataURL/toBlob + clone pattern.
 * The clone pattern creates a fresh canvas, copies content, then reads from
 * the clone — ensuring consistent noise per session (seeded PRNG).
 */

import { spoofToString } from '../shared/spoof-tostring';
import { storeOriginal, getOriginal, fnv1a, seededRandom } from '../shared/utils';

// Cache noised canvas content to ensure idempotent reads.
// Key: canvas instance → last content hash + noised dataURL.
const _noiseCache = new WeakMap<HTMLCanvasElement, { hash: number; applied: boolean }>();

export function applyCanvasDefense(rng: () => number): void {
  const proto = HTMLCanvasElement.prototype;

  // Store originals
  storeOriginal(proto, 'toDataURL', proto.toDataURL);
  storeOriginal(proto, 'toBlob', proto.toBlob);

  // Noise injection for 2D context getImageData
  const ctx2dProto = CanvasRenderingContext2D.prototype;
  const origGetImageData = ctx2dProto.getImageData;
  storeOriginal(ctx2dProto, 'getImageData', origGetImageData);

  ctx2dProto.getImageData = spoofToString(function (
    this: CanvasRenderingContext2D,
    ...args: Parameters<typeof origGetImageData>
  ) {
    const imageData = origGetImageData.apply(this, args);
    _applyNoise(imageData.data, rng);
    return imageData;
  }, 'getImageData');

  // toDataURL: delegate to original after noise is applied via getImageData
  proto.toDataURL = spoofToString(function (
    this: HTMLCanvasElement,
    ...args: Parameters<typeof proto.toDataURL>
  ) {
    const orig = getOriginal<typeof proto.toDataURL>(proto, 'toDataURL')!;
    // Apply noise by reading then writing back via clone
    _noiseViaClone(this, rng);
    return orig.apply(this, args);
  }, 'toDataURL');

  // toBlob: same pattern
  proto.toBlob = spoofToString(function (
    this: HTMLCanvasElement,
    ...args: Parameters<typeof proto.toBlob>
  ) {
    const orig = getOriginal<typeof proto.toBlob>(proto, 'toBlob')!;
    _noiseViaClone(this, rng);
    return orig.apply(this, args);
  }, 'toBlob');
}

/** Apply LSB noise to ~5% of pixels. */
function _applyNoise(data: Uint8ClampedArray, rng: () => number): void {
  const len = data.length;
  // Process every 4th pixel (RGBA stride) with 5% probability
  for (let i = 0; i < len; i += 4) {
    if (rng() < 0.05) {
      // Flip LSB of R channel only (minimal visual impact)
      data[i] = data[i] ^ 1;
    }
  }
}

/**
 * Apply noise to canvas pixels — idempotent per content.
 *
 * Uses a content hash to seed a per-canvas PRNG so that:
 * 1. Same canvas content → same noise (deterministic)
 * 2. canvas.toDataURL() === canvas.toDataURL() (idempotent)
 * 3. Different canvas content → different noise
 */
function _noiseViaClone(canvas: HTMLCanvasElement, _rng: () => number): void {
  if (canvas.width === 0 || canvas.height === 0) return;
  try {
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const origGetImageData = getOriginal<typeof ctx.getImageData>(
      CanvasRenderingContext2D.prototype, 'getImageData'
    );
    if (!origGetImageData) return;
    const imageData = origGetImageData.call(ctx, 0, 0, canvas.width, canvas.height);

    // Hash first 256 bytes of pixel data for content fingerprint
    const sample = imageData.data.slice(0, 256);
    let contentHash = 0;
    for (let i = 0; i < sample.length; i++) {
      contentHash = ((contentHash << 5) - contentHash + sample[i]) | 0;
    }

    // Check cache — skip if already noised with same content
    const cached = _noiseCache.get(canvas);
    if (cached && cached.hash === contentHash && cached.applied) return;

    // Deterministic PRNG seeded from content hash (not session RNG)
    const contentRng = seededRandom(contentHash);
    _applyNoise(imageData.data, contentRng);
    ctx.putImageData(imageData, 0, 0);
    _noiseCache.set(canvas, { hash: contentHash, applied: true });
  } catch {
    // Canvas may be tainted (cross-origin)
  }
}
