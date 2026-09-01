import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { getLocalSettings } from "@/core/settings";
import { renderWithProviders } from "@/test/harness";

import PersonalSpaceSettingsPage from "./personal-space-settings-page";

describe("PersonalSpaceSettingsPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("persists custom work rules", () => {
    renderWithProviders(<PersonalSpaceSettingsPage />, { locale: "zh-CN" });

    fireEvent.change(screen.getByRole("textbox", { name: "自定义工作规则" }), {
      target: { value: "研究时优先使用一手来源。" },
    });

    const personal = getLocalSettings().personal_space;
    expect(personal.custom_instructions).toBe("研究时优先使用一手来源。");
  });

  it("does not expose a global reply style", () => {
    renderWithProviders(<PersonalSpaceSettingsPage />, { locale: "zh-CN" });

    expect(screen.queryByText("回复风格")).not.toBeInTheDocument();
  });
});
