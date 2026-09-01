(() => {
  const version =
    typeof chrome !== "undefined" && chrome.runtime?.getManifest
      ? chrome.runtime.getManifest().version
      : "local-dev";

  Object.defineProperty(window, "__ECHO_BROWSER_RELAY__", {
    value: { ready: true, version },
    configurable: true,
  });

  document.documentElement.dataset.echoBrowserRelay = "ready";

  const INDICATOR_ID = "echo-browser-control-indicator";
  const STYLE_ID = "echo-browser-control-indicator-style";
  let indicatorHideTimer = 0;

  function ensureIndicatorStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
#${INDICATOR_ID} {
  position: fixed;
  inset: 0;
  z-index: 2147483646;
  pointer-events: none;
  opacity: 0;
  border: 1px solid rgba(20, 184, 166, 0.62);
  box-shadow:
    inset 0 0 0 1px rgba(20, 184, 166, 0.26),
    inset 0 0 18px rgba(20, 184, 166, 0.16);
  transition: opacity 180ms ease, border-color 180ms ease, box-shadow 180ms ease;
}
#${INDICATOR_ID}[data-state="active"],
#${INDICATOR_ID}[data-state="action"],
#${INDICATOR_ID}[data-state="paused"] {
  opacity: 1;
}
#${INDICATOR_ID}[data-state="action"] {
  animation: echo-control-edge-pulse 620ms ease-out 1;
}
#${INDICATOR_ID}[data-state="paused"] {
  border-color: rgba(245, 158, 11, 0.78);
  box-shadow:
    inset 0 0 0 1px rgba(245, 158, 11, 0.34),
    inset 0 0 18px rgba(245, 158, 11, 0.18);
}
@keyframes echo-control-edge-pulse {
  0% {
    box-shadow:
      inset 0 0 0 1px rgba(20, 184, 166, 0.24),
      inset 0 0 10px rgba(20, 184, 166, 0.10);
  }
  45% {
    box-shadow:
      inset 0 0 0 2px rgba(20, 184, 166, 0.72),
      inset 0 0 24px rgba(20, 184, 166, 0.24);
  }
  100% {
    box-shadow:
      inset 0 0 0 1px rgba(20, 184, 166, 0.26),
      inset 0 0 18px rgba(20, 184, 166, 0.16);
  }
}
@media (prefers-reduced-motion: reduce) {
  #${INDICATOR_ID} {
    transition: none;
  }
  #${INDICATOR_ID}[data-state="action"] {
    animation: none;
  }
}
`;
    document.documentElement.appendChild(style);
  }

  function ensureIndicator() {
    ensureIndicatorStyle();
    let node = document.getElementById(INDICATOR_ID);
    if (!node) {
      node = document.createElement("div");
      node.id = INDICATOR_ID;
      node.setAttribute("aria-hidden", "true");
      node.dataset.state = "idle";
      document.documentElement.appendChild(node);
    }
    return node;
  }

  function setControlIndicator(message = {}) {
    const mode = String(message.mode || "idle");
    const node = ensureIndicator();
    window.clearTimeout(indicatorHideTimer);
    if (mode === "active" || mode === "action") {
      node.hidden = false;
      node.dataset.state = mode;
      node.dataset.action = String(message.action || "");
      return;
    }
    if (mode === "paused") {
      node.hidden = false;
      node.dataset.state = "paused";
      indicatorHideTimer = window.setTimeout(() => {
        node.dataset.state = "idle";
        node.hidden = true;
      }, 1600);
      return;
    }
    node.dataset.state = "idle";
    indicatorHideTimer = window.setTimeout(() => {
      node.hidden = true;
    }, 420);
  }

  if (typeof chrome !== "undefined" && chrome.runtime?.onMessage) {
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (message?.type === "echo.controlIndicator") {
        setControlIndicator(message);
        sendResponse({ ok: true });
      }
      return false;
    });
  }

  let lastActivityAt = 0;
  function compactText(value, limit = 120) {
    return String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
  }

  function semanticTarget(event) {
    const raw = event?.target;
    if (!(raw instanceof Element)) return null;
    const node =
      raw.closest(
        "button,a,input,textarea,select,[role],[contenteditable='true']",
      ) || raw;
    if (!(node instanceof HTMLElement)) return null;
    return {
      tag: node.tagName.toLowerCase(),
      role: node.getAttribute("role") || undefined,
      id: node.id || undefined,
      name: node.getAttribute("name") || undefined,
      aria_label: node.getAttribute("aria-label") || undefined,
      text: compactText(node.innerText || node.textContent),
    };
  }

  function reportUserActivity(kind, event) {
    if (!event?.isTrusted || typeof chrome === "undefined" || !chrome.runtime?.sendMessage) {
      return;
    }
    const now = Date.now();
    if (now - lastActivityAt < 400) return;
    lastActivityAt = now;
    chrome.runtime
      .sendMessage({
        type: "echo.userActivity",
        activity: {
          kind,
          at: now / 1000,
          url: location.href,
          title: document.title,
          target: semanticTarget(event),
          data:
            event instanceof KeyboardEvent &&
            ["Enter", "Escape", "Tab", "Backspace", "Delete"].includes(event.key)
              ? { key: event.key }
              : undefined,
        },
      })
      .catch(() => {
        // The extension may have been reloaded while this content script is still alive.
      });
  }

  document.addEventListener("pointerdown", (event) => reportUserActivity("pointerdown", event), {
    capture: true,
    passive: true,
  });
  document.addEventListener("keydown", (event) => reportUserActivity("keydown", event), {
    capture: true,
  });
  document.addEventListener("input", (event) => reportUserActivity("input", event), {
    capture: true,
  });
  document.addEventListener("paste", (event) => reportUserActivity("paste", event), {
    capture: true,
  });
})();

