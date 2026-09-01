import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { getLocalSettings } from "@/core/settings";
import { renderWithProviders } from "@/test/harness";

import { AssistantSettingsMenu } from "./assistant-settings-menu";

describe("AssistantSettingsMenu", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("keeps the assistant-only idle threshold hidden until enabled", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AssistantSettingsMenu />, { locale: "zh-CN" });

    await user.click(screen.getByRole("button", { name: "助手设置" }));

    const threshold = screen.getByRole("spinbutton", {
      name: "助手新会话空闲时长",
      hidden: true,
    });
    expect(threshold).toBeDisabled();

    await user.click(screen.getByRole("switch", { name: "自动开启新会话" }));

    expect(threshold).toBeEnabled();
    expect(threshold).toHaveValue(6);
    await waitFor(() =>
      expect(getLocalSettings().session.auto_new_session_hours).toBe(6),
    );
  });

  it("clamps the idle threshold to the supported range", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AssistantSettingsMenu />, { locale: "zh-CN" });

    await user.click(screen.getByRole("button", { name: "助手设置" }));
    await user.click(screen.getByRole("switch", { name: "自动开启新会话" }));

    const threshold = screen.getByRole("spinbutton", {
      name: "助手新会话空闲时长",
    });
    await user.clear(threshold);
    await user.type(threshold, "9999");

    expect(threshold).toHaveValue(720);
  });
});
