import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import AccountSettingsPage from "./account-settings-page";

const accountMocks = vi.hoisted(() => ({
  updateProfile: vi.fn(),
  uploadAvatar: vi.fn(),
  updatePrivacy: vi.fn(),
  unlinkAccount: vi.fn(),
  refetchProfile: vi.fn(),
  refetchPrivacy: vi.fn(),
  privacy: undefined as undefined | Record<string, boolean>,
}));

vi.mock("@/providers/AuthProvider", () => ({
  useAuth: () => ({
    user: { user_id: "123", username: "123" },
  }),
}));

vi.mock("@/core/account", () => ({
  useProfile: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
    refetch: accountMocks.refetchProfile,
  }),
  usePrivacySettings: () => ({
    data: accountMocks.privacy,
    isLoading: false,
    isError: false,
    refetch: accountMocks.refetchPrivacy,
  }),
  useUpdateProfile: () => ({
    mutate: accountMocks.updateProfile,
    isPending: false,
  }),
  useUploadAvatar: () => ({
    mutate: accountMocks.uploadAvatar,
    isPending: false,
  }),
  useUpdatePrivacySettings: () => ({
    mutate: accountMocks.updatePrivacy,
    isPending: false,
  }),
  useUnlinkAccount: () => ({
    mutate: accountMocks.unlinkAccount,
    isPending: false,
  }),
}));

vi.mock("@/core/oct/hooks", () => ({
  useOctLink: () => ({ data: null, isLoading: false }),
  useRefreshOctCredits: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

describe("AccountSettingsPage", () => {
  beforeEach(() => {
    accountMocks.privacy = undefined;
    vi.clearAllMocks();
  });

  it("uses the authenticated identity instead of empty profile placeholders", () => {
    renderWithProviders(<AccountSettingsPage />, { locale: "zh-CN" });

    expect(screen.getByText("123")).toBeInTheDocument();
    expect(screen.queryByText("User")).not.toBeInTheDocument();
    expect(screen.queryByText("@")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存更改" })).toBeDisabled();
  });

  it("enables save only for a real change and trims the submitted profile", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AccountSettingsPage />, { locale: "zh-CN" });

    const name = screen.getByLabelText("显示名称");
    await user.type(name, "  Alice  ");
    const save = screen.getByRole("button", { name: "保存更改" });
    expect(save).toBeEnabled();
    await user.click(save);

    expect(accountMocks.updateProfile).toHaveBeenCalledWith({
      display_name: "Alice",
      bio: undefined,
    });
  });

  it("does not render unavailable account-link actions as dead buttons", () => {
    renderWithProviders(<AccountSettingsPage />, { locale: "zh-CN" });

    expect(
      screen.queryByRole("button", { name: "关联 Google" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/第三方账号关联暂未开放/)).toBeInTheDocument();
  });

  it("persists usage sharing through the matching privacy field", async () => {
    const user = userEvent.setup();
    accountMocks.privacy = {
      privacy_mode: true,
      data_collection_consent: false,
      share_usage_analytics: true,
      allow_community_features: false,
      public_profile: false,
    };
    renderWithProviders(<AccountSettingsPage />, { locale: "zh-CN" });

    const sharing = screen.getByRole("switch", { name: "分享使用数据" });
    expect(sharing).toBeChecked();
    await user.click(sharing);
    expect(accountMocks.updatePrivacy).toHaveBeenCalledWith({
      share_usage_analytics: false,
    });
  });
});
