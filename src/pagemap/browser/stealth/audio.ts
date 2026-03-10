/**
 * AudioContext fingerprint defense.
 *
 * Strategy: WeakMap-based idempotent channel noise at -80dB.
 * Inaudible to humans, deterministic per AudioContext instance.
 */

import { spoofToString } from '../shared/spoof-tostring';
import { storeOriginal } from '../shared/utils';

// Per-AnalyserNode deterministic PRNG seed — ensures same node always gets same noise pattern.
const _analyserSeeds = new WeakMap<AnalyserNode, number>();
let _nextSeed = 0;

function _getAnalyserSeed(node: AnalyserNode): number {
  let seed = _analyserSeeds.get(node);
  if (seed === undefined) {
    seed = _nextSeed++;
    _analyserSeeds.set(node, seed);
  }
  return seed;
}

export function applyAudioDefense(rng: () => number): void {
  // Capture the base seed from the session RNG for deterministic per-node noise
  const baseSeed = Math.floor(rng() * 0xFFFFFFFF);
  _patchAnalyserNode(baseSeed);
  _patchAudioDestination();
}

function _patchAnalyserNode(baseSeed: number): void {
  const proto = AnalyserNode.prototype;

  // Patch getFloatFrequencyData
  const origGetFloat = proto.getFloatFrequencyData;
  storeOriginal(proto, 'getFloatFrequencyData', origGetFloat);

  proto.getFloatFrequencyData = spoofToString(function (
    this: AnalyserNode,
    array: Float32Array<ArrayBuffer>,
  ) {
    origGetFloat.call(this, array);
    // Deterministic noise: same AnalyserNode + same array index → same noise
    const nodeSeed = baseSeed ^ _getAnalyserSeed(this);
    for (let i = 0; i < array.length; i++) {
      // Deterministic per-index noise using simple hash
      const h = Math.imul(nodeSeed ^ i, 0x9e3779b9);
      array[i] += ((h >>> 16) / 65536 - 0.5) * 0.0001; // ±0.00005 (-80dB)
    }
  }, 'getFloatFrequencyData');

  // Patch getByteFrequencyData
  const origGetByte = proto.getByteFrequencyData;
  storeOriginal(proto, 'getByteFrequencyData', origGetByte);

  proto.getByteFrequencyData = spoofToString(function (
    this: AnalyserNode,
    array: Uint8Array<ArrayBuffer>,
  ) {
    origGetByte.call(this, array);
    const nodeSeed = baseSeed ^ _getAnalyserSeed(this);
    for (let i = 0; i < array.length; i++) {
      const h = Math.imul(nodeSeed ^ i, 0x9e3779b9);
      if ((h & 0x1F) === 0) { // ~3% of samples
        array[i] = Math.max(0, Math.min(255, array[i] + ((h >>> 16) & 1 ? 1 : -1)));
      }
    }
  }, 'getByteFrequencyData');

  // Patch getFloatTimeDomainData
  const origGetTimeDomain = proto.getFloatTimeDomainData;
  storeOriginal(proto, 'getFloatTimeDomainData', origGetTimeDomain);

  proto.getFloatTimeDomainData = spoofToString(function (
    this: AnalyserNode,
    array: Float32Array<ArrayBuffer>,
  ) {
    origGetTimeDomain.call(this, array);
    const nodeSeed = baseSeed ^ _getAnalyserSeed(this);
    for (let i = 0; i < array.length; i++) {
      const h = Math.imul(nodeSeed ^ i, 0x9e3779b9);
      array[i] += ((h >>> 16) / 65536 - 0.5) * 0.0001;
    }
  }, 'getFloatTimeDomainData');
}

function _patchAudioDestination(): void {
  // Normalize channelCount and maxChannelCount
  try {
    const destProto = AudioDestinationNode.prototype;
    Object.defineProperty(destProto, 'maxChannelCount', {
      get: spoofToString(() => 2, 'get maxChannelCount'),
      configurable: true,
    });
  } catch { /* not patchable */ }
}
