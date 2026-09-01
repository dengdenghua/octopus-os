import { screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { GeneLockControlCard } from "./gene-lock-badge";

const fetchMock = vi.fn();

describe("GeneLockControlCard", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue({
      json: async () => ({
        schema_version: 1,
        maturity_level: 2,
        maturity_level_name: "growing",
        panic: { active: false, since: null, reason: "" },
        mode: "dev",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("exposes the selected governance mode and maturity level", async () => {
    renderWithProviders(<GeneLockControlCard compact />, { locale: "zh-CN" });

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "宽松" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
    expect(screen.getByRole("button", { name: "严格" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByRole("button", { name: "Lv2" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "紧急锁定" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });
});
