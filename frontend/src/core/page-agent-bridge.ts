type PageAgentElementKind = "button" | "link" | "input" | "textarea" | "select";
type PageAgentRisk = "low" | "medium" | "high";

export interface PageAgentCapability {
  id: string;
  label: string;
  description: string;
  risk: PageAgentRisk;
  riskReasons: string[];
  requiresConfirmation: boolean;
  inputSchema?: Record<string, unknown>;
}

interface PageAgentCapabilityRegistration extends PageAgentCapability {
  run: (input?: Record<string, unknown>) => Promise<unknown> | unknown;
}

interface PageAgentElement {
  id: string;
  kind: PageAgentElementKind;
  label: string;
  selector: string;
  disabled: boolean;
  risk: PageAgentRisk;
  riskReasons: string[];
  requiresConfirmation: boolean;
  required?: boolean;
  placeholder?: string;
  inputType?: string;
  valueSummary?: string;
}

interface PageAgentSnapshot {
  ok: true;
  app: "echo";
  url: string;
  title: string;
  route: string;
  text: string;
  actions: PageAgentElement[];
  fields: PageAgentElement[];
  capabilities: PageAgentCapability[];
  forms: Array<{
    id: string;
    label: string;
    fields: string[];
    submitActions: string[];
  }>;
}

interface EchoPageAgentBridge {
  version: string;
  snapshot: () => PageAgentSnapshot;
  run: (
    action:
      | { type: "click"; id: string; confirm?: boolean }
      | { type: "input"; id: string; text: string; clear?: boolean }
      | { type: "submit"; id: string; confirm?: boolean }
      | {
          type: "capability";
          id: string;
          input?: Record<string, unknown>;
          confirm?: boolean;
        },
  ) => Promise<{
    ok: boolean;
    error?: string;
    result?: unknown;
    url?: string;
    title?: string;
    changed?: boolean;
    before?: PageAgentRunState;
    after?: PageAgentRunState;
    risk?: PageAgentRisk;
    riskReasons?: string[];
    requiresConfirmation?: boolean;
  }>;
}

interface PageAgentRunState {
  url: string;
  title: string;
  route: string;
  textHash: string;
  actionCount: number;
  fieldCount: number;
  focusedId?: string;
}

declare global {
  interface Window {
    __echoPageAgent?: EchoPageAgentBridge;
  }
}

const capabilityRegistry = new Map<string, PageAgentCapabilityRegistration>();

export function registerPageAgentCapability(
  capability: PageAgentCapabilityRegistration,
): () => void {
  capabilityRegistry.set(capability.id, capability);
  return () => {
    if (capabilityRegistry.get(capability.id) === capability) {
      capabilityRegistry.delete(capability.id);
    }
  };
}

function textOf(el: Element): string {
  const aria = el.getAttribute("aria-label")?.trim();
  if (aria) return aria;
  const title = el.getAttribute("title")?.trim();
  if (title) return title;
  const id = el.getAttribute("id");
  if (id) {
    const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
    const labelText = label?.textContent?.trim();
    if (labelText) return labelText;
  }
  const wrappedLabel = el.closest("label")?.textContent?.trim();
  if (wrappedLabel) return wrappedLabel;
  const placeholder = (el as HTMLInputElement).placeholder?.trim();
  if (placeholder) return placeholder;
  return (el.textContent || "").replace(/\s+/g, " ").trim();
}

function classifyRisk(
  el: Element,
  label: string,
  kind: PageAgentElementKind,
): {
  risk: PageAgentRisk;
  riskReasons: string[];
} {
  const text = [
    label,
    el.getAttribute("aria-label"),
    el.getAttribute("title"),
    el.getAttribute("data-echo-risk"),
    el.getAttribute("type"),
    el.getAttribute("href"),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  const reasons: string[] = [];
  const highPatterns: Array<[string, string]> = [
    ["delete", "delete"],
    ["remove", "remove"],
    ["destroy", "destroy"],
    ["logout", "logout"],
    ["sign out", "logout"],
    ["submit", "submit"],
    ["send", "send"],
    ["publish", "publish"],
    ["支付", "payment"],
    ["付款", "payment"],
    ["购买", "purchase"],
    ["删除", "delete"],
    ["移除", "remove"],
    ["提交", "submit"],
    ["发送", "send"],
    ["发布", "publish"],
    ["登出", "logout"],
    ["退出登录", "logout"],
  ];
  const mediumPatterns: Array<[string, string]> = [
    ["save", "save"],
    ["connect", "connect"],
    ["login", "login"],
    ["sign in", "login"],
    ["authorize", "authorize"],
    ["grant", "permission"],
    ["保存", "save"],
    ["连接", "connect"],
    ["登录", "login"],
    ["授权", "authorize"],
  ];
  for (const [needle, reason] of highPatterns) {
    if (text.includes(needle)) reasons.push(reason);
  }
  if (reasons.length)
    return { risk: "high", riskReasons: Array.from(new Set(reasons)) };
  for (const [needle, reason] of mediumPatterns) {
    if (text.includes(needle)) reasons.push(reason);
  }
  if (kind === "link" && (el as HTMLAnchorElement).target === "_blank") {
    reasons.push("new-tab");
  }
  return {
    risk: reasons.length ? "medium" : "low",
    riskReasons: Array.from(new Set(reasons)),
  };
}

function stateHash(text: string): string {
  let hash = 5381;
  for (let i = 0; i < text.length; i += 1) {
    hash = (hash * 33) ^ text.charCodeAt(i);
  }
  return (hash >>> 0).toString(16);
}

function runState(): PageAgentRunState {
  const elements = collectElements();
  const active = document.activeElement;
  const text = (document.body?.innerText || "").slice(0, 20000);
  return {
    url: location.href,
    title: document.title,
    route: location.hash || location.pathname,
    textHash: stateHash(text),
    actionCount: elements.filter(
      (item) => !["input", "textarea", "select"].includes(item.kind),
    ).length,
    fieldCount: elements.filter((item) =>
      ["input", "textarea", "select"].includes(item.kind),
    ).length,
    focusedId: active?.getAttribute("data-echo-agent-id") || undefined,
  };
}

function elementKind(el: Element): PageAgentElementKind | null {
  const tag = el.tagName.toLowerCase();
  if (tag === "button") return "button";
  if (tag === "a") return "link";
  if (tag === "textarea") return "textarea";
  if (tag === "select") return "select";
  if (tag === "input") return "input";
  if (el.getAttribute("role") === "button") return "button";
  return null;
}

function cssPath(el: Element): string {
  const testId = el.getAttribute("data-testid");
  if (testId) return `[data-testid="${CSS.escape(testId)}"]`;
  const id = el.getAttribute("id");
  if (id) return `#${CSS.escape(id)}`;

  const parts: string[] = [];
  let node: Element | null = el;
  while (node && node !== document.body && parts.length < 6) {
    const tag = node.tagName.toLowerCase();
    const parent: Element | null = node.parentElement;
    if (!parent) break;
    const siblings = Array.from(parent.children).filter(
      (child): child is Element =>
        child instanceof Element && child.tagName === node!.tagName,
    );
    const nth =
      siblings.length > 1 ? `:nth-of-type(${siblings.indexOf(node) + 1})` : "";
    parts.unshift(`${tag}${nth}`);
    node = parent;
  }
  return parts.length ? parts.join(" > ") : el.tagName.toLowerCase();
}

function isVisible(el: Element): boolean {
  const html = el as HTMLElement;
  const rect = html.getBoundingClientRect();
  const style = window.getComputedStyle(html);
  return (
    rect.width > 0 &&
    rect.height > 0 &&
    style.visibility !== "hidden" &&
    style.display !== "none" &&
    !html.closest("[hidden],[aria-hidden='true']")
  );
}

function ensureAgentId(el: Element, index: number): string {
  const existing = el.getAttribute("data-echo-agent-id");
  if (existing) return existing;
  const id = `el-${index}-${Math.random().toString(36).slice(2, 8)}`;
  el.setAttribute("data-echo-agent-id", id);
  return id;
}

function collectElements(): PageAgentElement[] {
  const selector = [
    "button",
    "a[href]",
    "input",
    "textarea",
    "select",
    "[role='button']",
    "[data-echo-action]",
  ].join(",");
  return Array.from(document.querySelectorAll(selector))
    .filter(isVisible)
    .slice(0, 180)
    .map((el, index) => {
      const kind = elementKind(el) ?? "button";
      const html = el as
        | HTMLInputElement
        | HTMLButtonElement
        | HTMLSelectElement;
      const label = textOf(el) || el.getAttribute("name") || kind;
      const inputType = "type" in html ? html.type : undefined;
      const risk = classifyRisk(el, label, kind);
      const value =
        "value" in html && typeof html.value === "string" ? html.value : "";
      return {
        id: ensureAgentId(el, index),
        kind,
        label,
        selector: cssPath(el),
        disabled:
          Boolean((html as HTMLButtonElement).disabled) ||
          el.getAttribute("aria-disabled") === "true",
        risk: risk.risk,
        riskReasons: risk.riskReasons,
        requiresConfirmation: risk.risk === "high",
        required: Boolean((html as HTMLInputElement).required),
        placeholder: (html as HTMLInputElement).placeholder || undefined,
        inputType,
        valueSummary: value ? `<${value.length} chars>` : undefined,
      };
    });
}

function collectForms(elements: PageAgentElement[]) {
  return Array.from(document.querySelectorAll("form")).map((form, index) => {
    const id = ensureAgentId(form, index);
    const labels = Array.from(form.querySelectorAll("h1,h2,h3,legend"))
      .map((el) => el.textContent?.trim())
      .filter(Boolean);
    const fieldIds = elements
      .filter((item) => ["input", "textarea", "select"].includes(item.kind))
      .filter((item) =>
        form.querySelector(`[data-echo-agent-id="${item.id}"]`),
      )
      .map((item) => item.id);
    const submitIds = elements
      .filter((item) => item.kind === "button")
      .filter((item) => {
        const el = form.querySelector(`[data-echo-agent-id="${item.id}"]`);
        return el && ((el as HTMLButtonElement).type || "submit") === "submit";
      })
      .map((item) => item.id);
    return {
      id,
      label: labels[0] || `form ${index + 1}`,
      fields: fieldIds,
      submitActions: submitIds,
    };
  });
}

function snapshot(): PageAgentSnapshot {
  const elements = collectElements();
  const fields = elements.filter((item) =>
    ["input", "textarea", "select"].includes(item.kind),
  );
  const actions = elements.filter((item) => !fields.includes(item));
  const text = (document.body?.innerText || "").replace(/\s+\n/g, "\n").trim();
  return {
    ok: true,
    app: "echo",
    url: location.href,
    title: document.title,
    route: location.hash || location.pathname,
    text: text.slice(0, 12000),
    actions,
    fields,
    capabilities: Array.from(capabilityRegistry.values()).map(
      ({ run: _run, ...capability }) => capability,
    ),
    forms: collectForms(elements),
  };
}

function setNativeFormValue(
  input: HTMLInputElement | HTMLTextAreaElement,
  value: string,
) {
  let prototype = Object.getPrototypeOf(input) as object | null;
  let setter: ((this: typeof input, nextValue: string) => void) | undefined;

  while (prototype && !setter) {
    setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set as
      | ((this: typeof input, nextValue: string) => void)
      | undefined;
    prototype = Object.getPrototypeOf(prototype) as object | null;
  }

  if (setter) setter.call(input, value);
  else input.value = value;
}

async function run(action: Parameters<EchoPageAgentBridge["run"]>[0]) {
  if (action.type === "capability") {
    const capability = capabilityRegistry.get(action.id);
    const before = runState();
    if (!capability) {
      return {
        ok: false,
        error: `page agent capability not found: ${action.id}`,
        before,
        after: before,
        changed: false,
      };
    }
    if (capability.requiresConfirmation && !action.confirm) {
      return {
        ok: false,
        error: `confirmation required for capability: ${capability.label}`,
        before,
        after: before,
        changed: false,
        risk: capability.risk,
        riskReasons: capability.riskReasons,
        requiresConfirmation: true,
      };
    }
    const result = await capability.run(action.input ?? {});
    await new Promise((resolve) => window.setTimeout(resolve, 250));
    const after = runState();
    return {
      ok: true,
      result,
      url: location.href,
      title: document.title,
      changed:
        before.url !== after.url ||
        before.title !== after.title ||
        before.textHash !== after.textHash ||
        before.focusedId !== after.focusedId,
      before,
      after,
      risk: capability.risk,
      riskReasons: capability.riskReasons,
      requiresConfirmation: capability.requiresConfirmation,
    };
  }

  const el = document.querySelector<HTMLElement>(
    `[data-echo-agent-id="${CSS.escape(action.id)}"]`,
  );
  if (!el)
    return { ok: false, error: `page agent element not found: ${action.id}` };
  const item = collectElements().find(
    (candidate) => candidate.id === action.id,
  );
  const before = runState();
  if (
    item?.requiresConfirmation &&
    (action.type === "click" || action.type === "submit") &&
    !action.confirm
  ) {
    return {
      ok: false,
      error: `confirmation required for high-risk action: ${item.label}`,
      before,
      after: before,
      changed: false,
      risk: item.risk,
      riskReasons: item.riskReasons,
      requiresConfirmation: true,
    };
  }
  if (action.type === "click" || action.type === "submit") {
    el.click();
  } else {
    el.focus();
    const input = el as HTMLInputElement | HTMLTextAreaElement;
    const nextValue =
      action.clear === false ? `${input.value}${action.text}` : action.text;
    setNativeFormValue(input, nextValue);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }
  await new Promise((resolve) => window.setTimeout(resolve, 250));
  const after = runState();
  return {
    ok: true,
    url: location.href,
    title: document.title,
    changed:
      before.url !== after.url ||
      before.title !== after.title ||
      before.textHash !== after.textHash ||
      before.focusedId !== after.focusedId,
    before,
    after,
    risk: item?.risk ?? "low",
    riskReasons: item?.riskReasons ?? [],
    requiresConfirmation: item?.requiresConfirmation ?? false,
  };
}

export function installPageAgentBridge() {
  if (typeof window === "undefined") return;
  window.__echoPageAgent = {
    version: "0.1.0",
    snapshot,
    run,
  };
  document.documentElement.dataset.echoPageAgent = "ready";
}

export {};
