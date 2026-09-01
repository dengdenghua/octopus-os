import { afterEach, describe, expect, it, vi } from "vitest";

import {
  INSPECT_INJECTED_SCRIPT,
  inspectInjectedScript,
} from "./inspect-injected-script";

describe("INSPECT_INJECTED_SCRIPT", () => {
  afterEach(() => {
    delete (window as Window & { __echoInspectInstalled?: boolean })
      .__echoInspectInstalled;
    document.getElementById("__echo_inspect_outline__")?.remove();
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("reports a stable page-node selector from a real click flow", () => {
    document.body.innerHTML =
      '<main><h1 data-page-node-id="hero-title">Echo Age</h1></main>';
    const target = document.querySelector("h1")!;
    Object.defineProperty(document, "elementFromPoint", {
      configurable: true,
      value: vi.fn(() => target),
    });
    vi.spyOn(target, "getBoundingClientRect").mockReturnValue({
      x: 10,
      y: 20,
      width: 300,
      height: 48,
      top: 20,
      right: 310,
      bottom: 68,
      left: 10,
      toJSON: () => ({}),
    });
    const postMessage = vi
      .spyOn(window, "postMessage")
      .mockImplementation(() => undefined);

    window.eval(INSPECT_INJECTED_SCRIPT);
    window.dispatchEvent(
      new MessageEvent("message", {
        data: { type: "echo:inspect:enable" },
      }),
    );
    document.dispatchEvent(
      new MouseEvent("click", { bubbles: true, clientX: 12, clientY: 24 }),
    );

    const selected = postMessage.mock.calls
      .map(([message]) => message as { type?: string; payload?: unknown })
      .find((message) => message.type === "echo:inspect:select");
    expect(selected?.payload).toEqual(
      expect.objectContaining({
        selector: '[data-page-node-id="hero-title"]',
        tagName: "h1",
        textContent: "Echo Age",
      }),
    );
  });

  it("supports visual body editing, save snapshots, and cancel restore", () => {
    document.body.innerHTML = "<main><h1>Original</h1></main>";
    const postMessage = vi
      .spyOn(window, "postMessage")
      .mockImplementation(() => undefined);

    window.eval(INSPECT_INJECTED_SCRIPT);
    window.dispatchEvent(
      new MessageEvent("message", { data: { type: "echo:edit:enable" } }),
    );
    expect(document.body).toHaveAttribute("contenteditable", "true");

    document.querySelector("h1")!.textContent = "Human edit";
    document
      .querySelector("h1")!
      .dispatchEvent(new InputEvent("input", { bubbles: true }));
    expect(postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "echo:edit:state",
        active: true,
        dirty: true,
      }),
      "*",
    );
    window.dispatchEvent(
      new MessageEvent("message", {
        data: { type: "echo:edit:request-save" },
      }),
    );
    const snapshot = postMessage.mock.calls
      .map(([message]) => message as { type?: string; bodyHtml?: string })
      .find((message) => message.type === "echo:edit:content");
    expect(snapshot?.bodyHtml).toContain("<h1>Human edit</h1>");
    expect(snapshot?.bodyHtml).not.toContain("contenteditable");

    window.dispatchEvent(
      new MessageEvent("message", { data: { type: "echo:edit:cancel" } }),
    );
    expect(document.body.innerHTML).toBe("<main><h1>Original</h1></main>");
    expect(document.body).not.toHaveAttribute("contenteditable");
  });

  it("restores an originally empty body when an edit is cancelled", () => {
    const postMessage = vi
      .spyOn(window, "postMessage")
      .mockImplementation(() => undefined);
    window.eval(INSPECT_INJECTED_SCRIPT);
    window.dispatchEvent(
      new MessageEvent("message", { data: { type: "echo:edit:enable" } }),
    );
    document.body.innerHTML = "<p>Unsaved</p>";

    window.dispatchEvent(
      new MessageEvent("message", { data: { type: "echo:edit:cancel" } }),
    );

    expect(document.body.innerHTML).toBe("");
    expect(document.body).not.toHaveAttribute("contenteditable");
    expect(postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: "echo:edit:state", active: false }),
      "*",
    );
  });

  it("freezes links and form actions while keeping the page editable", () => {
    document.body.innerHTML = [
      '<a href="https://example.com/next">Open</a>',
      '<form action="https://example.com/submit"><button>Submit</button></form>',
    ].join("");
    const linkHandler = vi.fn();
    const submitHandler = vi.fn();
    document.querySelector("a")!.addEventListener("click", linkHandler);
    document.querySelector("form")!.addEventListener("submit", submitHandler);

    window.eval(INSPECT_INJECTED_SCRIPT);
    window.dispatchEvent(
      new MessageEvent("message", { data: { type: "echo:edit:enable" } }),
    );
    const linkClick = new MouseEvent("click", {
      bubbles: true,
      cancelable: true,
    });
    document.querySelector("a")!.dispatchEvent(linkClick);
    const submit = new Event("submit", { bubbles: true, cancelable: true });
    document.querySelector("form")!.dispatchEvent(submit);

    expect(linkClick.defaultPrevented).toBe(true);
    expect(submit.defaultPrevented).toBe(true);
    expect(linkHandler).not.toHaveBeenCalled();
    expect(submitHandler).not.toHaveBeenCalled();
  });

  it("rejects page-script commands that do not carry the private bridge token", () => {
    const iframe = document.createElement("iframe");
    document.body.appendChild(iframe);
    const frameWindow = iframe.contentWindow!;
    const frameBody = iframe.contentDocument!.body;
    frameWindow.eval(inspectInjectedScript("private-token"));

    frameWindow.dispatchEvent(
      new MessageEvent("message", { data: { type: "echo:edit:enable" } }),
    );
    expect(frameBody).not.toHaveAttribute("contenteditable");

    frameWindow.dispatchEvent(
      new MessageEvent("message", {
        data: {
          type: "echo:edit:enable",
          echoBridgeToken: "private-token",
        },
      }),
    );
    expect(frameBody).toHaveAttribute("contenteditable", "true");
  });
});
