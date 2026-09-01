(() => {
  "use strict";

  globalThis.__ECHO_CURSOR_OVERLAY_STORE__?.destroy?.();

  const HOST_ID = "echo-agent-cursor-overlay-host";
  const store = new Map([
    [
      "cursor",
      {
        visible: false,
        phase: "idle",
        action: "",
        x: Math.round(window.innerWidth / 2),
        y: Math.round(window.innerHeight / 2),
      },
    ],
  ]);
  const subscribers = new Set();
  let renderFrame = 0;
  let hideTimer = 0;
  let phaseTimer = 0;
  let transitionRevision = 0;
  let overlayHost = null;
  let destroyed = false;

  function snapshot() {
    return store.get("cursor");
  }

  function subscribe(listener) {
    subscribers.add(listener);
    return () => subscribers.delete(listener);
  }

  function publish(patch) {
    if (destroyed) return;
    store.set("cursor", { ...snapshot(), ...patch });
    for (const listener of subscribers) listener();
  }

  function resolvePoint(message) {
    const rawX = Number(message.x);
    const rawY = Number(message.y);
    if (Number.isFinite(rawX) && Number.isFinite(rawY)) {
      return { x: rawX, y: rawY };
    }
    const selector = String(message.selector || "").trim();
    if (selector) {
      try {
        const target = document.querySelector(selector);
        if (target instanceof Element) {
          const rect = target.getBoundingClientRect();
          if (rect.width > 0 && rect.height > 0) {
            return {
              x: rect.left + rect.width / 2,
              y: rect.top + rect.height / 2,
            };
          }
        }
      } catch {
        // A stale selector must never interfere with the browser action.
      }
    }
    return { x: window.innerWidth / 2, y: window.innerHeight / 2 };
  }

  function clampPoint(point) {
    return {
      x: Math.max(8, Math.min(Math.max(8, window.innerWidth - 8), point.x)),
      y: Math.max(8, Math.min(Math.max(8, window.innerHeight - 8), point.y)),
    };
  }

  function actionPhase(action) {
    if (action === "click" || action === "press") return "click";
    if (action === "type") return "type";
    return "active";
  }

  function ensureHost() {
    if (overlayHost?.isConnected && overlayHost.shadowRoot) return overlayHost;
    const existing = document.getElementById(HOST_ID);
    if (existing?.shadowRoot) {
      overlayHost = existing;
      return existing;
    }
    const parent = document.documentElement || document.body;
    if (!parent) return null;
    const host = document.createElement("div");
    host.id = HOST_ID;
    host.setAttribute("aria-hidden", "true");
    host.style.cssText =
      "position:fixed;inset:0;z-index:2147483647;pointer-events:none;contain:strict";
    const shadow = host.attachShadow({ mode: "open" });
    shadow.innerHTML = `
      <style>
        :host { all: initial; }
        #cursor {
          position: fixed;
          left: 0;
          top: 0;
          width: 22px;
          height: 28px;
          opacity: 0;
          transform: translate3d(-50px,-50px,0);
          transition: transform 150ms cubic-bezier(.2,.8,.2,1), opacity 140ms ease;
          filter: drop-shadow(0 3px 5px rgba(0,0,0,.28));
          will-change: transform, opacity;
          pointer-events: none;
        }
        #cursor[data-visible="true"] { opacity: 1; }
        #cursor::before {
          content: "";
          display: block;
          width: 16px;
          height: 21px;
          background: #fff;
          border: 1.5px solid rgba(15,23,42,.86);
          clip-path: polygon(0 0, 0 88%, 29% 66%, 46% 100%, 62% 92%, 45% 60%, 78% 58%);
        }
        #pulse {
          position: absolute;
          left: 2px;
          top: 2px;
          width: 22px;
          height: 22px;
          border: 2px solid rgba(20,184,166,.72);
          border-radius: 999px;
          opacity: 0;
          transform: translate(-50%,-50%) scale(.35);
        }
        #cursor[data-phase="click"] #pulse {
          animation: echo-cursor-pulse 520ms ease-out;
        }
        #cursor[data-phase="type"] #label {
          background: rgba(14,116,144,.86);
        }
        #cursor[data-phase="type"] #label::after {
          content: " ⌨";
        }
        #label {
          position: absolute;
          left: 17px;
          top: 18px;
          max-width: 150px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          border: .5px solid rgba(255,255,255,.44);
          border-radius: 8px;
          background: rgba(15,23,42,.78);
          color: white;
          padding: 3px 7px;
          font: 500 10px/14px ui-sans-serif, system-ui, -apple-system, sans-serif;
          backdrop-filter: blur(6px);
        }
        @keyframes echo-cursor-pulse {
          0% { opacity: .9; transform: translate(-50%,-50%) scale(.35); }
          100% { opacity: 0; transform: translate(-50%,-50%) scale(1.55); }
        }
        @media (prefers-reduced-motion: reduce) {
          #cursor { transition: none; }
          #cursor[data-phase="click"] #pulse { animation: none; }
        }
      </style>
      <div id="cursor" data-visible="false" data-phase="idle">
        <span id="pulse"></span><span id="label"></span>
      </div>`;
    parent.appendChild(host);
    overlayHost = host;
    return host;
  }

  function render() {
    renderFrame = 0;
    if (destroyed) return;
    const state = snapshot();
    const host = ensureHost();
    if (!host) {
      document.addEventListener("readystatechange", scheduleRender, {
        once: true,
      });
      return;
    }
    const root = host.shadowRoot;
    const cursor = root?.getElementById("cursor");
    const label = root?.getElementById("label");
    if (!cursor || !label) return;
    cursor.dataset.visible = String(state.visible);
    cursor.dataset.phase = state.phase;
    cursor.style.transform = `translate3d(${Math.round(state.x)}px,${Math.round(state.y)}px,0)`;
    label.textContent = state.action ? `EchoAI · ${state.action}` : "EchoAI";
  }

  function scheduleRender() {
    if (destroyed) return;
    if (!renderFrame) renderFrame = requestAnimationFrame(render);
  }

  subscribe(scheduleRender);

  function nextPaint(timeoutMs = 48) {
    return new Promise((resolve) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        resolve();
      };
      const timer = window.setTimeout(finish, timeoutMs);
      requestAnimationFrame(finish);
    });
  }

  async function handle(message = {}) {
    if (destroyed) return { ok: false };
    const revision = ++transitionRevision;
    window.clearTimeout(hideTimer);
    window.clearTimeout(phaseTimer);
    if (message.phase === "end" || message.phase === "idle") {
      const terminalPhase = actionPhase(String(message.action || ""));
      publish({
        visible: true,
        phase: terminalPhase === "active" ? "end" : terminalPhase,
        action: String(message.action || snapshot().action || "working"),
      });
      phaseTimer = window.setTimeout(
        () => {
          if (revision !== transitionRevision) return;
          publish({ phase: "end" });
        },
        terminalPhase === "active" ? 0 : 140,
      );
      hideTimer = window.setTimeout(() => {
        if (revision !== transitionRevision) return;
        publish({ visible: false, phase: "idle", action: "" });
      }, 320);
      return { ok: true };
    }
    const point = clampPoint(resolvePoint(message));
    publish({
      visible: true,
      phase: "start",
      action: String(message.action || "working"),
    });
    // Keep start and move on separate paint frames. The background waits for
    // this acknowledgement, so even an instantaneous DOM action cannot erase
    // the operator-visible movement before it is rendered. The timeout in
    // nextPaint prevents a hidden/throttled document from blocking commands.
    await nextPaint();
    if (revision !== transitionRevision) return { ok: true };
    publish({ phase: "move", x: point.x, y: point.y });
    await nextPaint();
    if (revision !== transitionRevision) return { ok: true };
    phaseTimer = window.setTimeout(() => {
      if (revision !== transitionRevision) return;
      publish({ phase: "active" });
    }, 180);
    return { ok: true };
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "echo.cursorOverlay") return false;
    handle(message).then(sendResponse, () => sendResponse({ ok: false }));
    return true;
  });

  // onUpdated("complete") can race document_start injection. Announcing that
  // this exact document is ready lets the service worker replay any active
  // command deterministically after navigation.
  try {
    chrome.runtime.sendMessage(
      { type: "echo.cursorOverlayReady" },
      () => void chrome.runtime.lastError,
    );
  } catch {
    // Restricted browser pages may reject extension messaging.
  }

  function destroy() {
    destroyed = true;
    transitionRevision += 1;
    window.clearTimeout(hideTimer);
    window.clearTimeout(phaseTimer);
    if (renderFrame) cancelAnimationFrame(renderFrame);
    renderFrame = 0;
    subscribers.clear();
    overlayHost?.remove();
    overlayHost = null;
  }

  globalThis.__ECHO_CURSOR_OVERLAY_STORE__ = {
    getSnapshot: snapshot,
    subscribe,
    destroy,
  };
})();

