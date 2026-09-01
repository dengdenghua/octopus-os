import "./oauth-deep-link-core.js";

const API_BASES = ["http://127.0.0.1:8000", "http://localhost:8000"];
const EXTENSION_VERSION = chrome.runtime.getManifest().version;
const AUTH_TOKEN_KEY = "echo.gatewayToken";
const READ_ONLY_ACTIONS = new Set([
  "extract",
  "aria",
  "state",
  "screenshot",
  "wait",
]);
const runningCommands = new Set();
const recentHumanActivityByTab = new Map();
// Content scripts are recreated on every navigation. Keep the active cursor
// state in the extension service worker so the new document can recover the
// operator-visible action instead of briefly losing it mid-command.
const activeCursorOverlayByTab = new Map();
let lastWorkingBase = API_BASES[0];
let activeLease = null;
let relaySocket = null;
let relaySocketReconnectTimer = null;
let relaySocketConnecting = false;
let gatewayToken = "";
let gatewayTokenLoaded = false;
let gatewayTokenRevision = 0;

function websocketAuthProtocols(token) {
  const value = String(token || "");
  if (!value) return [];
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  const encoded = btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
  return ["bearer.b64", encoded];
}

async function readGatewayToken() {
  if (gatewayTokenLoaded) return gatewayToken;
  const revision = gatewayTokenRevision;
  const stored = await chrome.storage.local
    .get(AUTH_TOKEN_KEY)
    .catch(() => ({}));
  // A side-panel save may finish while the initial storage read is pending.
  // Never let that stale read overwrite the newer in-memory credential.
  if (!gatewayTokenLoaded && revision === gatewayTokenRevision) {
    gatewayToken = String(stored?.[AUTH_TOKEN_KEY] || "").trim();
    gatewayTokenLoaded = true;
  }
  return gatewayToken;
}

async function updateGatewayToken(nextToken) {
  gatewayTokenRevision += 1;
  gatewayToken = String(nextToken || "").trim();
  gatewayTokenLoaded = true;
  if (gatewayToken) {
    await chrome.storage.local.set({ [AUTH_TOKEN_KEY]: gatewayToken });
  } else {
    await chrome.storage.local.remove(AUTH_TOKEN_KEY);
  }
  if (relaySocket) {
    const socket = relaySocket;
    relaySocket = null;
    socket.close(1000, "gateway credentials changed");
  }
  connectRelaySocket();
  return { ok: true, configured: Boolean(gatewayToken) };
}

async function activeTabInfo() {
  const tabs = await chrome.tabs.query({
    active: true,
    lastFocusedWindow: true,
  });
  const tab = tabs[0];
  if (!tab) return null;
  return {
    id: tab.id,
    url: tab.url || "",
    title: tab.title || "",
  };
}

async function apiFetch(path, init) {
  const token = await readGatewayToken();
  const headers = new Headers(init?.headers || {});
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const bases = [
    lastWorkingBase,
    ...API_BASES.filter((base) => base !== lastWorkingBase),
  ];
  for (const base of bases) {
    try {
      const res = await fetch(`${base}${path}`, { ...init, headers });
      if (res.ok) {
        lastWorkingBase = base;
        return res;
      }
    } catch {
      // Try the next localhost spelling.
    }
  }
  return null;
}

async function apiJson(path, init) {
  const res = await apiFetch(path, init);
  if (!res) return null;
  return res.json().catch(() => null);
}

async function controlJson(path, body, method = "POST") {
  return apiJson(`/api/control-sessions${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

function nowSeconds() {
  return Date.now() / 1000;
}

function comparableUrl(url) {
  return String(url || "").trim();
}

function leaseTabId(lease) {
  const tab = lease?.tab && typeof lease.tab === "object" ? lease.tab : null;
  return tab?.id === undefined || tab?.id === null ? "" : String(tab.id);
}

function leaseTabUrl(lease) {
  const tab = lease?.tab && typeof lease.tab === "object" ? lease.tab : null;
  return comparableUrl(tab?.url);
}

function controlSessionIdFor(commandOrLease, tab) {
  const lease =
    commandOrLease?.lease && typeof commandOrLease.lease === "object"
      ? commandOrLease.lease
      : commandOrLease;
  return String(
    commandOrLease?.control_session_id ||
      commandOrLease?.controlSessionId ||
      lease?.control_session_id ||
      lease?.controlSessionId ||
      (tab?.id ? `chrome-tab-${tab.id}` : "chrome-active-tab"),
  );
}

function controlActionIdFor(command) {
  return String(
    command?.control_action_id || command?.id || `chrome-${Date.now()}`,
  );
}

async function ensureControlSessionForCommand(
  command,
  tab,
  status = "running",
) {
  const sessionId = controlSessionIdFor(command, tab);
  await controlJson("", {
    session_id: sessionId,
    owner_id: "chrome-extension",
    owner_label: "Chrome Extension",
    surface: "chrome",
    target_id: tab?.id ? String(tab.id) : "active-tab",
    status,
    metadata: {
      extension_version: EXTENSION_VERSION,
      url: tab?.url || "",
      title: tab?.title || "",
      command_id: command?.id || "",
    },
  });
  return sessionId;
}

async function appendControlAction(command, tab, status = "running") {
  const sessionId = await ensureControlSessionForCommand(command, tab, status);
  const actionId = controlActionIdFor(command);
  const action = String(command?.action || "action");
  await controlJson(`/${encodeURIComponent(sessionId)}/actions`, {
    action_id: actionId,
    action_type: action,
    status,
    surface: "chrome",
    target_id: tab?.id ? String(tab.id) : "active-tab",
    descriptor: {
      type: action,
      params: command?.params || {},
      command_id: command?.id || "",
      url: tab?.url || "",
      title: tab?.title || "",
    },
  });
  return { sessionId, actionId };
}

async function updateControlAction(
  command,
  tab,
  status,
  result = {},
  error = "",
) {
  const sessionId = controlSessionIdFor(command, tab);
  const actionId = controlActionIdFor(command);
  await controlJson(
    `/${encodeURIComponent(sessionId)}/actions/${encodeURIComponent(actionId)}`,
    { status, result, error },
    "PATCH",
  );
}

async function appendControlEvidence(sessionId, evidence) {
  await controlJson(`/${encodeURIComponent(sessionId)}/evidence`, evidence);
}

async function finishControlAction(command, tab, control, action, result) {
  await updateControlAction(command, tab, "done", result);
  await appendControlEvidence(control.sessionId, {
    action_id: control.actionId,
    kind: action === "screenshot" ? "screenshot" : "result",
    action,
    ok: true,
    summary: "completed",
    detail: result,
  });
  return result;
}

function commandInterruptedError(reason, detail = {}) {
  const error = new Error(`browser relay control interrupted: ${reason}`);
  error.code = "browser_relay_control_interrupted";
  error.reason = reason;
  error.detail = detail;
  return error;
}

function recordHumanActivity(tabId, activity = {}) {
  if (tabId === undefined || tabId === null) return;
  const event = {
    kind: String(activity.kind || "user_activity"),
    at: Number(activity.at || nowSeconds()),
    url: comparableUrl(activity.url),
    title: String(activity.title || ""),
    tabId,
    target:
      activity.target && typeof activity.target === "object"
        ? activity.target
        : undefined,
    data:
      activity.data && typeof activity.data === "object"
        ? activity.data
        : undefined,
  };
  recentHumanActivityByTab.set(String(tabId), event);
  if (activeLease && String(tabId) === leaseTabId(activeLease)) {
    void setPageControlIndicator(tabId, "paused", {
      reason: event.kind,
      lease: activeLease,
    });
    void reportControlEvent({
      type: "human_interrupt",
      reason: event.kind,
      source: "chrome_content_script",
      activity: event,
      lease: activeLease,
    });
  }
}

async function reportControlEvent(event) {
  const tab = await activeTabInfo();
  await apiFetch("/api/browser/relay/heartbeat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      extension_version: EXTENSION_VERSION,
      active_tab: tab,
      control_event: event,
    }),
  });
  if (event?.lease) {
    const sessionId = controlSessionIdFor(event.lease, tab);
    await controlJson(`/${encodeURIComponent(sessionId)}/takeover`, {
      reason: String(event.reason || event.type || "human_interrupt"),
      owner_id: "human",
      owner_label: "Human operator",
      metadata: {
        source: event.source || "chrome_extension",
        activity: event.activity || {},
      },
    });
    await appendControlEvidence(sessionId, {
      kind: "lease",
      action: "chrome_human_interrupt",
      ok: false,
      summary: String(event.reason || "human interrupt"),
      detail: event,
    });
  }
}

async function relayControl(action, reason = "") {
  return apiJson("/api/browser/relay/control", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      action,
      reason,
      source: "chrome_side_panel",
    }),
  });
}

async function setPageControlIndicator(tabId, mode, detail = {}) {
  if (!tabId) return;
  await chrome.tabs
    .sendMessage(tabId, {
      type: "echo.controlIndicator",
      mode,
      ...detail,
    })
    .catch(() => null);
}

async function setPageCursorOverlay(tabId, phase, action, params = {}) {
  if (!tabId) return;
  const selector = String(params.selector || "").trim();
  const x = Number(params.x ?? params.clientX);
  const y = Number(params.y ?? params.clientY);
  const message = {
    type: "echo.cursorOverlay",
    phase,
    action,
    ...(selector ? { selector } : {}),
    ...(Number.isFinite(x) && Number.isFinite(y) ? { x, y } : {}),
  };
  const key = String(tabId);
  if (phase === "end" || phase === "idle") {
    activeCursorOverlayByTab.delete(key);
  } else {
    activeCursorOverlayByTab.set(key, message);
  }
  await chrome.tabs.sendMessage(tabId, message).catch(() => null);
}

async function restorePageCursorOverlay(tabId) {
  const active = activeCursorOverlayByTab.get(String(tabId));
  if (!active) return;
  await chrome.tabs
    .sendMessage(tabId, { ...active, phase: "active" })
    .catch(() => null);
}

async function waitForTabComplete(tabId, timeoutMs = 10000) {
  const current = await chrome.tabs.get(tabId).catch(() => null);
  if (!current || current.status === "complete") return current;
  return new Promise((resolve) => {
    const timer = setTimeout(done, timeoutMs);
    function done() {
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      chrome.tabs
        .get(tabId)
        .then(resolve)
        .catch(() => resolve(null));
    }
    function listener(updatedTabId, changeInfo) {
      if (updatedTabId === tabId && changeInfo.status === "complete") {
        done();
      }
    }
    chrome.tabs.onUpdated.addListener(listener);
  });
}

function watchTabNavigation(tabId) {
  let observed = false;
  let resolveObserved = null;
  const observedPromise = new Promise((resolve) => {
    resolveObserved = resolve;
  });
  function listener(updatedTabId, changeInfo) {
    if (
      updatedTabId === tabId &&
      (Boolean(changeInfo.url) || changeInfo.status === "loading")
    ) {
      observed = true;
      resolveObserved(true);
    }
  }
  chrome.tabs.onUpdated.addListener(listener);
  return {
    async wait(timeoutMs) {
      if (observed) return true;
      return Promise.race([
        observedPromise,
        new Promise((resolve) => setTimeout(() => resolve(false), timeoutMs)),
      ]);
    },
    close() {
      chrome.tabs.onUpdated.removeListener(listener);
    },
  };
}

function isExecutionContextLoss(error) {
  return /(frame.*removed|context.*invalid|execution context.*destroyed|no frame with id)/i.test(
    error instanceof Error ? error.message : String(error),
  );
}

async function currentTab() {
  const tabs = await chrome.tabs.query({
    active: true,
    lastFocusedWindow: true,
  });
  const tab = tabs[0];
  if (!tab?.id) throw new Error("No active browser tab");
  return tab;
}

async function runInTab(tabId, fn, args = []) {
  const [result] = await chrome.scripting.executeScript({
    target: { tabId },
    func: fn,
    args,
  });
  return result?.result;
}

async function runDomActionInTab(tabId, action, params) {
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["dom-actions.js"],
  });
  return runInTab(
    tabId,
    (nextAction, nextParams) => {
      if (!globalThis.__ECHO_DOM_ACTIONS__?.run) {
        throw new Error("EchoAI DOM action runtime failed to load");
      }
      return globalThis.__ECHO_DOM_ACTIONS__.run(nextAction, nextParams);
    },
    [action, params],
  );
}

async function validateCommandLease(command) {
  const lease =
    command.lease && typeof command.lease === "object" ? command.lease : null;
  if (!lease) return null;
  const tab = await currentTab();
  const tabId = String(tab.id);
  const expectedTabId = leaseTabId(lease);
  if (
    lease.require_same_tab !== false &&
    expectedTabId &&
    tabId !== expectedTabId
  ) {
    await setPageControlIndicator(tab.id, "paused", {
      reason: "active_tab_changed",
      lease,
    });
    await reportControlEvent({
      type: "human_interrupt",
      reason: "active_tab_changed",
      source: "chrome_extension",
      lease,
      activity: {
        kind: "active_tab_changed",
        at: nowSeconds(),
        expectedTabId,
        actualTabId: tabId,
      },
    });
    throw commandInterruptedError("active_tab_changed", {
      expectedTabId,
      actualTabId: tabId,
    });
  }
  const expectedUrl = leaseTabUrl(lease);
  const actualUrl = comparableUrl(tab.url);
  if (
    lease.require_same_url !== false &&
    expectedUrl &&
    actualUrl !== expectedUrl
  ) {
    await setPageControlIndicator(tab.id, "paused", {
      reason: "tab_url_changed",
      lease,
    });
    await reportControlEvent({
      type: "human_interrupt",
      reason: "tab_url_changed",
      source: "chrome_extension",
      lease,
      activity: {
        kind: "tab_url_changed",
        at: nowSeconds(),
        expectedUrl,
        actualUrl,
      },
    });
    throw commandInterruptedError("tab_url_changed", {
      expectedUrl,
      actualUrl,
    });
  }
  const recentActivity = recentHumanActivityByTab.get(tabId);
  const issuedAt = Number(lease.issued_at || 0);
  if (
    recentActivity &&
    Number(recentActivity.at || 0) >= issuedAt &&
    !READ_ONLY_ACTIONS.has(String(command.action || ""))
  ) {
    await setPageControlIndicator(tab.id, "paused", {
      reason: recentActivity.kind,
      lease,
    });
    await reportControlEvent({
      type: "human_interrupt",
      reason: recentActivity.kind,
      source: "chrome_content_script",
      lease,
      activity: recentActivity,
    });
    throw commandInterruptedError(recentActivity.kind, {
      activity: recentActivity,
    });
  }
  return lease;
}

async function executeCommand(command) {
  const lease = await validateCommandLease(command);
  const tab = await currentTab();
  const tabId = tab.id;
  const action = command.action;
  const params = { ...(command.params || {}) };
  const deadlineAt = Number(command.deadline_at || 0);
  if (params.timeout == null && deadlineAt > 0) {
    // Leave a small margin for posting the result before the gateway's HTTP
    // waiter expires. This prevents a DOM auto-wait from outliving its command.
    params.timeout = Math.max(
      0,
      Math.floor(deadlineAt * 1000 - Date.now() - 250),
    );
  }
  const control = await appendControlAction(command, tab, "running");
  let actionResult = null;

  activeLease = lease;
  try {
    await setPageControlIndicator(tabId, "action", {
      action,
      lease,
    });
    await setPageCursorOverlay(tabId, "start", action, params);
    if (action === "navigate") {
      const url = String(params.url || "");
      if (!url) throw new Error("url is required");
      await chrome.tabs.update(tabId, { url });
      await waitForTabComplete(tabId);
    } else if (action === "reload") {
      await chrome.tabs.reload(tabId);
      await waitForTabComplete(tabId);
    } else if (action === "back") {
      await chrome.tabs.goBack(tabId);
      await waitForTabComplete(tabId);
    } else if (action === "forward") {
      await chrome.tabs.goForward(tabId);
      await waitForTabComplete(tabId);
    } else if (action === "screenshot") {
      const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {
        format: "png",
      });
      return finishControlAction(command, tab, control, action, {
        ok: true,
        dataUrl,
      });
    } else {
      const returnsDomPayload =
        action === "extract" || action === "aria" || action === "state";
      const navigationWatch = returnsDomPayload
        ? null
        : watchTabNavigation(tabId);
      let domResult = null;
      let navigationObserved = false;
      try {
        try {
          domResult = await runDomActionInTab(tabId, action, params);
        } catch (error) {
          navigationObserved = navigationWatch
            ? await navigationWatch.wait(750)
            : false;
          if (!navigationObserved || !isExecutionContextLoss(error))
            throw error;
          domResult = { ok: true, recoveredByNavigation: true };
        }
        if (domResult && returnsDomPayload) {
          return finishControlAction(command, tab, control, action, domResult);
        }
        if (navigationWatch && !navigationObserved) {
          navigationObserved = await navigationWatch.wait(150);
        }
        if (navigationObserved) {
          const remaining = Math.max(
            250,
            Math.min(5000, Number(params.timeout || 5000)),
          );
          await waitForTabComplete(tabId, remaining);
        }
      } finally {
        navigationWatch?.close();
      }
      actionResult = { ...domResult, navigationObserved };
    }

    const next = await chrome.tabs.get(tabId);
    const result = {
      ...(actionResult && typeof actionResult === "object" ? actionResult : {}),
      ok: true,
      url: next.url || "",
      title: next.title || "",
    };
    return finishControlAction(command, next, control, action, result);
  } catch (error) {
    const detail = {
      ok: false,
      error: error instanceof Error ? error.message : String(error),
      code: error?.code || "",
      reason: error?.reason || "",
      detail: error?.detail || {},
    };
    await updateControlAction(command, tab, "failed", detail, detail.error);
    await appendControlEvidence(control.sessionId, {
      action_id: control.actionId,
      kind: "result",
      action,
      ok: false,
      summary: detail.error,
      detail,
    });
    throw error;
  } finally {
    activeLease = null;
    await setPageCursorOverlay(tabId, "end", action);
    await setPageControlIndicator(tabId, "idle", {
      action,
    });
  }
}

async function reportResult(command, result) {
  const payload = {
    type: "result",
    id: command.id,
    active_tab: await activeTabInfo(),
    result,
  };
  if (relaySocket?.readyState === WebSocket.OPEN) {
    relaySocket.send(JSON.stringify(payload));
    return;
  }
  await apiFetch("/api/browser/relay/result", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

async function processCommands(commands = []) {
  for (const command of commands) {
    if (!command?.id || runningCommands.has(command.id)) continue;
    runningCommands.add(command.id);
    try {
      const result = await executeCommand(command);
      await reportResult(command, result);
    } catch (error) {
      await reportResult(command, {
        ok: false,
        error: error instanceof Error ? error.message : String(error),
        code: error?.code || "",
        reason: error?.reason || "",
        detail: error?.detail || {},
      });
    } finally {
      runningCommands.delete(command.id);
    }
  }
}

async function relaySocketHeartbeat() {
  if (relaySocket?.readyState !== WebSocket.OPEN) return false;
  relaySocket.send(
    JSON.stringify({
      type: "heartbeat",
      extension_version: EXTENSION_VERSION,
      active_tab: await activeTabInfo(),
      active_lease: activeLease,
      recent_human_activity: Array.from(
        recentHumanActivityByTab.values(),
      ).slice(-5),
    }),
  );
  return true;
}

function scheduleRelaySocketReconnect() {
  if (relaySocketReconnectTimer) return;
  relaySocketReconnectTimer = setTimeout(() => {
    relaySocketReconnectTimer = null;
    connectRelaySocket();
  }, 1000);
}

async function connectRelaySocket() {
  if (
    relaySocketConnecting ||
    (relaySocket &&
      (relaySocket.readyState === WebSocket.OPEN ||
        relaySocket.readyState === WebSocket.CONNECTING))
  ) {
    return;
  }
  relaySocketConnecting = true;
  let socket;
  try {
    const token = await readGatewayToken();
    const wsUrl = `${lastWorkingBase.replace(/^http/, "ws")}/api/browser/relay/ws`;
    const protocols = websocketAuthProtocols(token);
    socket = protocols.length
      ? new WebSocket(wsUrl, protocols)
      : new WebSocket(wsUrl);
    relaySocket = socket;
  } catch (error) {
    console.warn(
      "EchoAI Browser Relay: failed to create push connection",
      error instanceof Error ? error.message : String(error),
    );
    scheduleRelaySocketReconnect();
    return;
  } finally {
    relaySocketConnecting = false;
  }
  socket.onopen = () => {
    if (relaySocket !== socket) return;
    void relaySocketHeartbeat();
  };
  socket.onmessage = (event) => {
    if (relaySocket !== socket) return;
    let message = null;
    try {
      message = JSON.parse(String(event.data || "{}"));
    } catch {
      return;
    }
    if (message?.type === "commands") {
      void processCommands(
        Array.isArray(message.commands) ? message.commands : [],
      );
    } else if (message?.type === "ping") {
      void relaySocketHeartbeat();
    }
  };
  socket.onclose = () => {
    if (relaySocket === socket) relaySocket = null;
    scheduleRelaySocketReconnect();
  };
  socket.onerror = () => {
    // onclose owns reconnect scheduling; HTTP polling stays active meanwhile.
  };
}

async function postHeartbeat(forceSocket = false) {
  // The push channel delivers commands without relying on MV3 timers. Keep
  // this HTTP path as a compatibility fallback for older local runtimes.
  if (relaySocket?.readyState === WebSocket.OPEN) {
    return forceSocket ? relaySocketHeartbeat() : true;
  }
  const data = await apiJson("/api/browser/relay/heartbeat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      extension_version: EXTENSION_VERSION,
      active_tab: await activeTabInfo(),
      active_lease: activeLease,
      recent_human_activity: Array.from(
        recentHumanActivityByTab.values(),
      ).slice(-5),
    }),
  });
  if (!data) return false;
  void processCommands(Array.isArray(data.commands) ? data.commands : []);
  return true;
}

async function relayStatus() {
  await postHeartbeat(true);
  const data = await apiJson("/api/browser/relay/status", { method: "GET" });
  return {
    ok: Boolean(data),
    base_url: lastWorkingBase,
    active_tab: await activeTabInfo(),
    relay: data || null,
  };
}

async function openSidePanel(windowId) {
  if (!chrome.sidePanel?.open || !windowId) return false;
  await chrome.sidePanel.open({ windowId });
  return true;
}

async function openPageAgent(tabId) {
  if (!tabId) return false;
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["bookmarklet.js"],
  });
  await postHeartbeat(true);
  return true;
}

async function configureSidePanelBehavior() {
  if (!chrome.sidePanel?.setPanelBehavior) return;
  try {
    await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  } catch (error) {
    console.warn(
      "EchoAI Browser Relay: failed to enable side panel behavior",
      error instanceof Error ? error.message : error,
    );
  }
}

chrome.runtime.onInstalled.addListener(() => {
  void configureSidePanelBehavior();
  connectRelaySocket();
  void postHeartbeat();
});

chrome.runtime.onStartup.addListener(() => {
  void configureSidePanelBehavior();
  connectRelaySocket();
  void postHeartbeat();
});

chrome.action.onClicked.addListener(async (tab) => {
  const tabId = tab.id;
  const windowId = tab.windowId;
  try {
    const opened = await openSidePanel(windowId);
    if (!opened && tabId) {
      await openPageAgent(tabId);
    }
    await postHeartbeat(true);
  } catch (error) {
    console.warn(
      "EchoAI Browser Relay: failed to open side panel",
      error instanceof Error ? error.message : error,
    );
  }
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const type = String(message?.type || "");
  (async () => {
    if (type === "echo.userActivity") {
      recordHumanActivity(_sender.tab?.id, message.activity || {});
      return { ok: true };
    }
    if (type === "echo.cursorOverlayReady") {
      const tabId = _sender.tab?.id;
      await restorePageCursorOverlay(tabId);
      return { ok: true, restored: Boolean(tabId) };
    }
    if (type === "echo.mcpOAuthDeepLink") {
      const callbackUrl = globalThis.EchoMcpOAuthDeepLink.buildCallbackURL({
        sourceURL: String(_sender.tab?.url || ""),
        deepLinkURL: String(message.deep_link_url || ""),
        backendBaseURL: lastWorkingBase,
      });
      return {
        ok: Boolean(callbackUrl),
        callback_url: callbackUrl || "",
      };
    }
    if (type === "echo.status") {
      return relayStatus();
    }
    if (type === "echo.heartbeat") {
      return { ok: await postHeartbeat(true), base_url: lastWorkingBase };
    }
    if (type === "echo.control") {
      return relayControl(
        String(message.action || ""),
        String(message.reason || ""),
      );
    }
    if (type === "echo.authChanged") {
      return updateGatewayToken(message.token);
    }
    if (type === "echo.openPageAgent") {
      const tab = await currentTab();
      return { ok: await openPageAgent(tab.id) };
    }
    if (type === "echo.activeTab") {
      return { ok: true, active_tab: await activeTabInfo() };
    }
    return { ok: false, error: `unknown message: ${type}` };
  })()
    .then((result) => sendResponse(result))
    .catch((error) => {
      sendResponse({
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      });
    });
  return true;
});

chrome.tabs.onActivated.addListener(() => {
  if (activeLease) {
    void currentTab()
      .then((tab) => {
        const expectedTabId = leaseTabId(activeLease);
        if (expectedTabId && String(tab.id) !== expectedTabId) {
          void setPageControlIndicator(tab.id, "paused", {
            reason: "active_tab_changed",
            lease: activeLease,
          });
          return reportControlEvent({
            type: "human_interrupt",
            reason: "active_tab_changed",
            source: "chrome_tabs_api",
            lease: activeLease,
            activity: {
              kind: "active_tab_changed",
              at: nowSeconds(),
              expectedTabId,
              actualTabId: tab.id,
            },
          });
        }
        return null;
      })
      .catch(() => null);
  }
  void postHeartbeat(true);
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === "complete" || changeInfo.url) {
    void postHeartbeat(true);
  }
  if (changeInfo.status === "complete") {
    void restorePageCursorOverlay(tabId);
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  activeCursorOverlayByTab.delete(String(tabId));
  recentHumanActivityByTab.delete(String(tabId));
});

setInterval(() => {
  void postHeartbeat();
}, 500);

connectRelaySocket();
void postHeartbeat();

