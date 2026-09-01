import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { AppMarketplacePanel } from "./app-marketplace-panel";

vi.mock("./capability-market-panel", () => ({
  CapabilityMarketPanel: ({
    searchQuery,
    view,
    featuredIds,
    maxItems,
    showToolbar,
  }: {
    searchQuery: string;
    view: string;
    featuredIds: readonly string[];
    maxItems?: number;
    showToolbar: boolean;
  }) => (
    <div
      data-testid="capability-market"
      data-search={searchQuery}
      data-view={view}
      data-featured={featuredIds.join(",")}
      data-max-items={maxItems}
      data-show-toolbar={String(showToolbar)}
    />
  ),
}));

vi.mock("./unified-assets-panel", () => ({
  UnifiedAssetsPanel: ({
    searchQuery,
    allowedKinds,
    showSyncAction,
  }: {
    searchQuery: string;
    allowedKinds: readonly string[];
    showSyncAction: boolean;
  }) => (
    <div
      data-testid="asset-library"
      data-search={searchQuery}
      data-kinds={allowedKinds.join(",")}
      data-show-sync={String(showSyncAction)}
    />
  ),
}));

describe("AppMarketplacePanel", () => {
  it("在精选、全部应用和我的库之间切换", async () => {
    renderWithProviders(<AppMarketplacePanel searchQuery="浏览器" />);

    const market = screen.getByTestId("capability-market");
    expect(market).toHaveAttribute("data-view", "featured");
    expect(market).toHaveAttribute("data-search", "浏览器");
    expect(market).toHaveAttribute("data-max-items", "7");
    expect(market).toHaveAttribute("data-show-toolbar", "false");

    await userEvent.click(screen.getByRole("tab", { name: "全部应用" }));
    expect(screen.getByTestId("capability-market")).toHaveAttribute(
      "data-view",
      "all",
    );

    await userEvent.click(screen.getByRole("tab", { name: "我的库" }));
    const library = screen.getByTestId("asset-library");
    expect(library).toHaveAttribute("data-kinds", "plugin,skill");
    expect(library).toHaveAttribute("data-show-sync", "false");
    expect(library).toHaveAttribute("data-search", "浏览器");

    expect(
      screen.queryByText(/WorkBuddy|Cloud|Registry|角色|专家团/),
    ).toBeNull();
  });

  it("支持由上层控制分区", async () => {
    const onViewChange = vi.fn();
    renderWithProviders(
      <AppMarketplacePanel view="all" onViewChange={onViewChange} />,
    );

    await userEvent.click(screen.getByRole("tab", { name: "我的库" }));

    expect(onViewChange).toHaveBeenCalledWith("library");
    expect(screen.getByTestId("capability-market")).toHaveAttribute(
      "data-view",
      "all",
    );
    expect(screen.queryByTestId("asset-library")).not.toBeInTheDocument();
  });
});
