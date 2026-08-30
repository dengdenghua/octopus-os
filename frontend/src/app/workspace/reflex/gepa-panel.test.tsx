import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { reflexFetch } from "./api";
import { GepaPanel } from "./gepa-panel";

vi.mock("./api", () => ({
  reflexFetch: vi.fn(),
}));

describe("GepaPanel", () => {
  beforeEach(() => {
    vi.mocked(reflexFetch).mockRejectedValue(new TypeError("Failed to fetch"));
  });

  it("does not present unavailable addendum and canary state as empty", async () => {
    renderWithProviders(<GepaPanel />, { locale: "zh-CN" });

    expect(await screen.findByText("状态不可用")).toBeInTheDocument();
    expect(
      screen.getByText("附录状态暂时无法加载；现有设置没有改变。"),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText("Canary 状态暂时无法加载；现有数据没有改变。"),
    ).toHaveLength(2);
    expect(screen.getByText("活跃 — · 已回滚 — · 总计 —")).toBeInTheDocument();
    expect(screen.queryByText(/Failed to fetch/)).not.toBeInTheDocument();
    expect(screen.queryByText("暂无 canary 状态")).not.toBeInTheDocument();
    expect(screen.queryByText("无")).not.toBeInTheDocument();
  });
});
