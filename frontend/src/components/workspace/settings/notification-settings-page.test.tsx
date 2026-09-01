import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import NotificationSettingsPage from "./notification-settings-page";

const notificationMock = vi.hoisted(() => ({
  current: {
    permission: "denied" as NotificationPermission,
    isSupported: true,
    isReady: true,
    requestPermission: vi.fn(),
    showNotification: vi.fn(() => true),
  },
}));

vi.mock("@/core/notification/hooks", () => ({
  useNotification: () => notificationMock.current,
}));

describe("NotificationSettingsPage", () => {
  beforeEach(() => {
    notificationMock.current = {
      permission: "denied",
      isSupported: true,
      isReady: true,
      requestPermission: vi.fn(),
      showNotification: vi.fn(() => true),
    };
  });

  it("names the disabled switch and explains denied desktop permission", () => {
    renderWithProviders(<NotificationSettingsPage />, { locale: "zh-CN" });

    expect(screen.getByRole("switch", { name: "启用通知" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "系统或浏览器的通知设置",
    );
    expect(screen.getByText("权限被拒绝")).toBeInTheDocument();
  });

  it("shows a stable loading state before capability detection completes", () => {
    notificationMock.current = {
      ...notificationMock.current,
      isReady: false,
    };

    renderWithProviders(<NotificationSettingsPage />, { locale: "zh-CN" });

    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });
});
