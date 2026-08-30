import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { MixSettingsSection } from "./mix-settings-section";

vi.mock("@/core/models/hooks", () => ({
  useModels: () => ({
    models: [
      {
        name: "deepseek-v4-flash",
        display_name: "deepseek-v4-flash",
        entry_id: "deepseek-default",
      },
      {
        name: "deepseek-v4-flash",
        display_name: "deepseek-v4-flash (api.b.ai)",
        entry_id: "deepseek-bai",
      },
      {
        name: "deepseek-v4-flash::1m",
        display_name: "deepseek-v4-flash · 1M",
      },
    ],
    isLoading: false,
    error: null,
  }),
}));

describe("MixSettingsSection", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ proposers: [], aggregator: "", n: 3 }),
      }),
    );
  });

  it("renders one selectable entry for duplicate routable model names", async () => {
    renderWithProviders(<MixSettingsSection />);

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "deepseek-v4-flash" }),
      ).toBeInTheDocument(),
    );

    expect(
      screen.getAllByRole("button", { name: "deepseek-v4-flash" }),
    ).toHaveLength(1);
    const aggregator = screen.getByRole("combobox");
    expect(
      within(aggregator).getAllByRole("option", {
        name: "deepseek-v4-flash",
      }),
    ).toHaveLength(1);
  });
});
