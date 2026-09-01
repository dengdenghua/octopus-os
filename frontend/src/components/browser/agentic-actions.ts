/* Implementation note. */

import { swallow } from "@/core/utils/log";
import {
  controlInterruptionDetail,
  runControlSessionAction,
  type ControlIndicatorMode,
  type ControlSessionOptions,
  type ControlStopReason,
} from "@/core/control-session";
import type { EchoElectronAPI } from "@/types/electron";
import type { WebviewTabHandle } from "./webview-tab";

export type AgentAction =
  | { type: "click"; selector: string }
  | { type: "type"; selector: string; text: string; clear?: boolean }
  | { type: "hover"; selector: string }
  | { type: "scroll"; deltaY?: number; deltaX?: number; selector?: string }
  | { type: "wait"; selector: string; timeout?: number }
  | { type: "press"; key: string }
  | { type: "pageAction"; id: string; confirm?: boolean }
  | { type: "pageInput"; id: string; text: string; clear?: boolean }
  | {
      type: "pageCapability";
      id: string;
      input?: Record<string, unknown>;
      confirm?: boolean;
    }
  | { type: "navigate"; url: string }
  | { type: "extract" }
  | { type: "snapshot" }
  | { type: "aria"; maxDepth?: number };

export interface ActionResult {
  action: AgentAction;
  ok: boolean;
  detail?: unknown;
  error?: string;
  /* Implementation note. */
  attempt?: number;
}

export type BrowserControlIndicatorMode = ControlIndicatorMode;

export type BrowserControlOptions = ControlSessionOptions;

const ACTION_BLOCK_RE = /```action\s*\n([\s\S]*?)```/g;

/* Implementation note. */
export function parseActions(text: string): AgentAction[] {
  const out: AgentAction[] = [];
  let m: RegExpExecArray | null;
  // Implementation note.
  ACTION_BLOCK_RE.lastIndex = 0;
  while ((m = ACTION_BLOCK_RE.exec(text)) !== null) {
    const block = m[1] ?? "";
    for (const rawLine of block.split("\n")) {
      const line = rawLine.trim();
      if (!line || line.startsWith("//") || line.startsWith("#")) continue;
      try {
        const parsed = JSON.parse(line) as AgentAction;
        if (parsed && typeof parsed.type === "string") {
          out.push(parsed);
        }
      } catch (e) {
        swallow(e);
      }
    }
  }
  return out;
}

/* Implementation note. */
export async function runAction(
  api: EchoElectronAPI,
  webContentsId: number,
  action: AgentAction,
  helpers: {
    navigate?: (url: string) => void;
  } = {},
): Promise<ActionResult> {
  const base: ActionResult = { action, ok: false };
  try {
    switch (action.type) {
      case "click": {
        const r = await api.browser.click(webContentsId, action.selector);
        return { ...base, ok: r.ok, detail: r, error: r.error };
      }
      case "type": {
        const r = await api.browser.type(
          webContentsId,
          action.selector,
          action.text,
          { clear: action.clear },
        );
        return { ...base, ok: r.ok, detail: r, error: r.error };
      }
      case "hover": {
        const r = await api.browser.hover(webContentsId, action.selector);
        return { ...base, ok: r.ok, detail: r, error: r.error };
      }
      case "scroll": {
        const r = await api.browser.scroll(webContentsId, {
          selector: action.selector,
          deltaX: action.deltaX,
          deltaY: action.deltaY,
        });
        return { ...base, ok: r.ok, detail: r, error: r.error };
      }
      case "wait": {
        const r = await api.browser.waitFor(
          webContentsId,
          action.selector,
          action.timeout,
        );
        return { ...base, ok: r.ok, detail: r, error: r.error };
      }
      case "press": {
        const r = await api.browser.pressKey(webContentsId, action.key);
        return { ...base, ok: r.ok, detail: r };
      }
      case "pageAction":
      case "pageCapability":
      case "pageInput": {
        return {
          ...base,
          ok: false,
          error:
            "page agent actions are only available in the browser relay path",
        };
      }
      case "navigate": {
        if (!helpers.navigate) {
          return { ...base, ok: false, error: "navigate helper not provided" };
        }
        helpers.navigate(action.url);
        return { ...base, ok: true, detail: { url: action.url } };
      }
      case "extract": {
        const r = await api.browser.extractText(webContentsId);
        return { ...base, ok: true, detail: r };
      }
      case "snapshot": {
        const r = await api.browser.capturePage(webContentsId);
        // Implementation note.
        return {
          ...base,
          ok: true,
          detail: { width: r.width, height: r.height, dataUrl: r.dataUrl },
        };
      }
      case "aria": {
        const r = await api.browser.getAriaTree(webContentsId, {
          maxDepth: action.maxDepth,
        });
        return { ...base, ok: !!r.ok, detail: r, error: r.error };
      }
      default: {
        return { ...base, ok: false, error: `unknown action type` };
      }
    }
  } catch (e) {
    swallow(e);
    return {
      ...base,
      ok: false,
      error: e instanceof Error ? e.message : String(e),
    };
  }
}

// A wedged webview IPC channel leaves action promises pending forever,
// which froze the assistant loop in `busy` with only a manual Stop as
// the way out. Racing every action against a deadline turns the hang
// into a normal failed result: the loop's catch/finally path reports
// it and unfreezes.
export const ACTION_TIMEOUT_MS = 30_000;

export async function withActionTimeout<T>(
  promise: Promise<T>,
  label: string,
  timeoutMs: number = ACTION_TIMEOUT_MS,
): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<never>((_, reject) => {
        timer = setTimeout(
          () =>
            reject(
              new Error(
                `action timeout (${Math.round(timeoutMs / 1000)}s): ${label}`,
              ),
            ),
          timeoutMs,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

/* Implementation note. */
export async function runActionWithRetry(
  api: EchoElectronAPI,
  webContentsId: number,
  action: AgentAction,
  helpers: { navigate?: (url: string) => void } = {},
  maxAttempts = 2,
): Promise<ActionResult> {
  let last: ActionResult = { action, ok: false };
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    last = await runAction(api, webContentsId, action, helpers);
    if (last.ok) return { ...last, attempt };
    // Implementation note.
    if (action.type !== "click" && action.type !== "wait") break;
    await new Promise((r) => setTimeout(r, 250 * (attempt + 1)));
  }
  return { ...last, attempt: maxAttempts - 1 };
}

function interruptedActionResult(
  action: AgentAction,
  reason: ControlStopReason,
  control?: BrowserControlOptions,
): ActionResult {
  return {
    action,
    ok: false,
    error: `browser control interrupted: ${reason}`,
    detail: {
      ...controlInterruptionDetail(reason, control),
      code: "browser_control_interrupted",
    },
  };
}

export async function runBrowserActionWithControl(
  action: AgentAction,
  run: () => Promise<ActionResult>,
  control: BrowserControlOptions = {},
): Promise<ActionResult> {
  return runControlSessionAction(action, run, {
    control,
    interrupted: (reason) => interruptedActionResult(action, reason, control),
  });
}

/* Implementation note. */
export function formatResults(
  results: ActionResult[],
  pageInfo?: {
    url: string;
    title: string;
    text: string;
    pageAgent?: unknown;
  },
): string {
  const lines: string[] = ["[操作执行结果]"];
  for (const r of results) {
    const tag = r.ok ? "✓" : "✗";
    const summary = (() => {
      if (r.error) return `error=${r.error}`;
      if (r.detail && typeof r.detail === "object") {
        const d = r.detail as Record<string, unknown>;
        // Implementation note.
        const compact: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(d)) {
          if (k === "dataUrl" && typeof v === "string") {
            compact[k] = `<${v.length} chars>`;
          } else if (k === "pageAgent") {
            compact[k] = "<pageAgent omitted; see current page section>";
          } else if (k === "nodes" && Array.isArray(v)) {
            compact[k] = `<${v.length} aria nodes>`;
          } else if (typeof v === "string" && v.length > 200) {
            compact[k] = `${v.slice(0, 200)}…`;
          } else {
            compact[k] = v;
          }
        }
        return JSON.stringify(compact);
      }
      return r.detail != null ? String(r.detail) : "ok";
    })();
    lines.push(
      `${tag} ${r.action.type}${r.attempt ? ` (retry x${r.attempt})` : ""}: ${summary}`,
    );
  }
  if (pageInfo) {
    lines.push("", "[当前页面]");
    lines.push(`URL: ${pageInfo.url}`);
    lines.push(`标题: ${pageInfo.title}`);
    if (pageInfo.text) {
      const snippet = pageInfo.text.slice(0, 1500);
      lines.push(`摘要: ${snippet}${pageInfo.text.length > 1500 ? "…" : ""}`);
    }
    if (pageInfo.pageAgent) {
      lines.push(
        `pageAgent: ${JSON.stringify(pageInfo.pageAgent).slice(0, 6000)}`,
      );
    }
  }
  return lines.join("\n");
}

/* Implementation note. */
export const BROWSER_ACTION_PROTOCOL = `\
[Echo AI 浏览器协议]
你不只是回答问题,还可以**直接操作当前网页**。需要操作时,在回复里
包含 \`\`\`action ... \`\`\` 代码块,块里每行一条 JSON 指令:

这些浏览器动作通过 WebView/Playwright 的 DOM 上下文执行,不移动真实系统鼠标,
也不占用电脑自动化的 desktop lease。网页任务优先使用本协议,不要退回真实鼠标坐标操作。

支持的指令:
- {"type":"click","selector":"CSS 选择器"}
- {"type":"type","selector":"CSS","text":"...","clear":true}     // clear=true 先清空
- {"type":"hover","selector":"CSS"}
- {"type":"scroll","deltaY":500}                                 // 或 {"selector":"CSS"} 滚到元素
- {"type":"wait","selector":"CSS","timeout":8000}                // 等元素出现
- {"type":"press","key":"Enter"}                                  // Enter / Tab / Escape / ArrowDown 等
- {"type":"pageAction","id":"pageAgent.actions id"}                // our pages: semantic click
- {"type":"pageInput","id":"pageAgent.fields id","text":"...","clear":true}
- {"type":"pageCapability","id":"pageAgent.capabilities id","input":{}} // our pages: business capability
- {"type":"navigate","url":"https://..."}
- {"type":"extract"}                                              // 重新抓页面文本
- {"type":"aria"}                                                 // 拿 ARIA 语义树(用元素结构推 selector)

高风险 pageAction/pageCapability 会返回 requiresConfirmation,不要自己加 confirm:true；先正常回复用户,说明需要用户确认。
执行后系统会把结果以 user message 的形式发给你,你看完决定下一步。
任务完成时,**正常回答用户**(不要再发 action 块),loop 自动停止。
最大连续 action 轮次 8 轮,超过会强停。每条 action 简洁、明确、可验证。\
`;

// Run an AgentAction against a WebviewTabHandle (the React webview component's
// imperative handle), as opposed to runAction() above which drives the Electron
// browser by webContentsId. Shared here so BOTH the standalone browser assistant
// and the embedded browser-preview surface drive their webview through one
// dispatcher (the embedded preview's webview was previously uncontrollable).
export async function runBrowserHandleAction(
  handle: WebviewTabHandle,
  action: AgentAction,
  options: { confirmDangerous?: boolean } = {},
): Promise<ActionResult> {
  const base: ActionResult = { action, ok: false };
  try {
    switch (action.type) {
      case "navigate":
        handle.loadURL(action.url);
        return { ...base, ok: true, detail: { url: action.url } };
      case "extract":
        return { ...base, ok: true, detail: await handle.extractText() };
      case "snapshot":
        return { ...base, ok: true, detail: await handle.capturePage() };
      case "click":
        return browserActionResult(
          base,
          await handle.runAction("click", { selector: action.selector }),
        );
      case "type":
        return browserActionResult(
          base,
          await handle.runAction("type", {
            selector: action.selector,
            text: action.text,
            clear: action.clear,
          }),
        );
      case "hover":
        return browserActionResult(
          base,
          await handle.runAction("hover", { selector: action.selector }),
        );
      case "scroll":
        return browserActionResult(
          base,
          await handle.runAction("scroll", {
            selector: action.selector,
            deltaX: action.deltaX,
            deltaY: action.deltaY,
            y: action.deltaY,
          }),
        );
      case "wait":
        return browserActionResult(
          base,
          await handle.runAction("wait", {
            selector: action.selector,
            timeout: action.timeout,
          }),
        );
      case "press":
        return browserActionResult(
          base,
          await handle.runAction("press", { key: action.key }),
        );
      case "pageAction":
        return browserActionResult(
          base,
          await handle.runAction("pageAction", {
            id: action.id,
            confirm: options.confirmDangerous === true,
          }),
        );
      case "pageInput":
        return browserActionResult(
          base,
          await handle.runAction("pageInput", {
            id: action.id,
            text: action.text,
            clear: action.clear,
          }),
        );
      case "pageCapability":
        return browserActionResult(
          base,
          await handle.runAction("pageCapability", {
            id: action.id,
            input: action.input,
            confirm: options.confirmDangerous === true,
          }),
        );
      case "aria":
        return browserActionResult(
          base,
          await handle.runAction("aria", { maxDepth: action.maxDepth }),
        );
      default:
        return { ...base, ok: false, error: "unknown action type" };
    }
  } catch (e) {
    swallow(e);
    return {
      ...base,
      ok: false,
      error: e instanceof Error ? e.message : String(e),
    };
  }
}

export async function runBrowserHandleActionWithControl(
  handle: WebviewTabHandle,
  action: AgentAction,
  options: {
    confirmDangerous?: boolean;
    control?: BrowserControlOptions;
  } = {},
): Promise<ActionResult> {
  return runBrowserActionWithControl(
    action,
    () =>
      runBrowserHandleAction(handle, action, {
        confirmDangerous: options.confirmDangerous,
      }),
    options.control,
  );
}

function browserActionResult(
  base: ActionResult,
  detail: Record<string, unknown>,
): ActionResult {
  const error = typeof detail.error === "string" ? detail.error : undefined;
  return { ...base, ok: detail.ok !== false, detail, error };
}
