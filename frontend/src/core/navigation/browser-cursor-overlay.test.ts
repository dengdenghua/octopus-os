import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("browser relay cursor overlay", () => {
  let onMessage:
    | ((
        message: Record<string, unknown>,
        sender: unknown,
        respond: (value: unknown) => void,
      ) => boolean)
    | null;

  beforeEach(() => {
    onMessage = null;
    document.documentElement.innerHTML = "<head></head><body></body>";
    vi.stubGlobal("chrome", {
      runtime: {
        onMessage: {
          addListener: (listener: typeof onMessage) => {
            onMessage = listener;
          },
        },
      },
    });
    const source = readFileSync(
      resolve(
        process.cwd(),
        "../extensions/echo-browser-relay/cursor-overlay.js",
      ),
      "utf8",
    );
    window.eval(source);
  });

  afterEach(() => {
    (
      globalThis as typeof globalThis & {
        __ECHO_CURSOR_OVERLAY_STORE__?: { destroy?: () => void };
      }
    ).__ECHO_CURSOR_OVERLAY_STORE__?.destroy?.();
    delete (
      globalThis as typeof globalThis & {
        __ECHO_CURSOR_OVERLAY_STORE__?: unknown;
      }
    ).__ECHO_CURSOR_OVERLAY_STORE__;
    document.getElementById("echo-agent-cursor-overlay-host")?.remove();
    vi.unstubAllGlobals();
  });

  it("renders inside an isolated, click-through overlay at the target", async () => {
    const target = document.createElement("button");
    target.id = "submit";
    target.getBoundingClientRect = () =>
      ({
        x: 80,
        y: 40,
        left: 80,
        top: 40,
        right: 120,
        bottom: 60,
        width: 40,
        height: 20,
        toJSON: () => ({}),
      }) as DOMRect;
    document.body.appendChild(target);

    expect(onMessage).not.toBeNull();
    await new Promise<void>((resolve) =>
      onMessage?.(
        {
          type: "echo.cursorOverlay",
          phase: "start",
          action: "click",
          selector: "#submit",
        },
        {},
        () => resolve(),
      ),
    );

    const host = document.getElementById("echo-agent-cursor-overlay-host");
    const cursor = host?.shadowRoot?.getElementById("cursor");
    expect(host?.style.pointerEvents).toBe("none");
    expect(cursor).toHaveAttribute("data-visible", "true");
    expect(cursor?.style.transform).toBe("translate3d(100px,50px,0)");
    expect(host?.shadowRoot?.getElementById("label")).toHaveTextContent(
      "EchoAI · click",
    );
  });

  it("is loaded before page actions and bracketed around every relay command", () => {
    const extensionRoot = resolve(
      process.cwd(),
      "../extensions/echo-browser-relay",
    );
    const manifest = JSON.parse(
      readFileSync(resolve(extensionRoot, "manifest.json"), "utf8"),
    ) as { content_scripts?: Array<{ js?: string[]; run_at?: string }> };
    const background = readFileSync(
      resolve(extensionRoot, "background.js"),
      "utf8",
    );

    const cursorScriptIndex =
      manifest.content_scripts?.findIndex((entry) =>
        entry.js?.includes("cursor-overlay.js"),
      ) ?? -1;
    const pageActionsIndex =
      manifest.content_scripts?.findIndex((entry) =>
        entry.js?.includes("content.js"),
      ) ?? -1;

    expect(cursorScriptIndex).toBeGreaterThanOrEqual(0);
    expect(pageActionsIndex).toBeGreaterThan(cursorScriptIndex);
    expect(manifest.content_scripts?.[cursorScriptIndex]).toMatchObject({
      js: ["cursor-overlay.js"],
      run_at: "document_start",
    });
    expect(manifest.content_scripts?.[pageActionsIndex]).toMatchObject({
      js: ["content.js"],
      run_at: "document_idle",
    });
    expect(background).toContain(
      'await setPageCursorOverlay(tabId, "start", action, params)',
    );
    expect(background).toContain(
      'await setPageCursorOverlay(tabId, "end", action)',
    );
  });
});
