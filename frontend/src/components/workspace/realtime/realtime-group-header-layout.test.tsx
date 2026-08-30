import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { RealtimeGroupHeaderLayout } from "./realtime-group-header-layout";

describe("RealtimeGroupHeaderLayout", () => {
  it("keeps group identity and collaboration controls at primary priority", () => {
    renderWithProviders(
      <RealtimeGroupHeaderLayout
        agentIdentity={<span>AI 头像</span>}
        title={<span>发布讨论群</span>}
        projectStatus={<button>进行中</button>}
        runStatus={<span>运行 12 秒</span>}
        members={<button>AI 成员 6</button>}
        invite={<button>邀请真人</button>}
        workbench={<button>工作台</button>}
        secondaryActions={
          <>
            <button>REC</button>
            <button>分享</button>
          </>
        }
      />,
    );

    const header = screen.getByTestId("realtime-group-header");
    expect(header).toHaveClass(
      "realtime-group-header",
      "@container/realtime-header",
      "min-w-0",
      "flex-1",
    );
    expect(header).toHaveAttribute("data-header-layout", "realtime");

    for (const label of [
      "发布讨论群",
      "进行中",
      "AI 成员 6",
      "邀请真人",
      "工作台",
    ]) {
      expect(
        within(header).getByText(label).closest("[data-header-priority]"),
      ).toHaveAttribute("data-header-priority", "primary");
    }
  });

  it("marks only supporting utilities for narrow-container collapse", () => {
    renderWithProviders(
      <RealtimeGroupHeaderLayout
        agentIdentity={<span>协作头像</span>}
        title={<span>项目群</span>}
        runStatus={<span>运行时长</span>}
        members={<button>成员</button>}
        invite={<button>邀请</button>}
        workbench={<button>工作台</button>}
        secondaryActions={<button>REC 与分享</button>}
      />,
    );

    const header = screen.getByTestId("realtime-group-header");
    expect(within(header).getByText("成员")).toBeVisible();
    expect(within(header).getByText("邀请")).toBeVisible();
    expect(within(header).getByText("工作台")).toBeVisible();
    expect(within(header).getByText("运行时长").parentElement).toHaveClass(
      "realtime-group-header__run",
      "@max-[880px]/realtime-header:hidden",
    );
    expect(within(header).getByText("REC 与分享").parentElement).toHaveClass(
      "realtime-group-header__secondary",
      "@max-[880px]/realtime-header:hidden",
    );
    expect(within(header).getByText("协作头像").parentElement).toHaveClass(
      "realtime-group-header__agent",
      "@max-[720px]/realtime-header:hidden",
    );
    expect(within(header).getByText("工作台").parentElement).not.toHaveClass(
      "@max-[880px]/realtime-header:hidden",
    );
  });
});
