import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Dock, DockItem } from "./dock";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Dock", () => {
  it("keeps the resting glass width while icon magnification changes", () => {
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());

    const renderDock = () => (
      <Dock className="mac-dock">
        <DockItem title="文件">文件</DockItem>
        <DockItem title="应用库">应用库</DockItem>
      </Dock>
    );
    const { rerender } = render(renderDock());
    const nav = screen.getByRole("navigation");
    const items = screen.getAllByRole("button");
    const inners = nav.querySelectorAll<HTMLElement>(".dock-item-inner");
    const lens = nav.querySelector<HTMLElement>(".mac-dock-lens");

    expect(lens).toHaveAttribute("aria-hidden", "true");
    expect(lens?.querySelector("img")).toHaveAttribute(
      "src",
      "/third-party/appletechie-macos/wallpaper-day2.jpg",
    );

    nav.style.columnGap = "5px";
    nav.style.paddingLeft = "7px";
    nav.style.paddingRight = "7px";
    nav.style.borderLeftWidth = "1px";
    nav.style.borderRightWidth = "1px";
    inners.forEach((inner) => {
      inner.style.width = "54px";
    });
    items.forEach((item, index) => {
      vi.spyOn(item, "getBoundingClientRect").mockReturnValue({
        x: index * 59,
        y: 0,
        width: 54,
        height: 54,
        top: 0,
        right: index * 59 + 54,
        bottom: 54,
        left: index * 59,
        toJSON: () => ({}),
      });
    });

    // Re-run the layout measurement after assigning deterministic jsdom sizes.
    rerender(renderDock());
    const restingGlassWidth = nav.style.getPropertyValue("--dock-glass-width");

    act(() => {
      fireEvent.pointerMove(nav, { clientX: 27 });
    });

    expect(
      Number(items[0]?.style.getPropertyValue("--dock-s")),
    ).toBeGreaterThan(1);
    expect(
      Number(items[1]?.style.getPropertyValue("--dock-s")),
    ).toBeGreaterThan(1);
    expect(Number(items[1]?.style.getPropertyValue("--dock-s"))).toBeLessThan(
      Number(items[0]?.style.getPropertyValue("--dock-s")),
    );
    expect(
      Number(
        inners[0]?.style.getPropertyValue("--dock-lift").replace("px", ""),
      ),
    ).toBeLessThan(0);
    expect(nav.style.getPropertyValue("--dock-glass-width")).toBe(
      restingGlassWidth,
    );
    expect(restingGlassWidth).toBe("129px");
  });

  it("uses a one-shot press spring instead of a permanent Dock animation", () => {
    render(
      <Dock className="mac-dock">
        <DockItem title="文件" running>
          文件
        </DockItem>
      </Dock>,
    );
    const item = screen.getByRole("button", { name: "文件" });
    expect(item).toHaveAttribute("data-liquid-icon");
    expect(item.querySelector(".mac-dock-running-dot")).toBeInTheDocument();
    expect(
      item.querySelector(".dock-item-inner .mac-dock-running-dot"),
    ).toBeNull();

    fireEvent.pointerDown(item);
    expect(item).toHaveAttribute("data-dock-pressed", "true");
    expect(item.querySelector(".dock-item-spring")).not.toBeNull();

    fireEvent.pointerUp(item);
    expect(item).not.toHaveAttribute("data-dock-pressed");
  });
});
