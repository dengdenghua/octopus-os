// gsap-compat: a minimal, behavior-preserving replacement for the tiny subset of
// the gsap imperative API used by `magic-bento.tsx`, implemented on top of Motion's
// vanilla `animate`. This lets us drop the `gsap` dependency while keeping the
// bento hover/particle/ripple interactions intact.
//
// Why Motion's `animate` (not a hand-rolled WAAPI shim): gsap animates transform
// sub-properties (x/y/scale/rotateX/rotateY) independently and composes them; Motion
// does the same out of the box (per-value overwrite + transform composition), whereas
// raw WAAPI would require reimplementing gsap's transform cache.
import { animate as motionAnimate } from "motion";

export type Tween = { kill: () => void };

type Vars = Record<string, unknown>;

const OPTION_KEYS = new Set([
  "duration",
  "ease",
  "repeat",
  "yoyo",
  "delay",
  "onComplete",
]);

// gsap ease names -> Motion `Easing` (cubic-bezier arrays approximate the GSAP curves).
function mapEase(ease: unknown): unknown {
  switch (ease) {
    case "none":
    case "linear":
      return "linear";
    case "power2.out":
      return [0.22, 1, 0.36, 1];
    case "power2.inOut":
      return [0.45, 0, 0.55, 1];
    case "power2.in":
      return [0.55, 0.085, 0.68, 0.53];
    case "back.out":
    case "back.out(1.7)":
      return [0.34, 1.56, 0.64, 1];
    case "back.in":
    case "back.in(1.7)":
      return [0.36, 0, 0.66, -0.56];
    case "back.inOut":
    case "back.inOut(1.7)":
      return [0.68, -0.6, 0.32, 1.6];
    default:
      return "linear";
  }
}

// Strip gsap option keys, rename gsap transform names to Motion's vocabulary.
function propsOnly(vars: Vars): Vars {
  const out: Vars = {};
  for (const [key, value] of Object.entries(vars)) {
    if (OPTION_KEYS.has(key)) continue;
    out[key === "rotation" ? "rotate" : key] = value;
  }
  return out;
}

function buildOptions(vars: Vars): Record<string, unknown> {
  const opts: Record<string, unknown> = {};
  if (typeof vars.duration === "number") opts.duration = vars.duration;
  if (vars.ease !== undefined) opts.ease = mapEase(vars.ease);
  if (typeof vars.repeat === "number") {
    opts.repeat = vars.repeat === -1 ? Infinity : vars.repeat;
  }
  if (vars.yoyo === true) opts.repeatType = "reverse";
  if (typeof vars.delay === "number") opts.delay = vars.delay;
  if (typeof vars.onComplete === "function") opts.onComplete = vars.onComplete;
  return opts;
}

function to(target: Element, vars: Vars): Tween {
  const controls = motionAnimate(
    target,
    propsOnly(vars) as Parameters<typeof motionAnimate>[1],
    buildOptions(vars) as Parameters<typeof motionAnimate>[2],
  );
  return { kill: () => controls.stop() };
}

function fromTo(target: Element, fromVars: Vars, toVars: Vars): Tween {
  const options = buildOptions(toVars);
  const props: Vars = {};
  const keys = new Set([...Object.keys(fromVars), ...Object.keys(toVars)]);
  for (const key of keys) {
    if (OPTION_KEYS.has(key)) continue;
    const motionKey = key === "rotation" ? "rotate" : key;
    const from = fromVars[key];
    const to = toVars[key];
    if (from === undefined && to === undefined) continue;
    (props as Record<string, unknown>)[motionKey] = [from ?? to, to ?? from];
  }
  const controls = motionAnimate(
    target,
    props as Parameters<typeof motionAnimate>[1],
    options as Parameters<typeof motionAnimate>[2],
  );
  return { kill: () => controls.stop() };
}

export const gsap = { to, fromTo };
