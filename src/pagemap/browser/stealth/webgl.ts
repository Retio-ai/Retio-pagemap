/**
 * WebGL fingerprint defense.
 *
 * Strategy: 8-profile GPU pool (seed % 8) + readPixels noise.
 * Standardization-first approach — picks a plausible GPU profile rather
 * than trying to add noise to renderer strings.
 */

import { spoofToString } from '../shared/spoof-tostring';
import { storeOriginal, getOriginal } from '../shared/utils';

interface GpuProfile {
  vendor: string;
  renderer: string;
  unmaskedVendor: string;
  unmaskedRenderer: string;
}

const GPU_PROFILES: GpuProfile[] = [
  { vendor: 'Google Inc. (NVIDIA)', renderer: 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650, OpenGL 4.5)', unmaskedVendor: 'Google Inc. (NVIDIA)', unmaskedRenderer: 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 SUPER, OpenGL 4.5)' },
  { vendor: 'Google Inc. (NVIDIA)', renderer: 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060, OpenGL 4.5)', unmaskedVendor: 'Google Inc. (NVIDIA)', unmaskedRenderer: 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060, OpenGL 4.5)' },
  { vendor: 'Google Inc. (AMD)', renderer: 'ANGLE (AMD, AMD Radeon RX 580, OpenGL 4.5)', unmaskedVendor: 'Google Inc. (AMD)', unmaskedRenderer: 'ANGLE (AMD, AMD Radeon RX 580, OpenGL 4.5)' },
  { vendor: 'Google Inc. (AMD)', renderer: 'ANGLE (AMD, AMD Radeon RX 6600 XT, OpenGL 4.5)', unmaskedVendor: 'Google Inc. (AMD)', unmaskedRenderer: 'ANGLE (AMD, AMD Radeon RX 6600 XT, OpenGL 4.5)' },
  { vendor: 'Google Inc. (Intel)', renderer: 'ANGLE (Intel, Intel(R) UHD Graphics 630, OpenGL 4.5)', unmaskedVendor: 'Google Inc. (Intel)', unmaskedRenderer: 'ANGLE (Intel, Intel(R) UHD Graphics 630, OpenGL 4.5)' },
  { vendor: 'Google Inc. (Intel)', renderer: 'ANGLE (Intel, Intel(R) Iris(R) Xe Graphics, OpenGL 4.5)', unmaskedVendor: 'Google Inc. (Intel)', unmaskedRenderer: 'ANGLE (Intel, Intel(R) Iris(R) Xe Graphics, OpenGL 4.5)' },
  { vendor: 'Google Inc. (Apple)', renderer: 'ANGLE (Apple, Apple M1, OpenGL 4.1)', unmaskedVendor: 'Google Inc. (Apple)', unmaskedRenderer: 'ANGLE (Apple, Apple M1, OpenGL 4.1)' },
  { vendor: 'Google Inc. (Apple)', renderer: 'ANGLE (Apple, Apple M2, OpenGL 4.1)', unmaskedVendor: 'Google Inc. (Apple)', unmaskedRenderer: 'ANGLE (Apple, Apple M2, OpenGL 4.1)' },
];

// WebGL extension constants
const UNMASKED_VENDOR_WEBGL = 0x9245;
const UNMASKED_RENDERER_WEBGL = 0x9246;

export function applyWebGLDefense(rng: () => number): void {
  const profileIdx = Math.floor(rng() * GPU_PROFILES.length) % GPU_PROFILES.length;
  const profile = GPU_PROFILES[profileIdx];

  // Patch both WebGL and WebGL2
  for (const ctxProto of [
    WebGLRenderingContext.prototype,
    WebGL2RenderingContext.prototype,
  ]) {
    _patchGetParameter(ctxProto, profile);
    _patchReadPixels(ctxProto, rng);
  }
}

function _patchGetParameter(
  proto: WebGLRenderingContext | WebGL2RenderingContext,
  profile: GpuProfile,
): void {
  const origGetParameter = proto.getParameter;
  storeOriginal(proto, 'getParameter', origGetParameter);

  (proto as { getParameter: typeof origGetParameter }).getParameter = spoofToString(function (
    this: WebGLRenderingContext | WebGL2RenderingContext,
    pname: GLenum,
  ) {
    // Intercept debug extension queries
    if (pname === UNMASKED_VENDOR_WEBGL) return profile.unmaskedVendor;
    if (pname === UNMASKED_RENDERER_WEBGL) return profile.unmaskedRenderer;
    return origGetParameter.call(this, pname);
  }, 'getParameter');
}

function _patchReadPixels(
  proto: WebGLRenderingContext | WebGL2RenderingContext,
  rng: () => number,
): void {
  const origReadPixels = proto.readPixels;
  storeOriginal(proto, 'readPixels', origReadPixels);

  // Use 'any' for the variadic override to avoid complex union type issues
  (proto as unknown as Record<string, unknown>).readPixels = spoofToString(function (
    this: WebGLRenderingContext | WebGL2RenderingContext,
    ...args: unknown[]
  ) {
    (origReadPixels as Function).apply(this, args);
    // Apply LSB noise to the pixel buffer (last non-number argument)
    const pixels = args[args.length - 1];
    if (pixels instanceof Uint8Array) {
      for (let i = 0; i < pixels.length; i += 4) {
        if (rng() < 0.03) {
          pixels[i] = pixels[i] ^ 1;
        }
      }
    }
  }, 'readPixels');
}
