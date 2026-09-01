import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {},
    locale: "zh",
    setLocale: () => Promise.resolve(),
  }),
}));

import { PersonalModeSelector } from "./personal-mode-selector";

describe("PersonalModeSelector", () => {
  it("shows the active mode and fires onModeChange on pick", async () => {
    const onModeChange = vi.fn();
    const user = userEvent.setup();
    render(<PersonalModeSelector mode="general" onModeChange={onModeChange} />);

    // The trigger reflects the active mode (localized label).
    await user.click(screen.getByRole("button", { name: /通用/ }));
    // Picking another option reports it up.
    await user.click(await screen.findByText("构建"));
    expect(onModeChange).toHaveBeenCalledWith("build");
  });

  it("offers all three personal-space modes", async () => {
    const user = userEvent.setup();
    render(<PersonalModeSelector mode="general" onModeChange={() => {}} />);

    await user.click(screen.getByRole("button", { name: /通用/ }));
    expect(await screen.findByText("研究")).toBeInTheDocument();
    expect(screen.getByText("构建")).toBeInTheDocument();
  });
});
