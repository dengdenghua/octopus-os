const API_BASES = ["http://127.0.0.1:8000", "http://localhost:8000"];
const FRONTEND_BASES = ["http://localhost:3000", "http://127.0.0.1:3000"];
const THREAD_KEY = "echo.chrome.sidecar.threadId";
const AUTH_TOKEN_KEY = "echo.gatewayToken";

const state = {
  apiBase: API_BASES[0],
  ws: null,
  nextId: 1,
  pending: new Map(),
  connected: false,
  streaming: false,
  threadId: localStorage.getItem(THREAD_KEY) || makeThreadId(),
  activeTab: null,
  control: null,
  assistantItems: new Map(),
  authToken: "",
  authSaving: false,
};

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

const el = {
  connectionText: document.getElementById("connectionText"),
  relayDot: document.getElementById("relayDot"),
  tabTitle: document.getElementById("tabTitle"),
  tabUrl: document.getElementById("tabUrl"),
  controlTitle: document.getElementById("controlTitle"),
  controlDetail: document.getElementById("controlDetail"),
  stopButton: document.getElementById("stopButton"),
  approvalDock: document.getElementById("approvalDock"),
  messages: document.getElementById("messages"),
  composer: document.getElementById("composer"),
  promptInput: document.getElementById("promptInput"),
  sendButton: document.getElementById("sendButton"),
  newThreadButton: document.getElementById("newThreadButton"),
  authToggleButton: document.getElementById("authToggleButton"),
  authPanel: document.getElementById("authPanel"),
  authForm: document.getElementById("authForm"),
  authTokenInput: document.getElementById("authTokenInput"),
  authSaveButton: document.getElementById("authSaveButton"),
  authClearButton: document.getElementById("authClearButton"),
  authStatus: document.getElementById("authStatus"),
  pageAgentButton: document.getElementById("pageAgentButton"),
  openAppButton: document.getElementById("openAppButton"),
};

localStorage.setItem(THREAD_KEY, state.threadId);
wireUi();
void initialize();

async function initialize() {
  const stored = await chrome.storage.local.get(AUTH_TOKEN_KEY).catch(() => ({}));
  state.authToken = String(stored?.[AUTH_TOKEN_KEY] || "").trim();
  el.authTokenInput.value = state.authToken;
  setAuthStatus(
    state.authToken ? "已配置连接密钥。" : "密钥仅保存在此 Chrome 配置中。",
  );
  await refreshRelayStatus();
  connectRealtime();
  window.setInterval(() => void refreshRelayStatus(), 1500);
}

function wireUi() {
  el.authToggleButton.addEventListener("click", () => {
    el.authPanel.hidden = !el.authPanel.hidden;
    el.authToggleButton.setAttribute("aria-expanded", String(!el.authPanel.hidden));
    if (!el.authPanel.hidden) el.authTokenInput.focus();
  });
  el.authForm.addEventListener("submit", (event) => {
    event.preventDefault();
    void saveGatewayToken(el.authTokenInput.value);
  });
  el.authClearButton.addEventListener("click", () => {
    el.authTokenInput.value = "";
    void saveGatewayToken("");
  });
  el.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    void sendPrompt();
  });
  el.promptInput.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      void sendPrompt();
    }
  });
  el.newThreadButton.addEventListener("click", () => {
    state.threadId = makeThreadId();
    state.assistantItems.clear();
    localStorage.setItem(THREAD_KEY, state.threadId);
    el.messages.replaceChildren();
    appendSystem("已开启新的 Chrome 助手对话。");
    el.promptInput.focus();
  });
  el.pageAgentButton.addEventListener("click", async () => {
    const result = await runtimeMessage({ type: "echo.openPageAgent" });
    if (!result?.ok) {
      appendSystem(`页面轻面板打开失败：${result?.error || "未知错误"}`, true);
    }
  });
  el.openAppButton.addEventListener("click", () => {
    const url = `${FRONTEND_BASES[0]}/#/workspace/realtime/${encodeURIComponent(
      state.threadId,
    )}`;
    chrome.tabs.create({ url });
  });
  el.stopButton.addEventListener("click", () => {
    void toggleControlStop();
  });
}

async function saveGatewayToken(value) {
  if (state.authSaving) return;
  const token = String(value || "").trim();
  setAuthBusy(true);
  setAuthStatus(token ? "连接密钥已保存，正在验证连接…" : "正在清除连接密钥…");
  try {
    const result = await runtimeMessage({ type: "echo.authChanged", token });
    if (!result?.ok) {
      setAuthBusy(false);
      setAuthStatus(
        `连接密钥保存失败：${result?.error || "未知错误"}`,
        "error",
      );
      return;
    }
    state.authToken = token;
    el.authTokenInput.value = token;
    reconnectRealtime();
    if (!token) {
      await refreshRelayStatus();
      setAuthBusy(false);
      setAuthStatus("连接密钥已清除。");
      return;
    }
    const connected = await waitForRelayConnection();
    await refreshRelayStatus();
    setAuthBusy(false);
    setAuthStatus(
      connected
        ? "连接密钥已验证，Chrome Relay 已连接。"
        : "密钥已保存，但未能连接。请检查密钥或确认 EchoOS 主控已启动。",
      connected ? "success" : "error",
    );
  } finally {
    setAuthBusy(false);
  }
}

function setAuthBusy(busy) {
  state.authSaving = busy;
  el.authSaveButton.disabled = busy;
  el.authClearButton.disabled = busy;
  el.authTokenInput.disabled = busy;
}

function setAuthStatus(message, tone = "muted") {
  el.authStatus.textContent = message;
  el.authStatus.dataset.tone = tone;
}

async function waitForRelayConnection(timeoutMs = 6_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const status = await runtimeMessage({ type: "echo.status" });
    if (status?.ok && status.relay?.push_connected === true) return true;
    await new Promise((resolve) => window.setTimeout(resolve, 250));
  }
  return false;
}

function makeThreadId() {
  return `chrome-${Date.now().toString(36)}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

async function runtimeMessage(message) {
  return chrome.runtime.sendMessage(message).catch((error) => ({
    ok: false,
    error: error instanceof Error ? error.message : String(error),
  }));
}

async function refreshRelayStatus() {
  const status = await runtimeMessage({ type: "echo.status" });
  if (!status?.ok) {
    setConnectionText("主控离线");
    el.relayDot.className = "status-dot error";
    return;
  }
  state.apiBase = status.base_url || state.apiBase;
  state.activeTab = status.active_tab || status.relay?.active_tab || null;
  state.control = status.relay?.control || null;
  const relayConnected = status.relay?.connected === true;
  el.relayDot.className = `status-dot ${relayConnected ? "connected" : ""}`;
  el.tabTitle.textContent = state.activeTab?.title || "尚未连接标签页";
  el.tabUrl.textContent = state.activeTab?.url || "等待 Chrome Relay";
  renderControl();
  setConnectionText(
    state.connected
      ? relayConnected
        ? "实时通道与 Chrome 已连接"
        : "实时通道已连接 · 等待 Chrome"
      : relayConnected
        ? "Chrome 已连接 · 等待实时通道"
        : "正在连接",
  );
}

function setConnectionText(text) {
  el.connectionText.textContent = text;
  el.sendButton.disabled = !state.connected || state.streaming;
}

function renderControl() {
  const control = state.control || {};
  const mode = String(control.mode || "idle");
  if (mode === "agent_active") {
    const lease = control.lease || {};
    el.controlTitle.textContent = "Agent 正在接管当前标签页";
    el.controlDetail.textContent = `${lease.action || "browser"} · 切换标签或手动输入会自动暂停`;
    el.stopButton.textContent = "停止";
    el.stopButton.disabled = false;
    return;
  }
  if (mode === "interrupted") {
    const interrupt = control.human_interrupt || {};
    el.controlTitle.textContent = "已暂停 Agent 页面操作";
    el.controlDetail.textContent = interrupt.reason || "检测到人工介入";
    el.stopButton.textContent = "恢复";
    el.stopButton.disabled = false;
    return;
  }
  el.controlTitle.textContent = state.streaming ? "Agent 正在思考" : "控制权空闲";
  el.controlDetail.textContent = state.streaming
    ? "如需打断后续页面操作，可点停止。"
    : "你可以随时操作页面。";
  el.stopButton.textContent = "停止";
  el.stopButton.disabled = !state.streaming;
}

async function toggleControlStop() {
  const mode = String(state.control?.mode || "idle");
  const action = mode === "interrupted" ? "resume" : "stop";
  const result = await runtimeMessage({
    type: "echo.control",
    action,
    reason: action === "stop" ? "operator_stop" : "operator_resume",
  });
  if (!result?.ok) {
    appendSystem(`控制权切换失败：${result?.error || "未知错误"}`, true);
    return;
  }
  state.control = result.control || null;
  if (action === "stop") {
    state.streaming = false;
    appendSystem("已停止后续 Chrome 页面操作。");
  } else {
    appendSystem("已恢复 Chrome 页面操作。");
  }
  renderControl();
  setConnectionText(state.connected ? "实时通道已连接" : "正在连接");
}

function connectRealtime() {
  if (
    state.ws &&
    (state.ws.readyState === WebSocket.OPEN ||
      state.ws.readyState === WebSocket.CONNECTING)
  ) {
    return;
  }
  const wsUrl = `${state.apiBase.replace(/^http/, "ws")}/api/realtime`;
  const protocols = websocketAuthProtocols(state.authToken);
  const ws = protocols.length
    ? new WebSocket(wsUrl, protocols)
    : new WebSocket(wsUrl);
  state.ws = ws;
  ws.onopen = () => {
    if (state.ws !== ws) return;
    state.connected = true;
    setConnectionText("实时通道已连接");
    appendSystem("实时通道已连接。");
  };
  ws.onmessage = (event) => {
    if (state.ws !== ws) return;
    handleRealtimeMessage(String(event.data || ""));
  };
  ws.onerror = () => {
    if (state.ws !== ws) return;
    state.connected = false;
    setConnectionText("实时通道异常");
  };
  ws.onclose = () => {
    if (state.ws !== ws) return;
    state.connected = false;
    failPending("实时通道已断开");
    state.streaming = false;
    setConnectionText("实时通道重连中");
    window.setTimeout(() => {
      state.apiBase =
        state.apiBase === API_BASES[0] ? API_BASES[1] : API_BASES[0];
      connectRealtime();
    }, 900);
  };
}

function reconnectRealtime() {
  const previous = state.ws;
  state.ws = null;
  previous?.close(1000, "gateway credentials changed");
  state.connected = false;
  connectRealtime();
}

function failPending(message) {
  for (const pending of state.pending.values()) {
    pending.reject(new Error(message));
  }
  state.pending.clear();
}

function rpc(method, params = {}) {
  connectRealtime();
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    return Promise.reject(new Error("实时通道尚未连接"));
  }
  const id = state.nextId++;
  const payload = { jsonrpc: "2.0", id, method, params };
  state.ws.send(JSON.stringify(payload));
  return new Promise((resolve, reject) => {
    state.pending.set(id, { resolve, reject });
  });
}

function handleRealtimeMessage(raw) {
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    return;
  }
  if (payload.id !== undefined && (payload.result || payload.error)) {
    const pending = state.pending.get(payload.id);
    if (!pending) return;
    state.pending.delete(payload.id);
    if (payload.error) pending.reject(new Error(payload.error.message));
    else pending.resolve(payload.result);
    return;
  }
  if (payload.id !== undefined && payload.method) {
    showApprovalRequest(payload);
    return;
  }
  if (!payload.method) return;
  handleNotification(payload.method, payload.params || {});
}

function handleNotification(method, params) {
  if (method === "item/agentMessage/delta") {
    appendAssistantDelta(params.itemId, String(params.delta || ""));
    return;
  }
  if (method === "item/started") {
    const item = params.item || {};
    if (item.type === "commandExecution") {
      appendEvent(`工具开始：${item.command || item.id || "未知命令"}`);
    }
    return;
  }
  if (method === "item/completed") {
    const item = params.item || {};
    if (item.type === "agentMessage" && item.text) {
      replaceAssistantText(item.id, String(item.text));
    } else if (item.type === "commandExecution") {
      appendEvent(`工具完成：${item.command || item.id || "未知命令"}`);
    }
    return;
  }
  if (method === "turn/completed" || method === "turn/interrupted") {
    state.streaming = false;
    setConnectionText("实时通道已连接");
    return;
  }
  if (method === "error") {
    state.streaming = false;
    setConnectionText("实时通道已连接");
    appendSystem(params.error?.message || "Agent 任务失败", true);
  }
}

async function sendPrompt() {
  const text = el.promptInput.value.trim();
  if (!text || state.streaming) return;
  appendUser(text);
  el.promptInput.value = "";
  state.streaming = true;
  setConnectionText("Agent 正在工作");
  await runtimeMessage({
    type: "echo.control",
    action: "resume",
    reason: "new_chrome_turn",
  });
  const prompt = text.toLowerCase().startsWith("@chrome")
    ? text
    : `@Chrome\n${text}`;
  try {
    await rpc("turn/start", {
      threadId: state.threadId,
      input: [
        {
          type: "text",
          text: prompt,
          metadata: {
            context: {
              mode: "chrome",
              capability_mode: "browser",
              runtime_surfaces: ["chrome"],
              tool_surface: "chrome",
              browser_operation_mode: true,
              chrome_operation_mode: true,
              browser_surface: "chrome",
              browser_session_policy: "thread_native_external_chrome",
              browser_track_preference: "extension",
              browser_permission_policy: "site_policy_required",
              browser_active_tab: state.activeTab,
            },
          },
        },
      ],
      approvalPolicy: "on-request",
    });
  } catch (error) {
    state.streaming = false;
    setConnectionText("实时通道已连接");
    appendSystem(error instanceof Error ? error.message : String(error), true);
  }
}

function showApprovalRequest(request) {
  el.approvalDock.hidden = false;
  const card = document.createElement("article");
  card.className = "approval-card";
  const title = document.createElement("h2");
  title.textContent = "需要确认";
  const body = document.createElement("pre");
  body.textContent = JSON.stringify(
    {
      method: request.method,
      params: request.params,
    },
    null,
    2,
  );
  const actions = document.createElement("div");
  actions.className = "approval-actions";
  const accept = document.createElement("button");
  accept.className = "primary";
  accept.type = "button";
  accept.textContent = "允许";
  const decline = document.createElement("button");
  decline.className = "secondary";
  decline.type = "button";
  decline.textContent = "拒绝";
  actions.append(accept, decline);
  card.append(title, body, actions);
  el.approvalDock.replaceChildren(card);
  accept.addEventListener("click", () => {
    reply(request.id, { action: "accept" });
    clearApproval();
  });
  decline.addEventListener("click", () => {
    reply(request.id, { action: "decline" });
    clearApproval();
  });
}

function reply(id, result) {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
  state.ws.send(JSON.stringify({ jsonrpc: "2.0", id, result }));
}

function clearApproval() {
  el.approvalDock.hidden = true;
  el.approvalDock.replaceChildren();
}

function appendSystem(text, danger = false) {
  const node = document.createElement("article");
  node.className = `message system${danger ? " danger" : ""}`;
  node.textContent = text;
  el.messages.append(node);
  scrollMessages();
}

function appendUser(text) {
  appendMessage("user", "你", text);
}

function appendAssistantDelta(itemId, delta) {
  let node = state.assistantItems.get(itemId);
  if (!node) {
    node = appendMessage("assistant", "Agent", "");
    state.assistantItems.set(itemId, node);
  }
  const textNode = node.querySelector(".text");
  textNode.textContent += delta;
  scrollMessages();
}

function replaceAssistantText(itemId, text) {
  let node = state.assistantItems.get(itemId);
  if (!node) {
    node = appendMessage("assistant", "Agent", "");
    state.assistantItems.set(itemId, node);
  }
  node.querySelector(".text").textContent = text;
  scrollMessages();
}

function appendMessage(role, label, text) {
  const node = document.createElement("article");
  node.className = `message ${role}`;
  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = label;
  const body = document.createElement("div");
  body.className = "text";
  body.textContent = text;
  node.append(meta, body);
  el.messages.append(node);
  scrollMessages();
  return node;
}

function appendEvent(text) {
  const node = document.createElement("div");
  node.className = "event-row";
  node.textContent = text;
  el.messages.append(node);
  scrollMessages();
}

function scrollMessages() {
  el.messages.scrollTop = el.messages.scrollHeight;
}

