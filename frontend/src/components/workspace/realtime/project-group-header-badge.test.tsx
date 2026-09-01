import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { ProjectGroupHeaderBadge } from "./project-group-header-badge";

describe("ProjectGroupHeaderBadge", () => {
  it("adds a lightweight capability marker without replacing group identity", () => {
    const onOpenWorkbench = vi.fn();
    renderWithProviders(
      <ProjectGroupHeaderBadge
        name="品牌发布"
        status="running"
        onOpenWorkbench={onOpenWorkbench}
      />,
      { locale: "zh-CN" },
    );

    expect(screen.getByTestId("project-capability-badge")).toHaveTextContent(
      "项目已开启·进行中",
    );
    expect(screen.queryByText("品牌发布")).toBeNull();
    expect(screen.queryByText(/位成员/)).toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: "打开项目工作台：品牌发布" }),
    );
    expect(onOpenWorkbench).toHaveBeenCalledTimes(1);
  });

  it("shows detach only to owners and delegates the destructive action", async () => {
    const onDetach = vi.fn();
    const owner = renderWithProviders(
      <ProjectGroupHeaderBadge
        name="品牌发布"
        status="done"
        onOpenWorkbench={vi.fn()}
        canDetach
        onDetach={onDetach}
      />,
      { locale: "zh-CN" },
    );

    const trigger = screen.getByRole("button", {
      name: "项目操作：品牌发布",
    });
    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
    fireEvent.click(trigger);
    const menu = await screen.findByRole("menu");
    fireEvent.click(within(menu).getByText("关闭项目能力"));
    expect(onDetach).toHaveBeenCalledTimes(1);
    owner.unmount();

    renderWithProviders(
      <ProjectGroupHeaderBadge
        name="只读成员群"
        status="running"
        onOpenWorkbench={vi.fn()}
      />,
      { locale: "zh-CN" },
    );
    expect(
      screen.queryByRole("button", { name: "项目操作：只读成员群" }),
    ).toBeNull();
  });
});
