import {
  BROWSER_OPEN_URL_REQUEST_KEY,
  BROWSER_OPEN_URL_REQUEST_EVENT,
  BROWSER_OPEN_URL_ACK_EVENT,
  type BrowserOpenUrlAck,
  type BrowserOpenUrlRequest,
} from "@/components/browser/browser-store";
import {
  getLinkOpenTarget,
  type LinkOpenTarget,
} from "@/core/settings/automation-preferences";
import { swallow } from "@/core/utils/log";
import { BROWSER_WORKSPACE_ROUTE } from "@/core/workspace/sidebar-routing";

export interface OpenTargetOptions extends Omit<BrowserOpenUrlRequest, "url"> {
  target?: LinkOpenTarget;
}

export type OpenTargetResult = "external" | "in_app" | "blocked";

const BROWSER_OPEN_ACK_TIMEOUT_MS = 1_500;

export function isWebTarget(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

async function openExternally(url: string): Promise<OpenTargetResult> {
  if (window.echo?.app?.openExternal) {
    try {
      await window.echo.app.openExternal(url);
      return "external";
    } catch (error) {
      // A desktop bridge can disappear during an app reload. Keep the
      // explicit external contract by falling through to the web platform
      // instead of turning an ordinary link click into an unhandled rejection.
      swallow(error, "open-external");
    }
  }
  const opened = window.open(url, "_blank", "noopener,noreferrer");
  if (opened) {
    opened.opener = null;
    return "external";
  }
  return "blocked";
}

function requestId(): string {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return `browser_open_${crypto.randomUUID()}`;
  }
  return `browser_open_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
}

function waitForBrowserAck(id: string): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (accepted: boolean) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      window.removeEventListener(BROWSER_OPEN_URL_ACK_EVENT, onAck);
      resolve(accepted);
    };
    const onAck = (event: Event) => {
      const ack = (event as CustomEvent<BrowserOpenUrlAck>).detail;
      if (ack?.requestId === id) finish(ack.accepted === true);
    };
    const timer = window.setTimeout(
      () => finish(false),
      BROWSER_OPEN_ACK_TIMEOUT_MS,
    );
    window.addEventListener(BROWSER_OPEN_URL_ACK_EVENT, onAck);
  });
}

function removePendingRequest(id: string): void {
  try {
    const raw = window.localStorage.getItem(BROWSER_OPEN_URL_REQUEST_KEY);
    if (!raw) return;
    const pending = JSON.parse(raw) as Partial<BrowserOpenUrlRequest>;
    if (pending.requestId === id) {
      window.localStorage.removeItem(BROWSER_OPEN_URL_REQUEST_KEY);
    }
  } catch (error) {
    swallow(error, "storage");
  }
}

export async function openTarget(
  rawUrl: string,
  options: OpenTargetOptions = {},
): Promise<OpenTargetResult> {
  const url = rawUrl.trim();
  if (typeof window === "undefined" || !isWebTarget(url)) return "blocked";
  const target = options.target ?? getLinkOpenTarget();
  if (target === "external") return openExternally(url);

  const request: BrowserOpenUrlRequest = {
    url,
    requestId: requestId(),
    ...(options.title ? { title: options.title } : {}),
    ...(options.device ? { device: options.device } : {}),
    ...(options.source ? { source: options.source } : {}),
    ...(options.sessionId ? { sessionId: options.sessionId } : {}),
  };
  const previousHash = window.location.hash;
  try {
    window.localStorage.setItem(
      BROWSER_OPEN_URL_REQUEST_KEY,
      JSON.stringify(request),
    );
    const acknowledged = waitForBrowserAck(request.requestId!);
    window.dispatchEvent(
      new CustomEvent<BrowserOpenUrlRequest>(BROWSER_OPEN_URL_REQUEST_EVENT, {
        detail: request,
      }),
    );
    window.location.hash = `#${BROWSER_WORKSPACE_ROUTE}`;
    if (await acknowledged) return "in_app";
    removePendingRequest(request.requestId!);
    if (window.location.hash === `#${BROWSER_WORKSPACE_ROUTE}`) {
      window.location.hash = previousHash;
    }
    return openExternally(url);
  } catch (error) {
    swallow(error, "storage");
    return openExternally(url);
  }
}

export function shouldRouteAnchorClick(
  event: Pick<
    MouseEvent,
    "button" | "metaKey" | "ctrlKey" | "shiftKey" | "altKey"
  >,
): boolean {
  return (
    event.button === 0 &&
    !event.metaKey &&
    !event.ctrlKey &&
    !event.shiftKey &&
    !event.altKey
  );
}
