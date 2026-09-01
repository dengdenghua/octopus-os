(() => {
  const marker = "__ECHO_BOOKMARKLET_RELAY__";
  const current = document.currentScript?.src || "http://127.0.0.1:8000/api/browser/relay/bookmarklet.js";
  const apiBase = new URL(current).origin;
  const appOrigin = new URL(current).searchParams.get("app") || "http://localhost:3000";
  const roles = [
    ["general", "默认助手"],
    ["coder", "代码助手"],
    ["desktop_operator", "网页操作员"],
    ["vibe_selling", "销售顾问"],
    ["ecommerce_mind", "电商顾问"],
    ["shopify_operator", "Shopify 运营"],
  ];
  const memoryModes = [
    ["ephemeral", "临时记忆"],
    ["agent", "跟随角色"],
    ["write_allowed", "允许沉淀"],
  ];
  const roleKey = "echo.pageAgent.role";
  const memoryKey = "echo.pageAgent.memoryMode";

  if (window[marker]?.stop) {
    window[marker].stop();
  }

  const state = {
    running: true,
    timer: 0,
    version: "bookmarklet-0.1.0",
  };

  function notice(text) {
    let el = document.getElementById("echo-bookmarklet-relay-status");
    if (!el) {
      el = document.createElement("div");
      el.id = "echo-bookmarklet-relay-status";
      el.style.cssText = [
        "position:fixed",
        "right:16px",
        "bottom:16px",
        "z-index:2147483647",
        "padding:10px 12px",
        "border-radius:999px",
        "background:rgba(17,24,39,.92)",
        "color:white",
        "font:13px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif",
        "box-shadow:0 12px 30px rgba(0,0,0,.24)",
        "backdrop-filter:blur(14px)",
      ].join(";");
      document.documentElement.appendChild(el);
    }
    el.textContent = text;
    window.clearTimeout(el.__echoTimer);
    el.__echoTimer = window.setTimeout(() => el.remove(), 2400);
  }

  function openInEchoAI(task, options = {}) {
    const agent = options.agent || "general";
    const memory = options.memory || "ephemeral";
    const memoryLabel =
      memoryModes.find(([id]) => id === memory)?.[1] || "临时记忆";
    const prompt = [
      task.trim(),
      "",
      "当前外部网页:",
      `标题: ${document.title}`,
      `URL: ${location.href}`,
      `角色: ${roles.find(([id]) => id === agent)?.[1] || agent}`,
      `记忆模式: ${memoryLabel}`,
      "",
      memory === "ephemeral"
        ? "请只在本次会话使用网页上下文，不要写入长期记忆。"
        : memory === "agent"
          ? "可以读取所选角色的记忆，但不要自动写入长期记忆。"
          : "用户允许将有价值的总结沉淀到所选角色记忆中；写入前仍需明确说明。",
      "请结合这个网页内容和 EchoAI 网页助手连接状态继续协助我。",
    ]
      .filter(Boolean)
      .join("\n");
    const chatUrl =
      `${appOrigin}/#/workspace/chats/new` +
      `?prompt=${encodeURIComponent(prompt)}` +
      `&agent=${encodeURIComponent(agent)}` +
      `&memory=${encodeURIComponent(memory)}` +
      `&source=bookmarklet`;
    window.open(chatUrl, "_blank", "noopener,noreferrer");
  }

  function mountAssistant() {
    document.getElementById("echo-page-agent-panel")?.remove();
    const panel = document.createElement("div");
    panel.id = "echo-page-agent-panel";
    panel.style.cssText = [
      "position:fixed",
      "left:50%",
      "bottom:28px",
      "z-index:2147483647",
      "width:min(420px,calc(100vw - 32px))",
      "transform:translateX(-50%)",
      "border-radius:18px",
      "background:rgba(255,255,255,.94)",
      "color:#111827",
      "box-shadow:0 22px 60px rgba(30,20,80,.28)",
      "border:1px solid rgba(124,58,237,.24)",
      "font:14px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif",
      "overflow:hidden",
      "backdrop-filter:blur(18px)",
    ].join(";");

    const bar = document.createElement("div");
    bar.style.cssText = [
      "height:42px",
      "display:flex",
      "align-items:center",
      "gap:8px",
      "padding:0 12px",
      "background:linear-gradient(135deg,rgba(79,70,229,.92),rgba(88,28,135,.88))",
      "color:white",
      "font-weight:650",
    ].join(";");
    const dot = document.createElement("span");
    dot.style.cssText = "width:8px;height:8px;border-radius:999px;background:#86efac;box-shadow:0 0 0 4px rgba(134,239,172,.18);";
    const title = document.createElement("span");
    title.textContent = "EchoAI 网页助手";
    title.style.cssText = "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;";
    const mini = document.createElement("button");
    mini.type = "button";
    mini.textContent = "▼";
    mini.style.cssText = "border:0;border-radius:8px;background:rgba(255,255,255,.16);color:white;width:28px;height:26px;cursor:pointer;";
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "×";
    close.style.cssText = "border:0;border-radius:8px;background:rgba(239,68,68,.24);color:#fecaca;width:28px;height:26px;cursor:pointer;font-size:16px;";
    bar.append(dot, title, mini, close);

    const body = document.createElement("div");
    body.style.cssText = "padding:12px;background:rgba(255,255,255,.9);";
    const status = document.createElement("div");
    status.textContent = "准备就绪，当前网页已连接 EchoAI";
    status.style.cssText = "margin-bottom:8px;color:#6b7280;font-size:12px;";
    const controls = document.createElement("div");
    controls.style.cssText = "display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px;";
    const roleSelect = document.createElement("select");
    roleSelect.style.cssText = "height:32px;border:1px solid rgba(99,102,241,.24);border-radius:10px;background:white;padding:0 8px;color:#111827;outline:none;";
    for (const [id, label] of roles) {
      const option = document.createElement("option");
      option.value = id;
      option.textContent = label;
      roleSelect.appendChild(option);
    }
    try {
      const savedRole = localStorage.getItem(roleKey);
      if (savedRole && roles.some(([id]) => id === savedRole)) {
        roleSelect.value = savedRole;
      }
    } catch {
      // storage can be disabled on some pages.
    }
    const memorySelect = document.createElement("select");
    memorySelect.style.cssText = roleSelect.style.cssText;
    for (const [id, label] of memoryModes) {
      const option = document.createElement("option");
      option.value = id;
      option.textContent = label;
      memorySelect.appendChild(option);
    }
    try {
      const savedMemory = localStorage.getItem(memoryKey);
      if (savedMemory && memoryModes.some(([id]) => id === savedMemory)) {
        memorySelect.value = savedMemory;
      }
    } catch {
      // storage can be disabled on some pages.
    }
    controls.append(roleSelect, memorySelect);
    const form = document.createElement("form");
    form.style.cssText = "display:flex;gap:8px;align-items:center;";
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "输入新任务，回车提交";
    input.style.cssText = [
      "flex:1",
      "height:38px",
      "border:1px solid rgba(99,102,241,.32)",
      "border-radius:12px",
      "padding:0 12px",
      "font:14px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif",
      "outline:none",
      "box-shadow:0 0 0 4px rgba(99,102,241,.08)",
    ].join(";");
    const send = document.createElement("button");
    send.type = "submit";
    send.textContent = "发送";
    send.style.cssText = "height:38px;border:0;border-radius:12px;background:#4f46e5;color:white;padding:0 14px;font-weight:650;cursor:pointer;";
    form.append(input, send);
    body.append(status, controls, form);
    panel.append(bar, body);
    document.documentElement.appendChild(panel);

    mini.addEventListener("click", () => {
      const collapsed = body.style.display !== "none";
      body.style.display = collapsed ? "none" : "block";
      mini.textContent = collapsed ? "▲" : "▼";
    });
    close.addEventListener("click", () => {
      panel.remove();
      state.stop();
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const task = input.value.trim();
      if (!task) {
        input.focus();
        return;
      }
      try {
        localStorage.setItem(roleKey, roleSelect.value);
        localStorage.setItem(memoryKey, memorySelect.value);
      } catch {
        // best effort
      }
      status.textContent = "已发送到 EchoAI 对话";
      openInEchoAI(task, {
        agent: roleSelect.value,
        memory: memorySelect.value,
      });
      input.value = "";
    });
    window.setTimeout(() => input.focus(), 80);
  }

  function visibleText() {
    return (document.body?.innerText || document.documentElement?.innerText || "").trim();
  }

  function findElement(selector) {
    if (!selector) throw new Error("selector is required");
    const el = document.querySelector(selector);
    if (!el) throw new Error(`selector not found: ${selector}`);
    return el;
  }

  async function runPageAgent(payload) {
    if (!window.__echoPageAgent?.run) {
      throw new Error("page agent bridge is not available on this page");
    }
    return window.__echoPageAgent.run(payload);
  }

  async function runDomAction(action, params = {}) {
    const selector = String(params.selector || "");
    const text = String(params.text || "");
    const key = String(params.key || "Enter");
    const y = Number(params.y ?? params.deltaY ?? 700);

    if (action === "navigate") {
      const url = String(params.url || "");
      if (!url) throw new Error("url is required");
      location.href = url;
      return { ok: true, url };
    }
    if (action === "reload") {
      location.reload();
      return { ok: true };
    }
    if (action === "back") {
      history.back();
      return { ok: true };
    }
    if (action === "forward") {
      history.forward();
      return { ok: true };
    }
    if (action === "pageAction") {
      return runPageAgent({
        type: "click",
        id: String(params.id || ""),
        confirm: params.confirm === true,
      });
    }
    if (action === "pageInput") {
      return runPageAgent({
        type: "input",
        id: String(params.id || ""),
        text,
        clear: params.clear !== false,
      });
    }
    if (action === "pageCapability") {
      return runPageAgent({
        type: "capability",
        id: String(params.id || ""),
        input: params.input && typeof params.input === "object" ? params.input : {},
        confirm: params.confirm === true,
      });
    }
    if (action === "click") {
      findElement(selector).click();
      return { ok: true };
    }
    if (action === "type") {
      const el = findElement(selector);
      el.focus();
      if ("value" in el) {
        el.value = text;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      } else {
        el.textContent = text;
        el.dispatchEvent(new InputEvent("input", { bubbles: true, data: text }));
      }
      return { ok: true };
    }
    if (action === "hover") {
      const el = findElement(selector);
      el.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
      el.dispatchEvent(new MouseEvent("mouseenter", { bubbles: true }));
      return { ok: true };
    }
    if (action === "scroll") {
      if (selector) {
        findElement(selector).scrollIntoView({ block: "center", behavior: "instant" });
      } else {
        window.scrollBy({ top: y, left: 0, behavior: "instant" });
      }
      return { ok: true };
    }
    if (action === "press") {
      const target = document.activeElement || document.body;
      target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
      target.dispatchEvent(new KeyboardEvent("keyup", { key, bubbles: true }));
      return { ok: true };
    }
    if (action === "extract" || action === "aria") {
      const fullText = visibleText();
      const pageAgent = window.__echoPageAgent?.snapshot?.() || null;
      return {
        ok: true,
        url: location.href,
        title: document.title,
        text: fullText.slice(0, 20000),
        textLength: fullText.length,
        truncated: fullText.length > 20000,
        pageAgent,
        nodes: {
          role: "document",
          name: document.title,
          url: location.href,
          text: fullText.slice(0, 5000),
          pageAgent,
          truncated: fullText.length > 5000,
        },
      };
    }
    if (action === "screenshot") {
      throw new Error("bookmarklet mode does not support screenshots; install the extension for screenshots");
    }
    throw new Error(`unsupported DOM action: ${action}`);
  }

  function report(command, result) {
    const body = JSON.stringify({
      id: command.id,
      active_tab: { id: "bookmarklet", url: location.href, title: document.title },
      result,
    });
    const url = `${apiBase}/api/browser/relay/bookmarklet-result`;
    if (navigator.sendBeacon) {
      navigator.sendBeacon(url, new Blob([body], { type: "text/plain" }));
      return;
    }
    fetch(url, { method: "POST", mode: "no-cors", body });
  }

  async function handleCommands(commands) {
    for (const command of commands || []) {
      try {
        const result = await runDomAction(command.action, command.params || {});
        report(command, result || { ok: true, url: location.href, title: document.title });
      } catch (error) {
        report(command, {
          ok: false,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    }
  }

  function poll() {
    if (!state.running) return;
    const callback = `__echoBookmarkletPoll_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    window[callback] = (data) => {
      delete window[callback];
      void handleCommands(Array.isArray(data?.commands) ? data.commands : []);
    };
    const script = document.createElement("script");
    script.src =
      `${apiBase}/api/browser/relay/bookmarklet-poll` +
      `?callback=${encodeURIComponent(callback)}` +
      `&version=${encodeURIComponent(state.version)}` +
      `&url=${encodeURIComponent(location.href)}` +
      `&title=${encodeURIComponent(document.title)}` +
      `&t=${Date.now()}`;
    script.onload = script.onerror = () => script.remove();
    document.documentElement.appendChild(script);
  }

  state.stop = () => {
    state.running = false;
    window.clearInterval(state.timer);
    delete window[marker];
    document.getElementById("echo-page-agent-panel")?.remove();
    notice("EchoAI 网页助手已断开");
  };
  window[marker] = state;
  state.timer = window.setInterval(poll, 500);
  poll();
  mountAssistant();
  notice("EchoAI 网页助手已连接");
})();

