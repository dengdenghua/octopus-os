import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { reflexFetch } from "./api";
import { ReflexMonitorContent } from "./page";

vi.mock("./api", () => ({
  reflexFetch: vi.fn(),
}));

vi.mock("./gepa-panel", () => ({
  GepaPanel: () => <div>recipe-forge</div>,
}));

vi.mock("./variant-performance-panel", () => ({
  VariantPerformancePanel: () => null,
}));

vi.mock("@/components/workspace/gene-lock-badge", () => ({
  GeneLockBadge: () => null,
}));

describe("ReflexMonitorContent", () => {
  beforeEach(() => {
    vi.mocked(reflexFetch).mockRejectedValue(new TypeError("Failed to fetch"));
  });

  it("distinguishes unavailable monitoring data from a healthy zero state", async () => {
    renderWithProviders(<ReflexMonitorContent />, {
      locale: "zh-CN",
      initialRoute: "/workspace/reflex",
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "反射数据暂时无法加载。",
    );
    expect(screen.getAllByText("—")).toHaveLength(6);
    expect(screen.getByText("反射趋势暂时无法加载。")).toBeInTheDocument();
    expect(screen.getByText("规则数据暂时无法加载。")).toBeInTheDocument();
    expect(screen.queryByText(/Failed to fetch/)).not.toBeInTheDocument();
    expect(
      screen.queryByText("最近 60 分钟无反射命中"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("未加载任何规则。")).not.toBeInTheDocument();
  });
});
