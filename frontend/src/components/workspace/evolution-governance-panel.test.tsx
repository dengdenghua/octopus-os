import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { getDualHelixShadowStatus } from "@/core/evolution/api";
import { renderWithProviders } from "@/test/harness";

import { EvolutionGovernancePanel } from "./evolution-governance-panel";

vi.mock("@/core/evolution/api", () => ({
  getDualHelixShadowStatus: vi.fn(async () => ({
    ok: true,
    enabled: false,
    isolation: "bounded_snapshot_read_only",
    runs: [],
  })),
  setDualHelixShadowEnabled: vi.fn(async (enabled: boolean) => ({
    ok: true,
    enabled,
    isolation: "bounded_snapshot_read_only",
    runs: [],
  })),
}));

describe("EvolutionGovernancePanel", () => {
  it("distinguishes an unavailable protection status from the disabled state", async () => {
    const user = userEvent.setup();
    vi.mocked(getDualHelixShadowStatus).mockRejectedValueOnce(
      new TypeError("Failed to fetch"),
    );

    renderWithProviders(<EvolutionGovernancePanel />, { locale: "zh-CN" });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "保护状态暂时无法加载；现有设置没有改变。",
    );
    expect(screen.getByRole("button", { name: "状态不可用" })).toBeDisabled();
    expect(screen.queryByText(/当前关闭/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Failed to fetch/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(
      await screen.findByText(/当前关闭，不会触发另一引擎/),
    ).toBeInTheDocument();
  });
});
