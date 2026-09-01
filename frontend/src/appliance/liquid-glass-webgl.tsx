import { useEffect, useRef } from "react";

import {
  liquidGlassSurfaceBounds,
  MAX_LIQUID_GLASS_SURFACES,
  visibleLiquidGlassSurfaces,
} from "@/appliance/liquid-glass-surfaces";
import {
  LIQUID_GLASS_MOTION_EVENT,
  type LiquidGlassMotionDetail,
} from "@/appliance/liquid-glass-motion";

const WALLPAPER_URL = "/third-party/appletechie-macos/wallpaper-day2.jpg";
const MAX_LENSES = MAX_LIQUID_GLASS_SURFACES;
export const LIQUID_GLASS_INTERACTION_SETTLE_MS = 180;

export type LiquidGlassRenderState = "active" | "idle" | "suspended";

export type LiquidGlassFrameScheduler = Readonly<{
  request: (animateForMs?: number) => void;
  suspend: () => void;
  dispose: () => void;
}>;

type LiquidGlassFrameSchedulerOptions = Readonly<{
  requestFrame: (callback: FrameRequestCallback) => number;
  cancelFrame: (handle: number) => void;
  now: () => number;
  isVisible: () => boolean;
  render: (timestamp: number) => boolean;
  onStateChange?: (state: LiquidGlassRenderState) => void;
}>;

/**
 * Coalesce invalidations into one frame and keep rendering only for a short
 * interaction settle window. The compositor is completely idle afterwards.
 */
export function createLiquidGlassFrameScheduler({
  requestFrame,
  cancelFrame,
  now,
  isVisible,
  render,
  onStateChange,
}: LiquidGlassFrameSchedulerOptions): LiquidGlassFrameScheduler {
  let pendingFrame: number | null = null;
  let animateUntil = 0;
  let disposed = false;

  const setState = (state: LiquidGlassRenderState) => {
    onStateChange?.(state);
  };

  const tick = (timestamp: number) => {
    pendingFrame = null;
    if (disposed) return;
    if (!isVisible()) {
      animateUntil = 0;
      setState("suspended");
      return;
    }

    const canAnimate = render(timestamp);
    if (canAnimate && timestamp < animateUntil) {
      setState("active");
      pendingFrame = requestFrame(tick);
      return;
    }

    animateUntil = 0;
    setState("idle");
  };

  return {
    request(animateForMs = 0) {
      if (disposed) return;
      if (!isVisible()) {
        setState("suspended");
        return;
      }
      animateUntil = Math.max(animateUntil, now() + Math.max(0, animateForMs));
      if (pendingFrame !== null) return;
      setState("active");
      pendingFrame = requestFrame(tick);
    },
    suspend() {
      animateUntil = 0;
      if (pendingFrame !== null) cancelFrame(pendingFrame);
      pendingFrame = null;
      if (!disposed) setState("suspended");
    },
    dispose() {
      disposed = true;
      animateUntil = 0;
      if (pendingFrame !== null) cancelFrame(pendingFrame);
      pendingFrame = null;
    },
  };
}

export type CoverTransform = Readonly<{
  scale: number;
  cropX: number;
  cropY: number;
}>;

/**
 * Return the same centre/cover crop used by CSS background-size: cover.
 * Keeping this arithmetic explicit makes the WebGL and DOM fallback paths
 * independently testable against the real wallpaper dimensions.
 */
export function calculateCoverTransform(
  viewportWidth: number,
  viewportHeight: number,
  imageWidth: number,
  imageHeight: number,
): CoverTransform {
  if (
    viewportWidth <= 0 ||
    viewportHeight <= 0 ||
    imageWidth <= 0 ||
    imageHeight <= 0
  ) {
    return { scale: 1, cropX: 0, cropY: 0 };
  }
  const scale = Math.max(
    viewportWidth / imageWidth,
    viewportHeight / imageHeight,
  );
  return {
    scale,
    cropX: (imageWidth * scale - viewportWidth) / 2,
    cropY: (imageHeight * scale - viewportHeight) / 2,
  };
}

export function liquidIntensityScale(intensity: string | undefined): number {
  if (intensity === "weak") return 0.72;
  if (intensity === "strong") return 1.28;
  return 1;
}

export type LiquidGlassShaderTuning = Readonly<{
  ior: number;
  thickness: number;
  roughness: number;
  dispersion: number;
  absorption: number;
  opacity: number;
  saturation: number;
  tint: readonly [number, number, number];
}>;

export type LiquidGlassSurfaceOptics = Readonly<{
  thicknessScale: number;
  alpha: number;
  edgeWidth: number;
}>;

/**
 * Material depth belongs to each surface, not to a single desktop-wide slab.
 * In particular, the Dock is a thin tray and must bend the scene less than a
 * widget or window even when they share the same user-facing tuning.
 */
export function liquidGlassSurfaceOptics(
  material: string | undefined,
  dock = false,
): LiquidGlassSurfaceOptics {
  if (dock) return { thicknessScale: 0.58, alpha: 0.44, edgeWidth: 2.4 };
  switch (material) {
    case "ultra-thin":
      return { thicknessScale: 0.38, alpha: 0.34, edgeWidth: 8 };
    case "thin":
      return { thicknessScale: 0.62, alpha: 0.46, edgeWidth: 10 };
    case "thick-dark":
      return { thicknessScale: 1, alpha: 0.56, edgeWidth: 18 };
    case "ultra-thick":
      return { thicknessScale: 1.12, alpha: 0.6, edgeWidth: 20 };
    case "thick":
    default:
      return { thicknessScale: 0.92, alpha: 0.53, edgeWidth: 16 };
  }
}

function boundedNumber(
  value: string | undefined,
  fallback: number,
  min: number,
  max: number,
): number {
  const parsed = Number.parseFloat(value ?? "");
  return Number.isFinite(parsed)
    ? Math.min(max, Math.max(min, parsed))
    : fallback;
}

function hexChannel(value: string, offset: number): number {
  return Number.parseInt(value.slice(offset, offset + 2), 16) / 255;
}

export function liquidGlassShaderTuning(
  dataset: Record<string, string | undefined>,
): LiquidGlassShaderTuning {
  const transparency = boundedNumber(dataset.liquidTransparency, 72, 35, 100);
  const refraction = boundedNumber(dataset.liquidRefraction, 60, 0, 100);
  const frost = boundedNumber(dataset.liquidFrost, 32, 0, 64);
  const thickness = boundedNumber(dataset.liquidThickness, 8, 1, 24);
  const dispersion = boundedNumber(dataset.liquidDispersion, 8, 0, 40);
  const saturation = boundedNumber(dataset.liquidSaturation, 125, 70, 180);
  const tintStrength = boundedNumber(dataset.liquidTintStrength, 12, 0, 40);
  const tint = /^#[0-9a-f]{6}$/i.test(dataset.liquidTint ?? "")
    ? dataset.liquidTint!
    : "#dbeeff";

  const fill = 100 - transparency;

  return {
    // The control now maps directly to the physical refractive-index range
    // printed in the UI. Optical strength comes from thickness and incidence,
    // never from a second hidden multiplier.
    ior: 1 + refraction * 0.008,
    thickness,
    roughness: frost / 64,
    dispersion: dispersion / 1000,
    absorption: tintStrength / 40,
    // Transparency controls material fill, not the optical transmission.
    // A fully clear surface must still carry enough displaced wallpaper to
    // read as refractive glass instead of disappearing altogether.
    opacity: 0.66 + (fill / 100) * 0.32,
    saturation: saturation / 100,
    tint: [hexChannel(tint, 1), hexChannel(tint, 3), hexChannel(tint, 5)],
  };
}

export function nativeLiquidGlassOwnsOptics(
  state: string | undefined,
  backend: string | undefined,
): boolean {
  return (
    state === "ready" &&
    (backend === "appkit" || backend === "kwin-wayland-effect")
  );
}

/**
 * Rounded-box distance using a fourth-order superellipse at the corners.
 * CSS `corner-shape: squircle` uses the same continuous-corner family; the
 * exponent keeps more of the corner full than a circular arc while retaining
 * straight box edges.
 */
export function roundedSuperellipseDistance(
  x: number,
  y: number,
  halfWidth: number,
  halfHeight: number,
  radius: number,
  exponent = 4,
): number {
  const safeExponent = Math.max(2, exponent);
  const qx = Math.abs(x) - halfWidth + radius;
  const qy = Math.abs(y) - halfHeight + radius;
  const outsideX = Math.max(qx, 0);
  const outsideY = Math.max(qy, 0);
  const cornerDistance = Math.pow(
    Math.pow(outsideX, safeExponent) + Math.pow(outsideY, safeExponent),
    1 / safeExponent,
  );
  return Math.min(Math.max(qx, qy), 0) + cornerDistance - radius;
}

export type LiquidScissorRect = Readonly<{
  x: number;
  y: number;
  width: number;
  height: number;
}>;

/** Clamp a floating-point lens rectangle to the physical canvas. */
export function calculateLiquidScissor(
  left: number,
  bottom: number,
  width: number,
  height: number,
  canvasWidth: number,
  canvasHeight: number,
): LiquidScissorRect | null {
  const x = Math.max(0, Math.floor(left));
  const y = Math.max(0, Math.floor(bottom));
  const right = Math.min(canvasWidth, Math.ceil(left + width));
  const top = Math.min(canvasHeight, Math.ceil(bottom + height));
  if (right <= x || top <= y) return null;
  return { x, y, width: right - x, height: top - y };
}

const VERTEX_SHADER = `#version 300 es
in vec2 a_position;

void main() {
  gl_Position = vec4(a_position, 0.0, 1.0);
}
`;

/**
 * Separable Gaussian used to build the wallpaper's mip chain.
 *
 * `generateMipmap` produces a box-filtered chain, and sampling it with
 * textureLod for frost gives axis-aligned blocking plus visible level
 * transitions — a box kernel keeps far too much high frequency, then aliases
 * it. Frosted glass scatter is isotropic, so each level is instead built with
 * a real Gaussian. This runs once per wallpaper, so the render loop keeps
 * paying for exactly three texture samples.
 */
const BLUR_FRAGMENT_SHADER = `#version 300 es
precision highp float;

uniform sampler2D u_source;
uniform vec2 u_targetSize;
uniform vec2 u_direction;

out vec4 outColor;

const float WEIGHTS[5] = float[5](
  0.2270270270,
  0.1945945946,
  0.1216216216,
  0.0540540541,
  0.0162162162
);

void main() {
  vec2 uv = gl_FragCoord.xy / u_targetSize;
  vec2 texelStep = u_direction / u_targetSize;
  vec4 sum = texture(u_source, uv) * WEIGHTS[0];
  for (int i = 1; i < 5; i += 1) {
    vec2 offset = texelStep * float(i);
    sum += texture(u_source, uv + offset) * WEIGHTS[i];
    sum += texture(u_source, uv - offset) * WEIGHTS[i];
  }
  outColor = sum;
}
`;

const FRAGMENT_SHADER = `#version 300 es
precision highp float;

const int MAX_LENSES = ${MAX_LENSES};

uniform vec2 u_resolution;
uniform vec2 u_textureSize;
uniform vec2 u_pointer;
uniform float u_time;
uniform float u_motion;
uniform float u_intensity;
uniform float u_ior;
uniform float u_thickness;
uniform float u_roughness;
uniform float u_dispersion;
uniform float u_absorption;
uniform float u_softMaterial;
uniform float u_opacity;
uniform float u_colourSaturation;
uniform vec3 u_tint;
uniform int u_lensCount;
uniform vec4 u_lenses[MAX_LENSES];
uniform vec4 u_lensParams[MAX_LENSES];
uniform sampler2D u_wallpaper;

out vec4 outColor;

float roundedSquircleSdf(vec2 point, vec4 rect, float radius) {
  vec2 halfSize = rect.zw * 0.5;
  vec2 centre = rect.xy + halfSize;
  vec2 q = abs(point - centre) - halfSize + radius;
  vec2 outside = max(q, 0.0);
  float continuousCorner = pow(
    pow(outside.x, 4.0) + pow(outside.y, 4.0),
    0.25
  );
  return min(max(q.x, q.y), 0.0) + continuousCorner - radius;
}

vec2 coverUv(vec2 screenPoint) {
  float scale = max(
    u_resolution.x / u_textureSize.x,
    u_resolution.y / u_textureSize.y
  );
  vec2 drawnSize = u_textureSize * scale;
  vec2 crop = (drawnSize - u_resolution) * 0.5;
  return (screenPoint + crop) / drawnSize;
}

vec3 wallpaperAt(vec2 screenPoint, float lod) {
  return textureLod(u_wallpaper, coverUv(screenPoint), lod).rgb;
}

vec2 slabOffset(
  vec3 incident,
  vec3 surfaceNormal,
  float ior,
  float opticalThickness
) {
  vec3 transmitted = refract(
    incident,
    surfaceNormal,
    1.0 / max(1.001, ior)
  );
  vec2 incomingSlope = incident.xy / max(0.18, -incident.z);
  vec2 transmittedSlope = transmitted.xy / max(0.18, -transmitted.z);
  return (transmittedSlope - incomingSlope) * opticalThickness;
}

void main() {
  vec2 point = gl_FragCoord.xy;
  vec4 lens = vec4(0.0);
  vec4 params = vec4(0.0);
  float distanceToEdge = 1.0;
  bool inside = false;

  for (int i = 0; i < MAX_LENSES; i += 1) {
    if (i >= u_lensCount) break;
    float candidate = roundedSquircleSdf(
      point,
      u_lenses[i],
      u_lensParams[i].x
    );
    if (candidate <= 0.0) {
      lens = u_lenses[i];
      params = u_lensParams[i];
      distanceToEdge = candidate;
      inside = true;
      break;
    }
  }

  if (!inside) {
    outColor = vec4(0.0);
    return;
  }

  float edgeWidth = max(1.0, params.y);
  float edge = 1.0 - smoothstep(0.0, edgeWidth, -distanceToEdge);
  float broadBevel = 1.0 - smoothstep(
    0.0,
    edgeWidth * 3.2,
    -distanceToEdge
  );

  float dx = roundedSquircleSdf(point + vec2(0.8, 0.0), lens, params.x) -
    roundedSquircleSdf(point - vec2(0.8, 0.0), lens, params.x);
  float dy = roundedSquircleSdf(point + vec2(0.0, 0.8), lens, params.x) -
    roundedSquircleSdf(point - vec2(0.0, 0.8), lens, params.x);
  vec2 outward = normalize(vec2(dx, dy) + vec2(0.00001));

  vec2 lensCentre = lens.xy + lens.zw * 0.5;
  vec2 local = (point - lensCentre) / max(lens.zw, vec2(1.0));
  float dome = clamp(1.0 - dot(local * 1.42, local * 1.42), 0.0, 1.0);
  float wavePhase = point.x * 0.011 + point.y * 0.008 + u_time * 0.42;
  vec2 microSlope = vec2(sin(wavePhase), cos(wavePhase * 0.83)) *
    u_roughness * 0.035 * dome * u_motion;
  // Refraction belongs to the bevel. A strong whole-surface dome term turns the
  // panel into a magnifier and warps the content behind its centre, so the
  // curvature is kept just high enough to avoid a dead-flat middle.
  vec2 surfaceSlope = outward * broadBevel * mix(0.74, 0.52, u_softMaterial) +
    local * dome * 0.04 + microSlope;
  float slopeSquared = min(dot(surfaceSlope, surfaceSlope), 0.88);
  vec3 surfaceNormal = normalize(vec3(
    -surfaceSlope,
    sqrt(max(0.12, 1.0 - slopeSquared))
  ));
  vec3 incident = normalize(vec3(local * 0.12, -1.0));

  float materialScale = params.z;
  float opticalThickness = u_thickness * materialScale *
    mix(0.78, 1.3, broadBevel) * u_intensity *
    mix(1.0, 0.86, u_softMaterial);
  float halfDispersion = u_dispersion * 0.5;
  vec2 offsetR = slabOffset(
    incident,
    surfaceNormal,
    u_ior - halfDispersion,
    opticalThickness
  );
  vec2 offsetG = slabOffset(
    incident,
    surfaceNormal,
    u_ior,
    opticalThickness
  );
  vec2 offsetB = slabOffset(
    incident,
    surfaceNormal,
    u_ior + halfDispersion,
    opticalThickness
  );

  float effectiveRoughness = mix(
    u_roughness,
    max(u_roughness, 0.44),
    u_softMaterial
  );
  float lod = clamp(pow(effectiveRoughness, 1.25) * 6.0, 0.0, 6.0);
  vec3 redSample = wallpaperAt(point + offsetR, lod);
  vec3 greenSample = wallpaperAt(point + offsetG, lod);
  vec3 blueSample = wallpaperAt(point + offsetB, lod);
  vec3 colour = vec3(redSample.r, greenSample.g, blueSample.b);

  float cosTheta = clamp(dot(-incident, surfaceNormal), 0.0, 1.0);
  float f0 = pow((u_ior - 1.0) / (u_ior + 1.0), 2.0);
  float fresnel = f0 + (1.0 - f0) * pow(1.0 - cosTheta, 5.0);

  vec3 absorptionCoefficient = (vec3(1.0) - u_tint) *
    u_absorption * 1.35;
  float opticalPath = (u_thickness / 12.0) / max(0.24, cosTheta);
  colour *= exp(-absorptionCoefficient * opticalPath);
  float luminance = dot(colour, vec3(0.2126, 0.7152, 0.0722));
  colour = mix(vec3(luminance), colour, u_colourSaturation);

  vec2 pointerVector = point - u_pointer;
  float pointerDistance = length(pointerVector);
  vec2 lightDirection = normalize(pointerVector + vec2(0.0001));
  float facing = max(dot(outward, lightDirection), 0.0);
  float pointerReach = exp(-pointerDistance * pointerDistance /
    max(900.0, lens.z * lens.z * 0.24));
  float localSpecular = pow(facing, 10.0) * edge *
    (0.08 + pointerReach * 0.54) * u_intensity;
  float reflection = clamp(
    fresnel * mix(0.82, 0.58, u_softMaterial) + localSpecular,
    0.0,
    0.38
  );
  // Reflect a two-band procedural environment instead of a flat white. A real
  // bevel shows sky on the upward-facing rim and ground on the downward one;
  // mixing toward a single constant is what makes a rim read as a painted
  // outline rather than a reflective surface. The flat centre keeps
  // broadBevel ~ 0, so it settles on the neutral mid-tone.
  float horizon = clamp(outward.y * broadBevel * 0.5 + 0.5, 0.0, 1.0);
  vec3 envColour = mix(
    vec3(0.46, 0.51, 0.60),
    vec3(0.88, 0.94, 1.02),
    smoothstep(0.18, 0.82, horizon)
  );
  colour = mix(colour, envColour, reflection);

  // Thick glass pipes trapped light to the cut edge by total internal
  // reflection, which reads as a thin bright line exactly at the rim.
  float rimBand = exp(-pow(-distanceToEdge / 1.7, 2.0));
  float tir = rimBand * clamp(u_thickness / 12.0, 0.0, 1.6) * 0.055 *
    u_intensity;
  colour += vec3(0.90, 0.96, 1.0) * tir;

  float causticWave = 0.5 + 0.5 * sin(
    local.x * 22.0 - local.y * 15.0 + u_time * 0.7
  );
  float caustic = pointerReach * dome *
    (0.012 + causticWave * 0.018 * u_motion) * u_intensity;
  colour += vec3(0.82, 0.94, 1.0) * caustic;
  colour *= 1.0 - edge * (1.0 - facing) * 0.035;

  float alpha = clamp(
    (params.w + edge * 0.06) * u_opacity + fresnel * 0.12,
    0.0,
    0.88
  );
  outColor = vec4(colour * alpha, alpha);
}
`;

type LiquidProgram = Readonly<{
  program: WebGLProgram;
  position: number;
  resolution: WebGLUniformLocation;
  textureSize: WebGLUniformLocation;
  pointer: WebGLUniformLocation;
  time: WebGLUniformLocation;
  motion: WebGLUniformLocation;
  intensity: WebGLUniformLocation;
  ior: WebGLUniformLocation;
  thickness: WebGLUniformLocation;
  roughness: WebGLUniformLocation;
  dispersion: WebGLUniformLocation;
  absorption: WebGLUniformLocation;
  softMaterial: WebGLUniformLocation;
  opacity: WebGLUniformLocation;
  colourSaturation: WebGLUniformLocation;
  tint: WebGLUniformLocation;
  lensCount: WebGLUniformLocation;
  lenses: WebGLUniformLocation;
  lensParams: WebGLUniformLocation;
  wallpaper: WebGLUniformLocation;
}>;

function compileShader(
  gl: WebGL2RenderingContext,
  type: number,
  source: string,
): WebGLShader {
  const shader = gl.createShader(type);
  if (!shader) throw new Error("Unable to create Liquid Glass shader");
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const message = gl.getShaderInfoLog(shader) || "Unknown shader error";
    gl.deleteShader(shader);
    throw new Error(message);
  }
  return shader;
}

function createProgram(gl: WebGL2RenderingContext): LiquidProgram {
  const vertex = compileShader(gl, gl.VERTEX_SHADER, VERTEX_SHADER);
  const fragment = compileShader(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER);
  const program = gl.createProgram();
  if (!program) throw new Error("Unable to create Liquid Glass program");
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  gl.deleteShader(vertex);
  gl.deleteShader(fragment);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const message = gl.getProgramInfoLog(program) || "Unknown link error";
    gl.deleteProgram(program);
    throw new Error(message);
  }

  const uniform = (name: string) => {
    const location = gl.getUniformLocation(program, name);
    if (!location) throw new Error(`Missing Liquid Glass uniform: ${name}`);
    return location;
  };

  return {
    program,
    position: gl.getAttribLocation(program, "a_position"),
    resolution: uniform("u_resolution"),
    textureSize: uniform("u_textureSize"),
    pointer: uniform("u_pointer"),
    time: uniform("u_time"),
    motion: uniform("u_motion"),
    intensity: uniform("u_intensity"),
    ior: uniform("u_ior"),
    thickness: uniform("u_thickness"),
    roughness: uniform("u_roughness"),
    dispersion: uniform("u_dispersion"),
    absorption: uniform("u_absorption"),
    softMaterial: uniform("u_softMaterial"),
    opacity: uniform("u_opacity"),
    colourSaturation: uniform("u_colourSaturation"),
    tint: uniform("u_tint"),
    lensCount: uniform("u_lensCount"),
    lenses: uniform("u_lenses[0]"),
    lensParams: uniform("u_lensParams[0]"),
    wallpaper: uniform("u_wallpaper"),
  };
}

/** Highest mip level the frost path can reach (lod is clamped to 6.0). */
const MAX_FROST_LOD = 6;

type BlurProgram = Readonly<{
  program: WebGLProgram;
  position: number;
  source: WebGLUniformLocation;
  targetSize: WebGLUniformLocation;
  direction: WebGLUniformLocation;
}>;

function createBlurProgram(gl: WebGL2RenderingContext): BlurProgram {
  const vertex = compileShader(gl, gl.VERTEX_SHADER, VERTEX_SHADER);
  const fragment = compileShader(
    gl,
    gl.FRAGMENT_SHADER,
    BLUR_FRAGMENT_SHADER,
  );
  const program = gl.createProgram();
  if (!program) throw new Error("Unable to create Liquid Glass blur program");
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  gl.deleteShader(vertex);
  gl.deleteShader(fragment);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const log = gl.getProgramInfoLog(program);
    gl.deleteProgram(program);
    throw new Error(`Liquid Glass blur link failed: ${log ?? "unknown"}`);
  }

  const uniform = (name: string) => {
    const location = gl.getUniformLocation(program, name);
    if (!location) throw new Error(`Missing Liquid Glass blur uniform: ${name}`);
    return location;
  };

  return {
    program,
    position: gl.getAttribLocation(program, "a_position"),
    source: uniform("u_source"),
    targetSize: uniform("u_targetSize"),
    direction: uniform("u_direction"),
  };
}

function createScratchTexture(gl: WebGL2RenderingContext): WebGLTexture {
  const texture = gl.createTexture();
  if (!texture) throw new Error("Unable to create Liquid Glass scratch texture");
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  return texture;
}

/**
 * Replace the box-filtered mip chain with a Gaussian one.
 *
 * Each level is built from the previous level with two separable passes, then
 * copied into the wallpaper's mip level. Scratch textures keep the source and
 * destination distinct, because sampling and rendering the same texture is
 * undefined even across different levels.
 *
 * Returns false when the GPU cannot render to the scratch attachment, in which
 * case the caller keeps the box-filtered chain that generateMipmap produced.
 */
function buildGaussianMipChain(
  gl: WebGL2RenderingContext,
  wallpaper: WebGLTexture,
  baseWidth: number,
  baseHeight: number,
  blur: BlurProgram,
  positionBuffer: WebGLBuffer,
  image: HTMLImageElement,
): boolean {
  const framebuffer = gl.createFramebuffer();
  if (!framebuffer) return false;

  // Level 0 lives in its own texture so the chain never samples the wallpaper
  // it is writing into.
  const source = createScratchTexture(gl);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);

  const horizontal = createScratchTexture(gl);
  const vertical = createScratchTexture(gl);
  let previous = source;
  let ok = true;

  gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
  gl.useProgram(blur.program);
  gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
  gl.enableVertexAttribArray(blur.position);
  gl.vertexAttribPointer(blur.position, 2, gl.FLOAT, false, 0, 0);
  gl.uniform1i(blur.source, 0);
  gl.activeTexture(gl.TEXTURE0);

  const pass = (
    from: WebGLTexture,
    into: WebGLTexture,
    width: number,
    height: number,
    directionX: number,
    directionY: number,
  ): boolean => {
    gl.bindTexture(gl.TEXTURE_2D, into);
    gl.texImage2D(
      gl.TEXTURE_2D,
      0,
      gl.RGBA,
      width,
      height,
      0,
      gl.RGBA,
      gl.UNSIGNED_BYTE,
      null,
    );
    gl.framebufferTexture2D(
      gl.FRAMEBUFFER,
      gl.COLOR_ATTACHMENT0,
      gl.TEXTURE_2D,
      into,
      0,
    );
    if (
      gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE
    ) {
      return false;
    }
    gl.bindTexture(gl.TEXTURE_2D, from);
    gl.viewport(0, 0, width, height);
    gl.uniform2f(blur.targetSize, width, height);
    gl.uniform2f(blur.direction, directionX, directionY);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    return true;
  };

  for (let level = 1; level <= MAX_FROST_LOD; level += 1) {
    const width = Math.max(1, baseWidth >> level);
    const height = Math.max(1, baseHeight >> level);
    if (!pass(previous, horizontal, width, height, 1, 0)) {
      ok = false;
      break;
    }
    if (!pass(horizontal, vertical, width, height, 0, 1)) {
      ok = false;
      break;
    }
    // The framebuffer still points at `vertical`, so this copies the finished
    // level straight into the wallpaper's chain.
    gl.bindTexture(gl.TEXTURE_2D, wallpaper);
    gl.copyTexImage2D(gl.TEXTURE_2D, level, gl.RGBA, 0, 0, width, height, 0);
    previous = vertical;
    if (width === 1 && height === 1) break;
  }

  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  gl.deleteFramebuffer(framebuffer);
  gl.deleteTexture(source);
  gl.deleteTexture(horizontal);
  gl.deleteTexture(vertical);
  return ok;
}

function createWallpaperTexture(
  gl: WebGL2RenderingContext,
  image: HTMLImageElement,
): WebGLTexture {
  const texture = gl.createTexture();
  if (!texture) throw new Error("Unable to create Liquid Glass texture");
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(
    gl.TEXTURE_2D,
    gl.TEXTURE_MIN_FILTER,
    gl.LINEAR_MIPMAP_LINEAR,
  );
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
  gl.generateMipmap(gl.TEXTURE_2D);
  return texture;
}

/**
 * A transparent, full-viewport WebGL compositor for the few high-value glass
 * surfaces. The shader samples the exact wallpaper crop and paints only
 * inside real DOM bounds; content remains ordinary sharp DOM above it.
 */
export function MacLiquidGlassWebGL() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const root = canvas?.closest<HTMLElement>(".macos-desktop-root");
    if (!canvas || !root) return;

    if (typeof WebGL2RenderingContext === "undefined") {
      canvas.dataset.liquidWebgl = "fallback";
      root.dataset.liquidWebgl = "fallback";
      return;
    }

    const gl = canvas.getContext("webgl2", {
      alpha: true,
      antialias: false,
      premultipliedAlpha: true,
      powerPreference: "low-power",
    });
    if (!gl) {
      canvas.dataset.liquidWebgl = "fallback";
      root.dataset.liquidWebgl = "fallback";
      return;
    }

    let programInfo: LiquidProgram;
    try {
      programInfo = createProgram(gl);
    } catch (error) {
      console.warn("[echo] Liquid Glass WebGL unavailable", error);
      canvas.dataset.liquidWebgl = "fallback";
      root.dataset.liquidWebgl = "fallback";
      return;
    }

    const positionBuffer = gl.createBuffer();
    if (!positionBuffer) return;
    gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 3, -1, -1, 3]),
      gl.STATIC_DRAW,
    );
    gl.useProgram(programInfo.program);
    gl.enableVertexAttribArray(programInfo.position);
    gl.vertexAttribPointer(programInfo.position, 2, gl.FLOAT, false, 0, 0);
    gl.disable(gl.DEPTH_TEST);
    gl.uniform1i(programInfo.wallpaper, 0);

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const pointer = { x: window.innerWidth / 2, y: window.innerHeight * 0.18 };
    let texture: WebGLTexture | null = null;
    let disposed = false;
    let needsLayout = true;
    let lensCount = 0;
    let renderedFrames = 0;
    let pointerWasOverGlass = false;
    canvas.dataset.liquidOpticalModel = "snell-fresnel-beer-lambert";
    canvas.dataset.liquidTextureSamples = "3";
    canvas.dataset.liquidRenderMode = "event-driven";
    const lensData = new Float32Array(MAX_LENSES * 4);
    const lensParams = new Float32Array(MAX_LENSES * 4);
    const image = new Image();

    const nativeOwnsOptics = () =>
      nativeLiquidGlassOwnsOptics(
        root.dataset.liquidNative,
        root.dataset.liquidNativeBackend,
      );

    const resize = () => {
      const ratio = Math.min(1.35, window.devicePixelRatio || 1);
      const width = Math.max(1, Math.round(root.clientWidth * ratio));
      const height = Math.max(1, Math.round(root.clientHeight * ratio));
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
        needsLayout = true;
      }
    };

    const syncLenses = () => {
      const ratio = canvas.width / Math.max(1, root.clientWidth);
      const rootBounds = root.getBoundingClientRect();
      const lenses = visibleLiquidGlassSurfaces(root);
      lensData.fill(0);
      lensParams.fill(0);
      lenses.forEach((element, index) => {
        const bounds = liquidGlassSurfaceBounds(element);
        const style = getComputedStyle(element);
        const left = (bounds.left - rootBounds.left) * ratio;
        const bottom = (rootBounds.bottom - bounds.bottom) * ratio;
        const width = bounds.width * ratio;
        const height = bounds.height * ratio;
        const radius = Math.min(
          Number.parseFloat(style.borderTopLeftRadius) * ratio || 18 * ratio,
          width / 2,
          height / 2,
        );
        const dock = element.classList.contains("mac-dock");
        const optics = liquidGlassSurfaceOptics(
          element.dataset.liquidSurface,
          dock,
        );
        const edgeWidth = Math.min(
          optics.edgeWidth * ratio,
          height * (dock ? 0.05 : 0.26),
        );
        lensData.set([left, bottom, width, height], index * 4);
        lensParams.set(
          [radius, edgeWidth, optics.thicknessScale, optics.alpha],
          index * 4,
        );
      });
      lensCount = lenses.length;
      let drawPixels = 0;
      for (let index = 0; index < lensCount; index += 1) {
        drawPixels +=
          Math.ceil(lensData[index * 4 + 2] ?? 0) *
          Math.ceil(lensData[index * 4 + 3] ?? 0);
      }
      canvas.dataset.liquidLensCount = String(lensCount);
      canvas.dataset.liquidDrawPixels = String(drawPixels);
      canvas.dataset.liquidDrawCoverage = (
        drawPixels / Math.max(1, canvas.width * canvas.height)
      ).toFixed(4);
      needsLayout = false;
    };

    const render = (timestamp: number): boolean => {
      if (disposed || !texture) return false;
      resize();
      if (needsLayout) syncLenses();

      const ratio = canvas.width / Math.max(1, root.clientWidth);
      const rootBounds = root.getBoundingClientRect();
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.disable(gl.SCISSOR_TEST);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.useProgram(programInfo.program);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.uniform2f(programInfo.resolution, canvas.width, canvas.height);
      gl.uniform2f(
        programInfo.textureSize,
        image.naturalWidth,
        image.naturalHeight,
      );
      gl.uniform2f(
        programInfo.pointer,
        (pointer.x - rootBounds.left) * ratio,
        (rootBounds.bottom - pointer.y) * ratio,
      );
      gl.uniform1f(programInfo.time, timestamp / 1000);
      gl.uniform1f(programInfo.motion, reducedMotion.matches ? 0 : 1);
      gl.uniform1f(
        programInfo.intensity,
        liquidIntensityScale(root.dataset.liquidIntensity),
      );
      const tuning = liquidGlassShaderTuning(root.dataset);
      gl.uniform1f(programInfo.ior, tuning.ior);
      gl.uniform1f(programInfo.thickness, tuning.thickness * ratio);
      gl.uniform1f(programInfo.roughness, tuning.roughness);
      gl.uniform1f(programInfo.dispersion, tuning.dispersion);
      gl.uniform1f(programInfo.absorption, tuning.absorption);
      gl.uniform1f(
        programInfo.softMaterial,
        root.classList.contains("mac-liquid-softlight") ? 1 : 0,
      );
      gl.uniform1f(programInfo.opacity, tuning.opacity);
      gl.uniform1f(programInfo.colourSaturation, tuning.saturation);
      gl.uniform3f(programInfo.tint, ...tuning.tint);
      gl.uniform1i(programInfo.lensCount, 1);
      gl.enable(gl.SCISSOR_TEST);
      for (let index = 0; index < lensCount; index += 1) {
        const offset = index * 4;
        const scissor = calculateLiquidScissor(
          lensData[offset] ?? 0,
          lensData[offset + 1] ?? 0,
          lensData[offset + 2] ?? 0,
          lensData[offset + 3] ?? 0,
          canvas.width,
          canvas.height,
        );
        if (!scissor) continue;
        gl.scissor(scissor.x, scissor.y, scissor.width, scissor.height);
        gl.uniform4fv(
          programInfo.lenses,
          lensData.subarray(offset, offset + 4),
        );
        gl.uniform4fv(
          programInfo.lensParams,
          lensParams.subarray(offset, offset + 4),
        );
        gl.drawArrays(gl.TRIANGLES, 0, 3);
      }
      gl.disable(gl.SCISSOR_TEST);
      renderedFrames += 1;
      canvas.dataset.liquidRenderedFrames = String(renderedFrames);
      return !reducedMotion.matches && lensCount > 0;
    };

    const scheduler = createLiquidGlassFrameScheduler({
      requestFrame: (callback) => window.requestAnimationFrame(callback),
      cancelFrame: (handle) => window.cancelAnimationFrame(handle),
      now: () => performance.now(),
      isVisible: () => !document.hidden,
      render,
      onStateChange: (state) => {
        canvas.dataset.liquidRenderState = state;
      },
    });

    const requestRender = (animateForMs = 0) => {
      if (disposed || !texture) return;
      if (nativeOwnsOptics()) {
        scheduler.suspend();
        return;
      }
      scheduler.request(reducedMotion.matches ? 0 : Math.max(0, animateForMs));
    };
    const handlePointer = (event: PointerEvent) => {
      pointer.x = event.clientX;
      pointer.y = event.clientY;
      const overGlass =
        event.target instanceof Element &&
        !!event.target.closest("[data-liquid-surface]");
      if (overGlass || pointerWasOverGlass) {
        requestRender(overGlass ? LIQUID_GLASS_INTERACTION_SETTLE_MS : 0);
      }
      pointerWasOverGlass = overGlass;
    };
    const handleLayout = () => {
      needsLayout = true;
      requestRender(LIQUID_GLASS_INTERACTION_SETTLE_MS);
    };
    const handleLiquidMotion = (event: Event) => {
      const detail = (event as CustomEvent<LiquidGlassMotionDetail>).detail;
      if (!detail) return;
      if (detail.layout) needsLayout = true;
      requestRender(Math.min(500, Math.max(0, detail.settleMs)));
    };
    const handleMotionPreference = () => {
      scheduler.suspend();
      requestRender(
        reducedMotion.matches ? 0 : LIQUID_GLASS_INTERACTION_SETTLE_MS,
      );
    };
    const handleVisibility = () => {
      if (document.hidden) {
        scheduler.suspend();
        return;
      }
      needsLayout = true;
      requestRender(LIQUID_GLASS_INTERACTION_SETTLE_MS);
    };
    const handleContextLoss = (event: Event) => {
      event.preventDefault();
      root.dataset.liquidWebgl = "fallback";
      canvas.dataset.liquidWebgl = "fallback";
    };

    const resizeObserver =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(handleLayout);
    resizeObserver?.observe(root);
    const mutationObserver = new MutationObserver(handleLayout);
    mutationObserver.observe(root, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: [
        "class",
        "hidden",
        "data-liquid-intensity",
        "data-liquid-transparency",
        "data-liquid-refraction",
        "data-liquid-frost",
        "data-liquid-thickness",
        "data-liquid-dispersion",
        "data-liquid-saturation",
        "data-liquid-tint",
        "data-liquid-tint-strength",
        "data-liquid-native",
        "data-liquid-native-backend",
      ],
    });
    window.addEventListener("resize", handleLayout);
    window.addEventListener("pointermove", handlePointer, { passive: true });
    window.addEventListener(LIQUID_GLASS_MOTION_EVENT, handleLiquidMotion);
    document.addEventListener("visibilitychange", handleVisibility);
    canvas.addEventListener("webglcontextlost", handleContextLoss);
    reducedMotion.addEventListener?.("change", handleMotionPreference);

    image.decoding = "async";
    image.src = WALLPAPER_URL;
    image.onload = () => {
      if (disposed) return;
      texture = createWallpaperTexture(gl, image);

      // Swap the box-filtered chain for a Gaussian one so frost reads as
      // isotropic scatter instead of blocky mip levels. One-off cost per
      // wallpaper; the render loop still samples three times.
      let frostQuality = "box-mipmap";
      try {
        const blur = createBlurProgram(gl);
        const built = buildGaussianMipChain(
          gl,
          texture,
          image.naturalWidth,
          image.naturalHeight,
          blur,
          positionBuffer,
          image,
        );
        gl.deleteProgram(blur.program);
        if (built) frostQuality = "gaussian-mipmap";
      } catch (error) {
        console.warn("[echo] Liquid Glass Gaussian mip chain unavailable", error);
      }
      canvas.dataset.liquidFrostQuality = frostQuality;

      // The blur passes rebind the array buffer and attribute pointer; render()
      // re-sets everything else it depends on each frame.
      gl.useProgram(programInfo.program);
      gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
      gl.enableVertexAttribArray(programInfo.position);
      gl.vertexAttribPointer(programInfo.position, 2, gl.FLOAT, false, 0, 0);
      gl.uniform1i(programInfo.wallpaper, 0);

      resize();
      syncLenses();
      canvas.dataset.liquidWebgl = "ready";
      canvas.dataset.liquidRenderedFrames = "0";
      root.dataset.liquidWebgl = "ready";
      requestRender();
    };
    image.onerror = () => {
      canvas.dataset.liquidWebgl = "fallback";
      root.dataset.liquidWebgl = "fallback";
    };

    return () => {
      disposed = true;
      scheduler.dispose();
      resizeObserver?.disconnect();
      mutationObserver.disconnect();
      window.removeEventListener("resize", handleLayout);
      window.removeEventListener("pointermove", handlePointer);
      window.removeEventListener(LIQUID_GLASS_MOTION_EVENT, handleLiquidMotion);
      document.removeEventListener("visibilitychange", handleVisibility);
      canvas.removeEventListener("webglcontextlost", handleContextLoss);
      reducedMotion.removeEventListener?.("change", handleMotionPreference);
      if (texture) gl.deleteTexture(texture);
      gl.deleteBuffer(positionBuffer);
      gl.deleteProgram(programInfo.program);
      if (root.dataset.liquidWebgl === "ready") {
        delete root.dataset.liquidWebgl;
      }
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="mac-liquid-webgl"
      data-liquid-webgl="loading"
      aria-hidden="true"
    />
  );
}
