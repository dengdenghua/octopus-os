(() => {
  "use strict";

  const MAX_TEXT = 20_000;
  const DEFAULT_LIMIT = 30;
  const CACHE_LIMIT = 300;
  const existingCache = globalThis.__ECHO_DOM_ACTION_CACHE__;
  const snapshotCache = existingCache instanceof Map ? existingCache : new Map();
  globalThis.__ECHO_DOM_ACTION_CACHE__ = snapshotCache;

  function textOf(element) {
    return String(
      element.innerText ||
        element.textContent ||
        element.getAttribute?.("aria-label") ||
        "",
    )
      .trim()
      .replace(/\s+/g, " ");
  }

  function typeOf(element) {
    return String(element.type || element.getAttribute?.("type") || "").toLowerCase();
  }

  function isVisible(element) {
    if (!(element instanceof Element) || !element.isConnected) return false;
    const style = window.getComputedStyle(element);
    if (
      style.display === "none" ||
      style.visibility === "hidden" ||
      style.visibility === "collapse" ||
      Number(style.opacity) === 0
    ) {
      return false;
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function isDisabled(element) {
    return (
      element.disabled === true ||
      element.matches?.(":disabled") === true ||
      element.getAttribute?.("aria-disabled") === "true"
    );
  }

  function uniqueMatch(selector, element) {
    try {
      const matches = document.querySelectorAll(selector);
      return matches.length === 1 && matches[0] === element;
    } catch {
      return false;
    }
  }

  function escapeAttribute(value) {
    return String(value).replaceAll("\\", "\\\\").replaceAll('"', '\\"');
  }

  function selectorFor(element) {
    const tag = element.tagName.toLowerCase();
    const candidates = [];
    const testId = element.getAttribute("data-testid");
    if (testId) candidates.push(`[data-testid="${escapeAttribute(testId)}"]`);
    if (element.id) candidates.push(`#${CSS.escape(element.id)}`);
    const aria = element.getAttribute("aria-label");
    if (aria) candidates.push(`${tag}[aria-label="${escapeAttribute(aria)}"]`);
    const name = element.getAttribute("name");
    if (name) candidates.push(`${tag}[name="${escapeAttribute(name)}"]`);
    for (const candidate of candidates) {
      if (uniqueMatch(candidate, element)) return candidate;
    }

    let node = element;
    const path = [];
    while (node?.nodeType === Node.ELEMENT_NODE && path.length < 8) {
      let part = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(
          (child) => child.tagName === node.tagName,
        );
        if (siblings.length > 1) {
          part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
        }
      }
      path.unshift(part);
      const candidate = path.join(" > ");
      if (uniqueMatch(candidate, element)) return candidate;
      node = parent;
    }
    return candidates[0] || path.join(" > ") || tag;
  }

  function roleOf(element) {
    const explicit = element.getAttribute("role");
    if (explicit) return explicit;
    const tag = element.tagName.toLowerCase();
    if (tag === "a" && element.hasAttribute("href")) return "link";
    if (tag === "button") return "button";
    if (tag === "textarea") return "textbox";
    if (tag === "select") return "combobox";
    if (tag === "input") {
      const type = typeOf(element);
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (["button", "submit", "reset"].includes(type)) return "button";
      return "textbox";
    }
    if (element.isContentEditable) return "textbox";
    return tag;
  }

  function accessibleName(element) {
    const labelledBy = element.getAttribute("aria-labelledby");
    if (labelledBy) {
      const label = labelledBy
        .split(/\s+/)
        .map((id) => document.getElementById(id)?.textContent || "")
        .join(" ")
        .trim();
      if (label) return label;
    }
    if (element.id) {
      const label = document.querySelector(`label[for="${escapeAttribute(element.id)}"]`);
      if (label?.textContent?.trim()) return label.textContent.trim();
    }
    return String(
      element.getAttribute("aria-label") ||
        element.getAttribute("placeholder") ||
        element.getAttribute("title") ||
        textOf(element),
    )
      .trim()
      .slice(0, 160);
  }

  function describe(element) {
    const inputType = typeOf(element);
    const selector = selectorFor(element);
    const value =
      inputType !== "password" && typeof element.value === "string"
        ? element.value.slice(0, 160)
        : element.isContentEditable
          ? textOf(element).slice(0, 160)
          : null;
    const description = {
      tag: element.tagName.toLowerCase(),
      role: roleOf(element),
      name: accessibleName(element),
      text: textOf(element).slice(0, 160),
      selector,
      selectorUnique: uniqueMatch(selector, element),
      href: element.href || null,
      type: inputType || null,
      value,
      checked:
        typeof element.checked === "boolean" && ["checkbox", "radio"].includes(inputType)
          ? element.checked
          : null,
      disabled: isDisabled(element),
    };
    if (description.selectorUnique) {
      snapshotCache.delete(selector);
      snapshotCache.set(selector, {
        role: description.role,
        name: description.name,
        text: description.text,
        type: description.type,
      });
      while (snapshotCache.size > CACHE_LIMIT) {
        snapshotCache.delete(snapshotCache.keys().next().value);
      }
    }
    return description;
  }

  function pick(selector, limit) {
    return Array.from(document.querySelectorAll(selector))
      .filter(isVisible)
      .slice(0, limit)
      .map(describe);
  }

  function pageAgentSnapshot() {
    return globalThis.__echoPageAgent?.snapshot?.() ?? null;
  }

  function pageState(limit = DEFAULT_LIMIT) {
    const bodyText = String(document.body?.innerText || document.body?.textContent || "");
    return {
      ok: true,
      url: location.href,
      title: document.title,
      textLength: bodyText.length,
      viewport: { width: window.innerWidth, height: window.innerHeight },
      scroll: { x: window.scrollX, y: window.scrollY },
      links: pick("a[href]", limit),
      buttons: pick('button,[role="button"],input[type="button"],input[type="submit"]', limit),
      inputs: pick('input,textarea,select,[contenteditable="true"]', limit),
      headings: pick("h1,h2,h3", limit),
      pageAgent: pageAgentSnapshot(),
    };
  }

  function findElement(selector) {
    if (!selector) throw new Error("selector is required");
    let element;
    try {
      element = document.querySelector(selector);
    } catch (error) {
      throw new Error(`invalid selector: ${selector}: ${error.message}`);
    }
    if (!element) throw new Error(`selector not found: ${selector}`);
    return element;
  }

  function assertActionable(element, action, { allowHidden = false } = {}) {
    if (!element.isConnected) throw new Error(`${action} target is detached`);
    if (!allowHidden && !isVisible(element)) throw new Error(`${action} target is not visible`);
    if (isDisabled(element)) throw new Error(`${action} target is disabled`);
    if (action === "type" && (element.readOnly === true || element.getAttribute("aria-readonly") === "true")) {
      throw new Error("type target is read-only");
    }
  }

  function rectSignature(element) {
    const rect = element.getBoundingClientRect();
    return [rect.x, rect.y, rect.width, rect.height].map((value) => Math.round(value * 10) / 10);
  }

  function sameRect(left, right) {
    return Boolean(left && right && left.every((value, index) => value === right[index]));
  }

  function matchesFingerprint(element, fingerprint) {
    if (!fingerprint || roleOf(element) !== fingerprint.role) return false;
    if (fingerprint.type && typeOf(element) !== fingerprint.type) return false;
    const name = accessibleName(element);
    if (fingerprint.name) return name === fingerprint.name;
    return Boolean(fingerprint.text && textOf(element) === fingerprint.text);
  }

  function resolveCachedElement(selector) {
    const fingerprint = snapshotCache.get(selector);
    let direct = null;
    try {
      direct = document.querySelector(selector);
    } catch (error) {
      throw new Error(`invalid selector: ${selector}: ${error.message}`);
    }
    const fragile = selector.includes(":nth-of-type(");
    if (direct && (!fingerprint || !fragile || matchesFingerprint(direct, fingerprint))) {
      return { element: direct, recovered: false, reason: "" };
    }
    if (!fingerprint) {
      return { element: null, recovered: false, reason: "not found" };
    }
    const matches = Array.from(
      document.querySelectorAll(
        'a[href],button,input,textarea,select,[role],[contenteditable="true"]',
      ),
    ).filter((element) => matchesFingerprint(element, fingerprint));
    if (matches.length === 1) {
      return { element: matches[0], recovered: true, reason: "" };
    }
    return {
      element: null,
      recovered: false,
      reason: matches.length > 1 ? "semantic recovery is ambiguous" : "not found",
    };
  }

  function hitTarget(element) {
    const rect = element.getBoundingClientRect();
    const x = Math.max(0, Math.min(window.innerWidth - 1, rect.left + rect.width / 2));
    const y = Math.max(0, Math.min(window.innerHeight - 1, rect.top + rect.height / 2));
    const hit = document.elementFromPoint(x, y);
    return hit && (hit === element || element.contains(hit)) ? hit : null;
  }

  async function waitForActionable(selector, action, params, { hitTest = false } = {}) {
    if (!selector) throw new Error("selector is required");
    const timeout = Math.max(0, Number(params.timeout ?? 10_000));
    const started = Date.now();
    let previousElement = null;
    let previousRect = null;
    let lastReason = "not found";
    while (true) {
      const resolved = resolveCachedElement(selector);
      const element = resolved.element;

      if (!element) {
        lastReason = resolved.reason;
        previousElement = null;
        previousRect = null;
      } else if (!isVisible(element)) {
        lastReason = "not visible";
        previousElement = element;
        previousRect = null;
      } else if (isDisabled(element)) {
        lastReason = "disabled";
        previousElement = element;
        previousRect = null;
      } else if (
        action === "type" &&
        (element.readOnly === true || element.getAttribute("aria-readonly") === "true")
      ) {
        lastReason = "read-only";
        previousElement = element;
        previousRect = null;
      } else {
        element.scrollIntoView?.({ block: "center", inline: "center", behavior: "instant" });
        const nextRect = rectSignature(element);
        if (element !== previousElement || !sameRect(previousRect, nextRect)) {
          lastReason = "moving";
          previousElement = element;
          previousRect = nextRect;
        } else if (hitTest && !hitTarget(element)) {
          lastReason = "covered or outside the viewport";
        } else {
          return {
            element,
            recoveredFromSelector: resolved.recovered ? selector : null,
          };
        }
      }

      if (Date.now() - started >= timeout) {
        throw new Error(
          `${action} timed out after ${timeout}ms: selector=${selector} is ${lastReason}`,
        );
      }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }

  function focusElement(element) {
    element.scrollIntoView?.({ block: "center", inline: "center", behavior: "instant" });
    element.focus?.({ preventScroll: true });
  }

  function inputEvent(type, init) {
    try {
      return new InputEvent(type, init);
    } catch {
      return new Event(type, { bubbles: init?.bubbles !== false, cancelable: init?.cancelable });
    }
  }

  function setNativeValue(element, value) {
    let prototype = Object.getPrototypeOf(element);
    let setter = null;
    while (prototype && !setter) {
      setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set || null;
      prototype = Object.getPrototypeOf(prototype);
    }
    if (setter) setter.call(element, value);
    else element.value = value;
  }

  function typeInto(element, text, clear) {
    assertActionable(element, "type");
    focusElement(element);
    const tag = element.tagName.toLowerCase();
    const inputType = typeOf(element);
    if (["checkbox", "radio", "button", "submit", "reset", "file"].includes(inputType)) {
      throw new Error(`type does not support input[type=${inputType}]`);
    }

    if (tag === "select") {
      const option = Array.from(element.options).find(
        (item) => item.value === text || item.textContent?.trim() === text,
      );
      if (!option) throw new Error(`select option not found: ${text}`);
      setNativeValue(element, option.value);
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      return { ok: true, value: element.value, selector: selectorFor(element) };
    }

    const contentEditable = element.isContentEditable;
    const current = contentEditable ? textOf(element) : String(element.value || "");
    const next = clear ? text : `${current}${text}`;
    const before = inputEvent("beforeinput", {
      bubbles: true,
      cancelable: true,
      composed: true,
      data: text,
      inputType: clear ? "insertReplacementText" : "insertText",
    });
    if (!element.dispatchEvent(before)) throw new Error("type was prevented by the page");

    if (contentEditable) {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(element);
      if (!clear) range.collapse(false);
      selection?.removeAllRanges();
      selection?.addRange(range);
      let inserted = false;
      try {
        inserted = document.execCommand?.("insertText", false, text) === true;
      } catch {
        inserted = false;
      }
      if (!inserted) {
        element.textContent = next;
        element.dispatchEvent(
          inputEvent("input", {
            bubbles: true,
            composed: true,
            data: text,
            inputType: clear ? "insertReplacementText" : "insertText",
          }),
        );
      }
      element.dispatchEvent(new Event("change", { bubbles: true }));
      return { ok: true, value: textOf(element), selector: selectorFor(element) };
    }

    setNativeValue(element, next);
    element.dispatchEvent(
      inputEvent("input", {
        bubbles: true,
        composed: true,
        data: text,
        inputType: clear ? "insertReplacementText" : "insertText",
      }),
    );
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true, value: element.value, selector: selectorFor(element) };
  }

  function keyCodeFor(key) {
    const codes = {
      Enter: "Enter",
      Escape: "Escape",
      Tab: "Tab",
      Backspace: "Backspace",
      Delete: "Delete",
      ArrowUp: "ArrowUp",
      ArrowDown: "ArrowDown",
      ArrowLeft: "ArrowLeft",
      ArrowRight: "ArrowRight",
      " ": "Space",
    };
    if (codes[key]) return codes[key];
    if (key.length === 1 && /[a-z]/i.test(key)) return `Key${key.toUpperCase()}`;
    if (key.length === 1 && /\d/.test(key)) return `Digit${key}`;
    return key;
  }

  function pressKey(target, params) {
    assertActionable(target, "press");
    focusElement(target);
    const key = String(params.key || "Enter");
    const init = {
      key,
      code: String(params.code || keyCodeFor(key)),
      bubbles: true,
      cancelable: true,
      composed: true,
      altKey: params.altKey === true,
      ctrlKey: params.ctrlKey === true,
      metaKey: params.metaKey === true,
      shiftKey: params.shiftKey === true,
      repeat: false,
    };
    let submitted = false;
    const form = target.form || target.closest?.("form") || null;
    const observeSubmit = () => {
      submitted = true;
    };
    form?.addEventListener("submit", observeSubmit, { capture: true, once: true });
    const proceed = target.dispatchEvent(new KeyboardEvent("keydown", init));
    if (proceed && key.length === 1) {
      target.dispatchEvent(new KeyboardEvent("keypress", init));
    }
    target.dispatchEvent(new KeyboardEvent("keyup", init));

    if (proceed && key === "Enter" && form && !submitted && !params.shiftKey) {
      const tag = target.tagName.toLowerCase();
      if (tag !== "textarea" && !target.isContentEditable) form.requestSubmit?.();
    } else if (proceed && key === " " && target.matches?.('button,[role="button"]')) {
      target.click();
    } else if (proceed && key === "Tab") {
      const focusable = Array.from(
        document.querySelectorAll(
          'a[href],button,input,textarea,select,[tabindex]:not([tabindex="-1"]),[contenteditable="true"]',
        ),
      ).filter((element) => isVisible(element) && !isDisabled(element));
      const index = focusable.indexOf(target);
      const offset = params.shiftKey ? -1 : 1;
      const next = focusable[(index + offset + focusable.length) % focusable.length];
      next?.focus();
    }
    return { ok: true, key, defaultPrevented: !proceed };
  }

  function waitFor(params) {
    const selector = String(params.selector || "");
    const state = String(params.state || "attached");
    const expectedText = params.text == null ? null : String(params.text);
    const timeout = Math.max(0, Number(params.timeout || 10_000));
    const started = Date.now();
    return new Promise((resolve, reject) => {
      const check = () => {
        let element = null;
        if (selector) {
          try {
            element = document.querySelector(selector);
          } catch (error) {
            reject(new Error(`invalid selector: ${selector}: ${error.message}`));
            return;
          }
        }
        const matched =
          !selector ||
          (state === "attached" && Boolean(element)) ||
          (state === "detached" && !element) ||
          (state === "visible" && Boolean(element && isVisible(element))) ||
          (state === "hidden" && Boolean(!element || !isVisible(element)));
        const textMatched = expectedText == null || textOf(element || document.body).includes(expectedText);
        if (matched && textMatched) {
          resolve({ ok: true, state, elapsedMs: Date.now() - started });
        } else if (Date.now() - started >= timeout) {
          reject(
            new Error(
              `wait timed out after ${timeout}ms: selector=${selector || "<page>"} state=${state}`,
            ),
          );
        } else {
          setTimeout(check, 100);
        }
      };
      check();
    });
  }

  async function run(action, rawParams = {}) {
    const params = rawParams && typeof rawParams === "object" ? rawParams : {};
    const selector = String(params.selector || "");
    if (action === "pageAction" || action === "pageInput" || action === "pageCapability") {
      if (!globalThis.__echoPageAgent?.run) {
        throw new Error("page agent bridge is not available on this page");
      }
      const payload =
        action === "pageAction"
          ? { type: "click", id: String(params.id || ""), confirm: params.confirm === true }
          : action === "pageInput"
            ? {
                type: "input",
                id: String(params.id || ""),
                text: String(params.text || ""),
                clear: params.clear !== false,
              }
            : {
                type: "capability",
                id: String(params.id || ""),
                input: params.input && typeof params.input === "object" ? params.input : {},
                confirm: params.confirm === true,
              };
      return globalThis.__echoPageAgent.run(payload);
    }
    if (action === "click") {
      const target = await waitForActionable(selector, "click", params, { hitTest: true });
      const { element } = target;
      assertActionable(element, "click");
      focusElement(element);
      element.click();
      return {
        ok: true,
        selector: selectorFor(element),
        recoveredFromSelector: target.recoveredFromSelector,
      };
    }
    if (action === "type") {
      const target = await waitForActionable(selector, "type", params);
      return {
        ...typeInto(target.element, String(params.text || ""), params.clear === true),
        recoveredFromSelector: target.recoveredFromSelector,
      };
    }
    if (action === "hover") {
      const target = await waitForActionable(selector, "hover", params, { hitTest: true });
      const { element } = target;
      assertActionable(element, "hover");
      focusElement(element);
      element.dispatchEvent(new PointerEvent("pointerover", { bubbles: true, composed: true }));
      element.dispatchEvent(new PointerEvent("pointerenter", { bubbles: false, composed: true }));
      element.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, composed: true }));
      element.dispatchEvent(new MouseEvent("mouseenter", { bubbles: false, composed: true }));
      return {
        ok: true,
        selector: selectorFor(element),
        recoveredFromSelector: target.recoveredFromSelector,
      };
    }
    if (action === "scroll") {
      if (selector) {
        const element = findElement(selector);
        element.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
      } else {
        window.scrollBy({ top: Number(params.y ?? params.deltaY ?? 700), left: 0, behavior: "instant" });
      }
      return { ok: true, x: window.scrollX, y: window.scrollY };
    }
    if (action === "press") {
      const resolved = selector
        ? await waitForActionable(selector, "press", params)
        : { element: document.activeElement || document.body, recoveredFromSelector: null };
      return {
        ...pressKey(resolved.element, params),
        selector: selectorFor(resolved.element),
        recoveredFromSelector: resolved.recoveredFromSelector,
      };
    }
    if (action === "wait") return waitFor(params);
    if (action === "state") return pageState(Math.max(1, Number(params.max_items || DEFAULT_LIMIT)));
    if (action === "extract" || action === "aria") {
      const fullText = String(document.body?.innerText || document.documentElement?.innerText || "").trim();
      const state = pageState(Math.max(1, Number(params.max_items || DEFAULT_LIMIT)));
      const nodes = [...state.links, ...state.buttons, ...state.inputs, ...state.headings];
      return {
        ok: true,
        url: location.href,
        title: document.title,
        text: fullText.slice(0, MAX_TEXT),
        textLength: fullText.length,
        truncated: fullText.length > MAX_TEXT,
        pageAgent: state.pageAgent,
        nodes,
      };
    }
    throw new Error(`unsupported DOM action: ${action}`);
  }

  globalThis.__ECHO_DOM_ACTIONS__ = Object.freeze({ run, pageState });
})();

