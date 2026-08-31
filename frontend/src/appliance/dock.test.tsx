import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Dock, DockItem } from "./dock";

describe("Dock", () => {
  it("keeps icon geometry untouched during pointer movement", () => {
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
      inner.style.width = "62px";
    });
    // Re-run the layout measurement after assigning deterministic jsdom sizes.
    rerender(renderDock());
    const restingGlassWidth = nav.style.getPropertyValue("--dock-glass-width");

    fireEvent.pointerMove(nav, { clientX: 27 });

    expect(items[0]?.style.getPropertyValue("--dock-s")).toBe("");
    expect(items[1]?.style.getPropertyValue("--dock-s")).toBe("");
    expect(inners[0]?.style.getPropertyValue("--dock-lift")).toBe("");
    expect(nav.style.getPropertyValue("--dock-glass-width")).toBe(
      restingGlassWidth,
    );
    expect(restingGlassWidth).toBe("145px");
  });

  it("keeps icon geometry untouched while pressing", () => {
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
    expect(item).not.toHaveAttribute("data-dock-pressed");
    expect(item.querySelector(".dock-item-spring")).not.toBeNull();

    fireEvent.pointerUp(item);
    expect(item).not.toHaveAttribute("data-dock-pressed");
  });
});
