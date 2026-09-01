import { useState } from "react";
import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { ChatPageLayout } from "./chat-page-layout";

function OverlayLifecycleHarness() {
  const [open, setOpen] = useState(false);
  const [revision, setRevision] = useState(0);

  return (
    <ChatPageLayout
      header={
        <button type="button" onClick={() => setOpen(true)}>
          Open workbench
        </button>
      }
      messageList={<div>Messages</div>}
      inputArea={<div>Composer</div>}
      secondaryPanel={
        open ? (
          <button
            type="button"
            onClick={() => setRevision((value) => value + 1)}
          >
            Panel action {revision}
          </button>
        ) : undefined
      }
      onSecondaryClose={() => setOpen(false)}
    />
  );
}

function OverlayRecoveryHarness() {
  const [showUtility, setShowUtility] = useState(true);
  const [revision, setRevision] = useState(0);

  return (
    <>
      <ChatPageLayout
        header={<div>Header</div>}
        messageList={<div>Messages</div>}
        inputArea={<div>Composer</div>}
        secondaryPanel={
          <div>
            {showUtility ? (
              <button type="button" onClick={() => setShowUtility(false)}>
                Unmount utility
              </button>
            ) : (
              <span>Utility removed</span>
            )}
            <span>Revision {revision}</span>
          </div>
        }
      />
      <div data-radix-portal="">
        <button type="button" onClick={() => setRevision((value) => value + 1)}>
          Portal action {revision}
        </button>
      </div>
    </>
  );
}

describe("ChatPageLayout", () => {
  let overlayHeight = 148;
  let layoutWidth = 1400;
  const originalResizeObserver = globalThis.ResizeObserver;
  const originalInnerWidth = window.innerWidth;

  beforeEach(() => {
    overlayHeight = 148;
    layoutWidth = 1400;
    window.localStorage.removeItem("echo:chatSidebarWidth");
    window.localStorage.removeItem("echo:chatSecondaryPanelWidth");
    Object.assign(globalThis, { ResizeObserver: undefined });

    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
      function () {
        const height = this.hasAttribute("data-chat-input-overlay")
          ? overlayHeight
          : 0;
        const width = this.hasAttribute("data-chat-page-layout-root")
          ? layoutWidth
          : 0;
        return {
          x: 0,
          y: 0,
          top: 0,
          right: width,
          bottom: height,
          left: 0,
          width,
          height,
          toJSON: () => ({}),
        };
      },
    );
  });

  afterEach(() => {
    Object.assign(globalThis, { ResizeObserver: originalResizeObserver });
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: originalInnerWidth,
    });
    vi.restoreAllMocks();
  });

  test("publishes live composer height for floating conversation controls", () => {
    renderWithProviders(
      <ChatPageLayout
        header={<div>Header</div>}
        messageList={<div>Messages</div>}
        inputArea={<div>Composer</div>}
      />,
    );

    const workspace = screen.getByRole("region", {
      name: "Conversation workspace",
    });
    expect(workspace).toHaveStyle({
      "--chat-input-overlay-height": "148px",
    });

    overlayHeight = 149;
    fireEvent(window, new Event("resize"));

    expect(workspace).toHaveStyle({
      "--chat-input-overlay-height": "148px",
    });

    overlayHeight = 284;
    fireEvent(window, new Event("resize"));

    expect(workspace).toHaveStyle({
      "--chat-input-overlay-height": "284px",
    });
  });

  test("owns exactly one page-level heading", () => {
    const { container } = renderWithProviders(
      <ChatPageLayout
        pageTitle="New task"
        header={<div>Header</div>}
        messageList={<div>Messages</div>}
        inputArea={<h2>Welcome</h2>}
      />,
    );

    expect(
      screen.getByRole("heading", { level: 1, name: "New task" }),
    ).toBeInTheDocument();
    expect(container.querySelectorAll("h1")).toHaveLength(1);
  });

  test("uses a layout-local right drawer when a desktop container cannot fit the workbench", () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1440,
    });
    layoutWidth = 900;

    const onSecondaryClose = vi.fn();
    const { container } = renderWithProviders(
      <ChatPageLayout
        header={<div>Header</div>}
        messageList={<div>Messages</div>}
        inputArea={<div>Composer</div>}
        secondaryPanel={<div>Workbench</div>}
        onSecondaryClose={onSecondaryClose}
      />,
    );

    const workbench = screen.getByRole("dialog", {
      name: "Agent workbench",
    });
    expect(workbench).toHaveAttribute("aria-modal", "true");
    expect(workbench).toHaveFocus();
    expect(workbench).toHaveAttribute(
      "data-secondary-panel-presentation",
      "desktop-drawer",
    );
    expect(workbench).toHaveClass("absolute", "inset-y-0", "right-0");
    expect(workbench).not.toHaveClass("fixed", "bottom-0", "left-0");
    expect(
      screen.queryByRole("button", {
        name: "Expand or collapse the agent workbench drawer",
      }),
    ).not.toBeInTheDocument();

    const backdrop = container.querySelector(
      '[data-secondary-panel-backdrop="desktop-drawer"]',
    );
    expect(backdrop).toHaveClass("absolute", "inset-0");
    fireEvent.click(backdrop!);
    expect(onSecondaryClose).toHaveBeenCalledOnce();
  });

  test("manages modal focus, inert background, Escape close, and focus restoration", () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1440,
    });
    layoutWidth = 900;

    const { container } = renderWithProviders(<OverlayLifecycleHarness />);
    const opener = screen.getByRole("button", { name: "Open workbench" });
    const mainColumn = container.querySelector(
      '[data-chat-page-main-column="true"]',
    );

    opener.focus();
    fireEvent.click(opener);

    const dialog = screen.getByRole("dialog", { name: "Agent workbench" });
    expect(dialog).toHaveFocus();
    expect(mainColumn).toHaveAttribute("inert");
    expect(mainColumn).toHaveAttribute("aria-hidden", "true");

    // A normal panel render must not steal focus back from its active control.
    const panelAction = screen.getByRole("button", { name: "Panel action 0" });
    panelAction.focus();
    fireEvent.click(panelAction);
    expect(
      screen.getByRole("button", { name: "Panel action 1" }),
    ).toHaveFocus();
    expect(dialog).not.toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(
      screen.queryByRole("dialog", { name: "Agent workbench" }),
    ).not.toBeInTheDocument();
    expect(mainColumn).not.toHaveAttribute("inert");
    expect(mainColumn).not.toHaveAttribute("aria-hidden");
    expect(opener).toHaveFocus();
  });

  test("lets editable controls and nested popup surfaces consume Escape", () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1440,
    });
    layoutWidth = 900;
    const onSecondaryClose = vi.fn();

    renderWithProviders(
      <ChatPageLayout
        header={<div>Header</div>}
        messageList={<div>Messages</div>}
        inputArea={<div>Composer</div>}
        secondaryPanel={
          <div>
            <input aria-label="Guarded input" />
            <textarea aria-label="Guarded textarea" />
            <select aria-label="Guarded select" defaultValue="one">
              <option value="one">One</option>
            </select>
            <div
              contentEditable
              suppressContentEditableWarning
              role="textbox"
              aria-label="Guarded editor"
            >
              Editable
            </div>
            <div role="menu" aria-label="Nested menu">
              <button type="button" role="menuitem">
                Menu action
              </button>
            </div>
            <button type="button" onKeyDown={(event) => event.preventDefault()}>
              Handled Escape
            </button>
            <button type="button">Close with Escape</button>
          </div>
        }
        onSecondaryClose={onSecondaryClose}
      />,
    );

    const guardedControls = [
      screen.getByRole("textbox", { name: "Guarded input" }),
      screen.getByRole("textbox", { name: "Guarded textarea" }),
      screen.getByRole("combobox", { name: "Guarded select" }),
      screen.getByRole("textbox", { name: "Guarded editor" }),
      screen.getByRole("menuitem", { name: "Menu action" }),
      screen.getByRole("button", { name: "Handled Escape" }),
    ];
    for (const control of guardedControls) {
      control.focus();
      fireEvent.keyDown(control, { key: "Escape" });
    }
    expect(onSecondaryClose).not.toHaveBeenCalled();

    const closeControl = screen.getByRole("button", {
      name: "Close with Escape",
    });
    closeControl.focus();
    fireEvent.keyDown(closeControl, { key: "Escape" });
    expect(onSecondaryClose).toHaveBeenCalledOnce();
  });

  test("recovers focus after active utility unmount without stealing Radix portal focus", () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1440,
    });
    layoutWidth = 900;

    renderWithProviders(<OverlayRecoveryHarness />);
    const dialog = screen.getByRole("dialog", { name: "Agent workbench" });
    const utility = screen.getByRole("button", { name: "Unmount utility" });

    utility.focus();
    fireEvent.click(utility);
    expect(screen.getByText("Utility removed")).toBeInTheDocument();
    expect(dialog).toHaveFocus();

    const portalAction = screen.getByRole("button", {
      name: "Portal action 0",
    });
    portalAction.focus();
    fireEvent.click(portalAction);
    expect(
      screen.getByRole("button", { name: "Portal action 1" }),
    ).toHaveFocus();
    expect(dialog).not.toHaveFocus();
  });

  test("keeps the header in the conversation column while inline panels span the full shell", () => {
    layoutWidth = 1400;

    const { container } = renderWithProviders(
      <ChatPageLayout
        header={<div>Header</div>}
        messageList={<div>Messages</div>}
        inputArea={<div>Composer</div>}
        sidebar={<div>Utility</div>}
        showSidebar
        secondaryPanel={<div>Workbench</div>}
      />,
    );

    const root = container.querySelector('[data-chat-page-layout-root="true"]');
    const mainColumn = container.querySelector(
      '[data-chat-page-main-column="true"]',
    );
    const header = container.querySelector('[data-chat-page-header="true"]');
    const utility = screen.getByRole("complementary", {
      name: "Artifacts, plan, and research panel",
    });
    const workbench = screen.getByRole("complementary", {
      name: "Agent workbench",
    });

    expect(header?.parentElement).toBe(mainColumn);
    expect(mainColumn?.parentElement).toBe(root);
    expect(utility.parentElement).toBe(root);
    expect(workbench.parentElement).toBe(root);
    expect(mainColumn).not.toHaveAttribute("aria-hidden");
    expect(mainColumn).not.toHaveAttribute("inert");
    expect(workbench).not.toHaveAttribute("aria-modal");
    expect(utility).toHaveClass("border-l", "bg-background");
    expect(workbench).toHaveClass("border-l", "bg-background");
    expect(utility).not.toHaveClass(
      "backdrop-blur-[10px]",
      "shadow-[-12px_0_32px_-16px_rgba(0,0,0,0.12)]",
    );
    expect(workbench).not.toHaveClass(
      "backdrop-blur-[10px]",
      "shadow-[-12px_0_32px_-16px_rgba(0,0,0,0.12)]",
    );
  });

  test("keeps the workbench inline below the old viewport breakpoint when its container has room", () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1024,
    });
    layoutWidth = 1000;

    renderWithProviders(
      <ChatPageLayout
        header={<div>Header</div>}
        messageList={<div>Messages</div>}
        inputArea={<div>Composer</div>}
        secondaryPanel={<div>Workbench</div>}
      />,
    );

    const workbench = screen.getByRole("complementary", {
      name: "Agent workbench",
    });
    expect(workbench).toHaveAttribute(
      "data-secondary-panel-presentation",
      "inline",
    );
    expect(workbench).not.toHaveClass("fixed");
    expect(workbench).toHaveStyle({ width: "360px" });
    expect(workbench.querySelector('[role="separator"]')).toHaveAttribute(
      "aria-valuemin",
      "360",
    );
    expect(workbench.querySelector('[role="separator"]')).toHaveAttribute(
      "aria-valuemax",
      "800",
    );
  });

  test("preserves the mobile workbench drawer behavior", () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 767,
    });
    // Deliberately wider than a real mobile root so the assertion proves the
    // mobile viewport rule remains independent from the container-fit rule.
    layoutWidth = 1300;

    renderWithProviders(
      <ChatPageLayout
        header={<div>Header</div>}
        messageList={<div>Messages</div>}
        inputArea={<div>Composer</div>}
        secondaryPanel={<div>Workbench</div>}
      />,
    );

    expect(
      screen.getByRole("dialog", { name: "Agent workbench" }),
    ).toHaveAttribute("data-secondary-panel-presentation", "bottom-sheet");
    expect(screen.getByRole("dialog", { name: "Agent workbench" })).toHaveClass(
      "fixed",
      "right-0",
      "bottom-0",
      "left-0",
    );
    expect(
      screen.getByRole("dialog", { name: "Agent workbench" }),
    ).toHaveAttribute("aria-modal", "true");
    expect(
      screen.getByRole("dialog", { name: "Agent workbench" }),
    ).toHaveFocus();
  });

  test("reacts to container-only resizes through ResizeObserver", () => {
    const resizeCallbacks: Array<() => void> = [];
    class MockResizeObserver {
      constructor(callback: ResizeObserverCallback) {
        resizeCallbacks.push(() =>
          callback([], this as unknown as ResizeObserver),
        );
      }

      observe() {}
      unobserve() {}
      disconnect() {}
    }
    Object.assign(globalThis, { ResizeObserver: MockResizeObserver });
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1440,
    });
    layoutWidth = 1300;

    renderWithProviders(
      <ChatPageLayout
        header={<div>Header</div>}
        messageList={<div>Messages</div>}
        inputArea={<div>Composer</div>}
        secondaryPanel={<div>Workbench</div>}
      />,
    );

    expect(
      screen.getByRole("complementary", { name: "Agent workbench" }),
    ).not.toHaveClass("fixed");

    layoutWidth = 900;
    act(() => resizeCallbacks.forEach((notify) => notify()));

    expect(
      screen.getByRole("dialog", { name: "Agent workbench" }),
    ).toHaveAttribute("data-secondary-panel-presentation", "desktop-drawer");
  });

  test("temporarily clamps a persisted width without overwriting it", () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1440,
    });
    window.localStorage.setItem("echo:chatSecondaryPanelWidth", "500");
    layoutWidth = 1200;

    renderWithProviders(
      <ChatPageLayout
        header={<div>Header</div>}
        messageList={<div>Messages</div>}
        inputArea={<div>Composer</div>}
        secondaryPanel={<div>Workbench</div>}
      />,
    );

    const workbench = screen.getByRole("complementary", {
      name: "Agent workbench",
    });
    expect(workbench).toHaveStyle({ width: "500px" });

    layoutWidth = 1050;
    fireEvent(window, new Event("resize"));
    expect(workbench).toHaveStyle({ width: "430px" });
    expect(window.localStorage.getItem("echo:chatSecondaryPanelWidth")).toBe(
      "500",
    );

    layoutWidth = 1200;
    fireEvent(window, new Event("resize"));
    expect(workbench).toHaveStyle({ width: "500px" });
  });

  test("clamps two inline panels to preserve a 620px conversation column", () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1440,
    });
    layoutWidth = 1300;

    renderWithProviders(
      <ChatPageLayout
        header={<div>Header</div>}
        messageList={<div>Messages</div>}
        inputArea={<div>Composer</div>}
        sidebar={<div>Utility</div>}
        showSidebar
        secondaryPanel={<div>Workbench</div>}
      />,
    );

    expect(
      screen.getByRole("complementary", {
        name: "Artifacts, plan, and research panel",
      }),
    ).toHaveStyle({ width: "300px" });
    expect(
      screen.getByRole("complementary", { name: "Agent workbench" }),
    ).toHaveStyle({ width: "380px" });
  });
});
