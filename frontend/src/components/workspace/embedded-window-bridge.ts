import { createContext, useContext } from "react";

export type EmbeddedWindowAction = "close" | "minimize" | "maximize";

export const EchoDesktopWindowChromeContext = createContext(false);

export function useEchoDesktopWindowChrome(): boolean {
  return useContext(EchoDesktopWindowChromeContext);
}

export function isEmbeddedWindow(): boolean {
  return typeof window !== "undefined" && window.self !== window.top;
}

export function sendEmbeddedWindowControl(action: EmbeddedWindowAction) {
  if (!isEmbeddedWindow()) return;
  window.parent.postMessage({ type: "echo-os:window-control", action }, "*");
}

export function sendEmbeddedWindowDrag(
  phase: "start" | "move" | "end",
  screenX: number,
  screenY: number,
) {
  if (!isEmbeddedWindow()) return;
  window.parent.postMessage(
    { type: "echo-os:window-drag", phase, screenX, screenY },
    "*",
  );
}
