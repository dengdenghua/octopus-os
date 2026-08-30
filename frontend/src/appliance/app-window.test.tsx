import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppWindow } from "./app-window";

describe("Echo OS desktop application window", () => {
  it("renders a system application directly without creating an iframe", () => {
    const { container } = render(
      <AppWindow
        win={{
          id: "embedded-agent",
          title: "工作台",
          url: "/workspace/realtime/new",
          content: <div data-testid="native-agent">内建 Agent</div>,
          integratedChrome: true,
        }}
        index={0}
        focused
        onFocus={vi.fn()}
        onClose={vi.fn()}
        onMinimize={vi.fn()}
      />,
    );

    expect(screen.getByTestId("native-agent")).toHaveTextContent("内建 Agent");
    expect(container.querySelector("iframe")).toBeNull();
  });

  it("keeps one OS-owned control row without adding a second title bar", () => {
    const { container } = render(
      <AppWindow
        win={{
          id: "agent",
          title: "工作台",
          url: "https://embedded-app.example.test/",
          integratedChrome: true,
        }}
        index={0}
        focused
        onFocus={vi.fn()}
        onClose={vi.fn()}
        onMinimize={vi.fn()}
      />,
    );

    expect(container.querySelector(".mac-window-titlebar")).toBeNull();
    expect(container.querySelector(".mac-window-compact-titlebar")).toBeNull();
    expect(
      container.querySelector(".mac-window-integrated-controls"),
    ).not.toBeNull();
    expect(container.querySelectorAll(".mac-traffic-light")).toHaveLength(3);
    expect(container.querySelector(".is-integrated-chrome")).not.toBeNull();
    expect(screen.getByTitle("工作台")).toBeInTheDocument();
  });

  it("closes an integrated window from the always-present OS control", () => {
    const onClose = vi.fn();
    render(
      <AppWindow
        win={{
          id: "agent",
          title: "工作台",
          url: "https://embedded-app.example.test/",
          integratedChrome: true,
        }}
        index={0}
        focused
        onFocus={vi.fn()}
        onClose={onClose}
        onMinimize={vi.fn()}
      />,
    );

    screen.getByRole("button", { name: "关闭工作台" }).click();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("accepts window controls only from its own integrated iframe", () => {
    const onClose = vi.fn();
    const onMinimize = vi.fn();
    render(
      <AppWindow
        win={{
          id: "agent",
          title: "工作台",
          url: "https://embedded-app.example.test/",
          integratedChrome: true,
        }}
        index={0}
        focused
        onFocus={vi.fn()}
        onClose={onClose}
        onMinimize={onMinimize}
      />,
    );

    const iframe = screen.getByTitle<HTMLIFrameElement>("工作台");
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: iframe.contentWindow,
          data: { type: "echo-os:window-control", action: "minimize" },
        }),
      );
      window.dispatchEvent(
        new MessageEvent("message", {
          source: window,
          data: { type: "echo-os:window-control", action: "close" },
        }),
      );
    });

    expect(onMinimize).toHaveBeenCalledOnce();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("throttles dragging to display frames and settles its optical tilt", () => {
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(16);
      return 1;
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    const { container } = render(
      <AppWindow
        win={{
          id: "agent",
          title: "工作台",
          url: "https://embedded-app.example.test/",
          integratedChrome: true,
        }}
        index={0}
        focused
        onFocus={vi.fn()}
        onClose={vi.fn()}
        onMinimize={vi.fn()}
      />,
    );
    const win = container.querySelector<HTMLElement>(".mac-window")!;
    const controls = container.querySelector<HTMLElement>(
      ".mac-window-integrated-controls",
    )!;

    fireEvent.pointerDown(controls, { clientX: 100, clientY: 60 });
    expect(win).toHaveAttribute("data-window-dragging", "move");

    fireEvent.pointerMove(window, { clientX: 150, clientY: 74 });
    expect(win.style.getPropertyValue("--window-tilt-y")).not.toBe("0deg");

    fireEvent.pointerUp(window);
    expect(win).not.toHaveAttribute("data-window-dragging");
    expect(win.style.getPropertyValue("--window-tilt-x")).toBe("0deg");
    expect(win.style.getPropertyValue("--window-tilt-y")).toBe("0deg");
    vi.unstubAllGlobals();
  });
});
