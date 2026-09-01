import { useEffect, useState } from "react";

import { LIQUID_GLASS_MOTION_EVENT } from "./liquid-glass-motion";

/**
 * How long the noise field keeps animating after the last motion sample.
 * An animated feTurbulence inside a backdrop-filter forces Chromium to
 * regenerate fractal noise over the whole backdrop every frame, so the
 * animation is gated to actual interaction instead of running forever.
 */
const OPTICS_SETTLE_MS = 600;

type LiquidDisplacementProfile = Readonly<{
  width: number;
  height: number;
  cornerRadius: number;
  edgeWidth: number;
  maxOffset: number;
}>;

const LIQUID_PROFILES = {
  dock: {
    width: 256,
    height: 64,
    cornerRadius: 20,
    edgeWidth: 17,
    // Echo Orbit's Dock bends the wallpaper just enough to catch a colour seam;
    // this stays below a visible "wobble" while surviving Chromium's
    // backdrop-filter quantisation.
    maxOffset: 12,
  },
  wide: {
    width: 256,
    height: 80,
    cornerRadius: 24,
    edgeWidth: 20,
    maxOffset: 10,
  },
  compact: {
    width: 128,
    height: 96,
    cornerRadius: 24,
    edgeWidth: 18,
    maxOffset: 8,
  },
} as const satisfies Record<string, LiquidDisplacementProfile>;

type LiquidProfileName = keyof typeof LIQUID_PROFILES;

export type LiquidDisplacementPixels = {
  width: number;
  height: number;
  pixels: Uint8ClampedArray;
};

export type LiquidSpecularPixels = LiquidDisplacementPixels;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function smoothStep(edge0: number, edge1: number, value: number): number {
  const amount = clamp((value - edge0) / (edge1 - edge0), 0, 1);
  return amount * amount * (3 - 2 * amount);
}

function roundedRectDistance(
  x: number,
  y: number,
  halfWidth: number,
  halfHeight: number,
  radius: number,
): number {
  const qx = Math.abs(x) - halfWidth + radius;
  const qy = Math.abs(y) - halfHeight + radius;
  return (
    Math.min(Math.max(qx, qy), 0) +
    Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) -
    radius
  );
}

/**
 * Build a signed-distance displacement map for a rounded glass lens.
 *
 * The bevel is treated as a convex squircle. Its surface slope is converted
 * with Snell's law (air IOR 1.0 -> glass IOR 1.5), then projected back into a
 * pixel offset. This is the same physical model described by the open-source
 * liquid-glass-css-svg reference, rather than a generic radial warp.
 *
 * R/G encode the X/Y sample offset, B carries an edge mask for the later
 * chromatic pass, and the clean centre remains neutral. The map is generated
 * once and reused by SVG backdrop filters; no per-frame canvas readback occurs.
 */
export function createLiquidDisplacementPixels(
  profile: LiquidDisplacementProfile,
): LiquidDisplacementPixels {
  const { width, height, cornerRadius, edgeWidth, maxOffset } = profile;
  const pixels = new Uint8ClampedArray(width * height * 4);
  const halfWidth = width / 2;
  const halfHeight = height / 2;
  const radius = Math.min(cornerRadius, halfWidth, halfHeight);
  const neutral = 128;

  const distanceAt = (x: number, y: number) =>
    roundedRectDistance(x, y, halfWidth - 0.75, halfHeight - 0.75, radius);

  for (let py = 0; py < height; py += 1) {
    for (let px = 0; px < width; px += 1) {
      const x = px + 0.5 - halfWidth;
      const y = py + 0.5 - halfHeight;
      const distance = distanceAt(x, y);
      const index = (py * width + px) * 4;

      if (distance > 0) {
        pixels[index] = neutral;
        pixels[index + 1] = neutral;
        pixels[index + 2] = 0;
        pixels[index + 3] = 255;
        continue;
      }

      const distanceFromEdge = -distance;
      if (distanceFromEdge >= edgeWidth) {
        pixels[index] = neutral;
        pixels[index + 1] = neutral;
        pixels[index + 2] = 0;
        pixels[index + 3] = 255;
        continue;
      }

      // Convex squircle profile h(t) = (1 - (1 - t)^4)^(1/4).
      // Differentiating h gives the surface slope at the bevel. Snell's law
      // then turns that slope into the inward sampling distance.
      const t = distanceFromEdge / Math.max(0.001, edgeWidth);
      const tc = Math.max(t, 0.02);
      const u = 1 - tc;
      const u4 = u ** 4;
      const slope = u ** 3 / Math.max(0.0001, (1 - u4) ** 0.75);
      const thetaAir = Math.atan(slope);
      const sinGlass = Math.sin(thetaAir) / 1.5;
      const thetaGlass = Math.asin(clamp(sinGlass, -1, 1));
      const taper = Math.min(8, edgeWidth * 0.45);
      const innerFade = smoothStep(
        0,
        1,
        (edgeWidth - distanceFromEdge) / Math.max(0.001, taper),
      );
      const outerFade = smoothStep(0, 1, distanceFromEdge);
      const physicalOffset =
        Math.tan(thetaAir - thetaGlass) * edgeWidth * innerFade * outerFade;
      const offset = Math.min(maxOffset, Math.max(0, physicalOffset));
      const edgeAmount = 1 - smoothStep(0, edgeWidth, distanceFromEdge);

      // The SDF gradient gives the closest surface normal even on rounded
      // corners, so refraction follows the glass geometry instead of radiating
      // from the centre like a generic spotlight gradient.
      const gradientX = distanceAt(x + 0.75, y) - distanceAt(x - 0.75, y);
      const gradientY = distanceAt(x, y + 0.75) - distanceAt(x, y - 0.75);
      const gradientLength = Math.hypot(gradientX, gradientY);
      const inwardX = gradientLength > 0.0001 ? -gradientX / gradientLength : 0;
      const inwardY = gradientLength > 0.0001 ? -gradientY / gradientLength : 0;
      const displacementX = inwardX * offset;
      const displacementY = inwardY * offset;

      pixels[index] = Math.round(
        clamp(0.5 + displacementX / (maxOffset * 2), 0, 1) * 255,
      );
      pixels[index + 1] = Math.round(
        clamp(0.5 + displacementY / (maxOffset * 2), 0, 1) * 255,
      );
      pixels[index + 2] = Math.round(edgeAmount * 255);
      pixels[index + 3] = 255;
    }
  }

  return { width, height, pixels };
}

/**
 * Build a soft, symmetric specular map for the convex bevel. The map is kept
 * separate from the displacement map so the highlight can be composited as a
 * screen layer without becoming another border or changing the silhouette.
 */
export function createLiquidSpecularPixels(
  profile: LiquidDisplacementProfile,
): LiquidSpecularPixels {
  const { width, height, cornerRadius, edgeWidth } = profile;
  const pixels = new Uint8ClampedArray(width * height * 4);
  const halfWidth = width / 2;
  const halfHeight = height / 2;
  const radius = Math.min(cornerRadius, halfWidth, halfHeight);
  const distanceAt = (x: number, y: number) =>
    roundedRectDistance(x, y, halfWidth - 0.75, halfHeight - 0.75, radius);
  const taper = Math.min(8, edgeWidth * 0.45);
  // Directional light from the upper-left; abs() creates the paired highlight
  // that a thin glass bevel shows on the opposite rim as well.
  const lightX = -0.58;
  const lightY = -0.82;

  for (let py = 0; py < height; py += 1) {
    for (let px = 0; px < width; px += 1) {
      const x = px + 0.5 - halfWidth;
      const y = py + 0.5 - halfHeight;
      const distance = distanceAt(x, y);
      if (distance >= 0) continue;

      const distanceFromEdge = -distance;
      if (distanceFromEdge >= edgeWidth) continue;

      const t = distanceFromEdge / Math.max(0.001, edgeWidth);
      const tc = Math.max(t, 0.02);
      const u = 1 - tc;
      const u4 = u ** 4;
      const slope = u ** 3 / Math.max(0.0001, (1 - u4) ** 0.75);
      const thetaAir = Math.atan(slope);
      const sinTheta = Math.sin(thetaAir);

      const gradientX = distanceAt(x + 0.75, y) - distanceAt(x - 0.75, y);
      const gradientY = distanceAt(x, y + 0.75) - distanceAt(x, y - 0.75);
      const gradientLength = Math.hypot(gradientX, gradientY) || 1;
      const normalX = gradientX / gradientLength;
      const normalY = gradientY / gradientLength;
      const directional = Math.pow(
        Math.abs(normalX * lightX + normalY * lightY) * sinTheta,
        6,
      );
      const rimBaseline = sinTheta * 0.05;
      const innerFade = smoothStep(
        0,
        1,
        (edgeWidth - distanceFromEdge) / Math.max(0.001, taper),
      );
      const outerFade = smoothStep(0, 1, distanceFromEdge);
      const rim = Math.min(1, sinTheta * 1.8);
      const intensity =
        Math.max(directional * 0.3, rimBaseline) * rim * innerFade * outerFade;
      const index = (py * width + px) * 4;
      pixels[index] = 255;
      pixels[index + 1] = 255;
      pixels[index + 2] = 255;
      pixels[index + 3] = Math.round(clamp(intensity, 0, 1) * 255);
    }
  }

  return { width, height, pixels };
}

function encodeDisplacementMap(
  profile: LiquidDisplacementProfile,
): string | null {
  if (typeof document === "undefined") return null;
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");
  if (!context) return null;

  const map = createLiquidDisplacementPixels(profile);
  canvas.width = map.width;
  canvas.height = map.height;
  const image = context.createImageData(map.width, map.height);
  image.data.set(map.pixels);
  context.putImageData(image, 0, 0);
  return canvas.toDataURL("image/png");
}

function encodeSpecularMap(profile: LiquidDisplacementProfile): string | null {
  if (typeof document === "undefined") return null;
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");
  if (!context) return null;

  const map = createLiquidSpecularPixels(profile);
  canvas.width = map.width;
  canvas.height = map.height;
  const image = context.createImageData(map.width, map.height);
  image.data.set(map.pixels);
  context.putImageData(image, 0, 0);
  return canvas.toDataURL("image/png");
}

function LiquidFilter({
  id,
  href,
  specularHref,
  scale,
  animated,
}: {
  id: string;
  href: string;
  specularHref: string;
  scale: number;
  animated: boolean;
}) {
  return (
    <filter
      id={id}
      x="-12%"
      y="-18%"
      width="124%"
      height="136%"
      colorInterpolationFilters="sRGB"
    >
      <feImage
        href={href}
        x="0"
        y="0"
        width="100%"
        height="100%"
        preserveAspectRatio="none"
        result="echo-liquid-map"
      />
      <feGaussianBlur
        in="echo-liquid-map"
        stdDeviation="0.35"
        result="echo-liquid-map-soft"
      />
      <feDisplacementMap
        in="SourceGraphic"
        in2="echo-liquid-map-soft"
        scale={scale}
        xChannelSelector="R"
        yChannelSelector="G"
        result="echo-liquid-edge-refracted"
      />
      {/* A low-frequency field bends the whole lens by a few pixels. The SDF
       * map above only handles the rim; this second pass is what makes a
       * wallpaper seam continue through the centre of the glass. */}
      {/* baseFrequency matches the animation's first keyframe so the surface
       * does not pop when the gate opens or closes. */}
      <feTurbulence
        type="fractalNoise"
        baseFrequency="0.008 0.011"
        numOctaves="2"
        seed="7"
        result="echo-liquid-noise"
      >
        {animated && (
          <>
            <animate
              attributeName="baseFrequency"
              values="0.008 0.011;0.012 0.007;0.009 0.014;0.008 0.011"
              dur="16s"
              repeatCount="indefinite"
              calcMode="spline"
              keySplines=".45 0 .55 1;.45 0 .55 1;.45 0 .55 1"
            />
            <animate
              attributeName="seed"
              values="7;13;3;19;5;11;7"
              dur="31s"
              repeatCount="indefinite"
              calcMode="discrete"
            />
          </>
        )}
      </feTurbulence>
      <feGaussianBlur
        in="echo-liquid-noise"
        stdDeviation="2"
        result="echo-liquid-noise-soft"
      />
      <feDisplacementMap
        in="echo-liquid-edge-refracted"
        in2="echo-liquid-noise-soft"
        scale="18"
        xChannelSelector="R"
        yChannelSelector="G"
        result="echo-liquid-warped"
      />
      {/* Keep chromatic dispersion at the perimeter only. A small RGB split
       * reads as glass thickness; a full-surface split reads as a filter. */}
      {/* Dispersion follows the same surface normal as the main refraction.
       * Sampling the low-frequency noise independently for every channel
       * creates a coloured haze; sampling the SDF map with a small signed
       * spread produces the thin prism fringe seen on a real bevel. */}
      <feDisplacementMap
        in="echo-liquid-warped"
        in2="echo-liquid-map-soft"
        scale="2.2"
        xChannelSelector="R"
        yChannelSelector="G"
        result="echo-liquid-chroma-r"
      />
      {/* The green channel is the undisplaced reference, so it reads straight
       * from echo-liquid-warped instead of paying for a scale="0" pass. */}
      <feDisplacementMap
        in="echo-liquid-warped"
        in2="echo-liquid-map-soft"
        scale="-1.7"
        xChannelSelector="R"
        yChannelSelector="G"
        result="echo-liquid-chroma-b"
      />
      <feColorMatrix
        in="echo-liquid-chroma-r"
        type="matrix"
        values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0"
        result="echo-liquid-chroma-red"
      />
      <feColorMatrix
        in="echo-liquid-warped"
        type="matrix"
        values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0"
        result="echo-liquid-chroma-green"
      />
      <feColorMatrix
        in="echo-liquid-chroma-b"
        type="matrix"
        values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0"
        result="echo-liquid-chroma-blue"
      />
      <feBlend
        in="echo-liquid-chroma-red"
        in2="echo-liquid-chroma-green"
        mode="screen"
        result="echo-liquid-chroma-rg"
      />
      <feBlend
        in="echo-liquid-chroma-rg"
        in2="echo-liquid-chroma-blue"
        mode="screen"
        result="echo-liquid-chroma-rgb"
      />
      <feColorMatrix
        in="echo-liquid-map-soft"
        type="matrix"
        values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 1 0 0"
        result="echo-liquid-edge-alpha"
      />
      <feComposite
        in="echo-liquid-chroma-rgb"
        in2="echo-liquid-edge-alpha"
        operator="in"
        result="echo-liquid-chroma-edge"
      />
      <feBlend
        in="echo-liquid-warped"
        in2="echo-liquid-chroma-edge"
        mode="screen"
        result="echo-liquid-composed"
      />
      <feImage
        href={specularHref}
        x="0"
        y="0"
        width="100%"
        height="100%"
        preserveAspectRatio="none"
        result="echo-liquid-specular"
      />
      <feGaussianBlur
        in="echo-liquid-specular"
        stdDeviation="0.45"
        result="echo-liquid-specular-soft"
      />
      {/* A restrained lighting pass follows the open-source Echo Orbit references:
       * use the bevel height as a normal field, then clip the result back to
       * the SDF edge so the highlight adds material depth without drawing a
       * second rounded outline. */}
      <feSpecularLighting
        in="echo-liquid-specular-soft"
        surfaceScale="1.55"
        specularConstant="0.22"
        specularExponent="22"
        lightingColor="#ffffff"
        result="echo-liquid-fresnel-light"
      >
        <feDistantLight azimuth="224" elevation="52" />
      </feSpecularLighting>
      <feComposite
        in="echo-liquid-fresnel-light"
        in2="echo-liquid-edge-alpha"
        operator="in"
        result="echo-liquid-fresnel-edge"
      />
      <feBlend
        in="echo-liquid-composed"
        in2="echo-liquid-fresnel-edge"
        mode="screen"
        result="echo-liquid-with-fresnel"
      />
      <feBlend
        in="echo-liquid-with-fresnel"
        in2="echo-liquid-specular-soft"
        mode="screen"
      />
    </filter>
  );
}

/**
 * A transmission-only companion for scene clones that already live inside a
 * rounded host. The host owns the silhouette and the full filter above owns
 * the visible rim; this filter only bends the cloned wallpaper, so it cannot
 * paint a second rounded glass edge inside a card or icon.
 */
function LiquidTransmissionFilter({
  id,
  href,
  scale,
  animated,
}: {
  id: string;
  href: string;
  scale: number;
  animated: boolean;
}) {
  return (
    <filter
      id={id}
      x="-12%"
      y="-18%"
      width="124%"
      height="136%"
      colorInterpolationFilters="sRGB"
    >
      <feImage
        href={href}
        x="0"
        y="0"
        width="100%"
        height="100%"
        preserveAspectRatio="none"
        result="echo-liquid-transmission-map"
      />
      <feGaussianBlur
        in="echo-liquid-transmission-map"
        stdDeviation="0.35"
        result="echo-liquid-transmission-map-soft"
      />
      <feDisplacementMap
        in="SourceGraphic"
        in2="echo-liquid-transmission-map-soft"
        scale={scale}
        xChannelSelector="R"
        yChannelSelector="G"
        result="echo-liquid-transmission-edge"
      />
      <feTurbulence
        type="fractalNoise"
        baseFrequency="0.008 0.011"
        numOctaves="2"
        seed="7"
        result="echo-liquid-transmission-noise"
      >
        {animated && (
          <>
            <animate
              attributeName="baseFrequency"
              values="0.008 0.011;0.012 0.007;0.009 0.014;0.008 0.011"
              dur="16s"
              repeatCount="indefinite"
              calcMode="spline"
              keySplines=".45 0 .55 1;.45 0 .55 1;.45 0 .55 1"
            />
            <animate
              attributeName="seed"
              values="7;13;3;19;5;11;7"
              dur="31s"
              repeatCount="indefinite"
              calcMode="discrete"
            />
          </>
        )}
      </feTurbulence>
      <feGaussianBlur
        in="echo-liquid-transmission-noise"
        stdDeviation="2"
        result="echo-liquid-transmission-noise-soft"
      />
      <feDisplacementMap
        in="echo-liquid-transmission-edge"
        in2="echo-liquid-transmission-noise-soft"
        scale="18"
        xChannelSelector="R"
        yChannelSelector="G"
      />
    </filter>
  );
}

/**
 * Shared SVG optics for the few high-value crystal glass surfaces. Existing CSS
 * transmission remains the fallback when canvas or SVG backdrop filters are
 * unavailable.
 */
export function MacLiquidGlassOptics() {
  const [maps, setMaps] = useState<
    Partial<
      Record<LiquidProfileName, { displacement: string; specular: string }>
    >
  >({});
  const [motionEnabled, setMotionEnabled] = useState(() =>
    typeof window === "undefined"
      ? true
      : !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const [interacting, setInteracting] = useState(false);

  useEffect(() => {
    const nextMaps: Partial<
      Record<LiquidProfileName, { displacement: string; specular: string }>
    > = {};
    for (const [name, profile] of Object.entries(LIQUID_PROFILES) as Array<
      [LiquidProfileName, LiquidDisplacementProfile]
    >) {
      const displacement = encodeDisplacementMap(profile);
      const specular = encodeSpecularMap(profile);
      if (displacement && specular) {
        nextMaps[name] = { displacement, specular };
      }
    }
    setMaps(nextMaps);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setMotionEnabled(!media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);

  // The same motion signal that drives the WebGL scheduler opens this gate, so
  // both layers idle together instead of the SVG noise animating on its own.
  useEffect(() => {
    if (typeof window === "undefined") return;
    let settleTimer: ReturnType<typeof setTimeout> | undefined;
    const onMotion = () => {
      setInteracting(true);
      if (settleTimer) clearTimeout(settleTimer);
      settleTimer = setTimeout(() => setInteracting(false), OPTICS_SETTLE_MS);
    };
    window.addEventListener(LIQUID_GLASS_MOTION_EVENT, onMotion);
    return () => {
      window.removeEventListener(LIQUID_GLASS_MOTION_EVENT, onMotion);
      if (settleTimer) clearTimeout(settleTimer);
    };
  }, []);

  const ready =
    Object.keys(maps).length === Object.keys(LIQUID_PROFILES).length;
  const animated = motionEnabled && interacting;

  return (
    <svg
      aria-hidden="true"
      focusable="false"
      className="mac-liquid-optics-defs"
      data-liquid-optics={ready ? "ready" : "fallback"}
      data-liquid-optics-motion={animated ? "active" : "idle"}
    >
      <defs>
        {maps.dock && (
          <LiquidFilter
            id="echo-liquid-dock-refraction"
            href={maps.dock.displacement}
            specularHref={maps.dock.specular}
            scale={LIQUID_PROFILES.dock.maxOffset * 2}
            animated={animated}
          />
        )}
        {maps.dock && (
          <LiquidTransmissionFilter
            id="echo-liquid-dock-transmission"
            href={maps.dock.displacement}
            scale={LIQUID_PROFILES.dock.maxOffset * 2}
            animated={animated}
          />
        )}
        {maps.wide && (
          <LiquidFilter
            id="echo-liquid-wide-refraction"
            href={maps.wide.displacement}
            specularHref={maps.wide.specular}
            scale={LIQUID_PROFILES.wide.maxOffset * 2}
            animated={animated}
          />
        )}
        {maps.compact && (
          <LiquidFilter
            id="echo-liquid-compact-refraction"
            href={maps.compact.displacement}
            specularHref={maps.compact.specular}
            scale={LIQUID_PROFILES.compact.maxOffset * 2}
            animated={animated}
          />
        )}
        {maps.compact && (
          <LiquidTransmissionFilter
            id="echo-liquid-compact-transmission"
            href={maps.compact.displacement}
            scale={LIQUID_PROFILES.compact.maxOffset * 2}
            animated={animated}
          />
        )}
      </defs>
    </svg>
  );
}
