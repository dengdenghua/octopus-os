/**
 * Smoke tests for the new sections on the privacy settings page:
 *   • AI mode (efficiency / privacy)
 *   • Path denylist (add / remove)
 *
 * The existing identity-lock toggle is exercised indirectly via the
 * mock fetch sequence — these tests focus on the two new sections.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import { renderWithProviders } from "@/test/harness";

import PrivacySettingsPage from "./privacy-settings-page";

const fetchMock = vi.fn();

const IDENTITY_LOCK_RESPONSE = {
  locked: true,
  source: "default",
};

const CONSTITUTION_PROFILE_RESPONSE = {
  profile: "normal",
  available: ["strict", "normal", "lax"],
};

const JUDGE_RESPONSE = {
  enabled: false,
  available: true,
};

const AI_MODE_RESPONSE = {
  mode: "efficiency",
  recommended: "efficiency",
  device: {
    has_local_model: true,
    has_gpu: true,
    ram_gb: 16,
    cpu_count: 8,
    cloud_reachable: true,
    notes: [],
  },
  modes: [
    {
      id: "efficiency",
      label: "效率模式",
      description: "云端高性能模型",
      recommended_default: true,
    },
    {
      id: "privacy",
      label: "隐私模式",
      description: "本地模型，数据不离开本机",
    },
  ],
};

const DENYLIST_RESPONSE = {
  paths: ["C:/Users/me/secrets", "/home/me/.ssh"],
};

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

/**
 * Wire fetch to dispatch on URL substring. The page fires multiple
 * GETs on mount; we register a tiny router so tests don't have to
 * babysit the call order.
 */
function installFetchRouter(
  routes: Record<string, (init?: RequestInit) => unknown>,
) {
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    for (const [substr, handler] of Object.entries(routes)) {
      if (typeof url === "string" && url.includes(substr)) {
        const method = init?.method ?? "GET";
        const body = handler(init);
        // Allow handlers to return a status-tagged tuple.
        if (
          Array.isArray(body) &&
          body.length === 2 &&
          typeof body[1] === "number"
        ) {
          return Promise.resolve(jsonResponse(body[0], body[1] as number));
        }
        // Ignore method for now — tests assert via fetchMock.mock.calls.
        void method;
        return Promise.resolve(jsonResponse(body));
      }
    }
    return Promise.resolve(jsonResponse({}, 404));
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("PrivacySettingsPage · AI mode section", () => {
  it("renders both efficiency and privacy mode cards", async () => {
    installFetchRouter({
      "/api/config/identity-lock": () => IDENTITY_LOCK_RESPONSE,
      "/api/safety/constitution-profile": () => CONSTITUTION_PROFILE_RESPONSE,
      "/api/safety/llm-judge": () => JUDGE_RESPONSE,
      "/api/ai-mode": () => AI_MODE_RESPONSE,
      "/api/path-denylist": () => DENYLIST_RESPONSE,
    });

    renderWithProviders(<PrivacySettingsPage />, { locale: "zh-CN" });

    await waitFor(() => {
      expect(screen.getByText("效率模式")).toBeInTheDocument();
      expect(screen.getByText("隐私模式")).toBeInTheDocument();
    });
    // Recommendation summary line uses the recommended label.
    expect(
      screen.getByText(/根据本机设备配置.*推荐使用.*效率模式/),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看规则说明" })).toHaveAttribute(
      "href",
      "https://github.com/dengdenghua/echo-os/blob/main/docs/constitution.md",
    );
    expect(screen.getByRole("link", { name: "查看规则说明" })).toHaveAttribute(
      "target",
      "_blank",
    );
    expect(
      screen.getByRole("button", { name: "关闭产品身份保护" }),
    ).toBePressed();
    expect(screen.getByText("当前策略：使用系统默认")).toBeInTheDocument();
    expect(
      screen.getByRole("switch", { name: "启用智能语义审查" }),
    ).toBeEnabled();
  });

  it("clicking 隐私模式 sends POST /api/ai-mode with mode=privacy", async () => {
    installFetchRouter({
      "/api/config/identity-lock": () => IDENTITY_LOCK_RESPONSE,
      "/api/safety/constitution-profile": () => CONSTITUTION_PROFILE_RESPONSE,
      "/api/safety/llm-judge": () => JUDGE_RESPONSE,
      "/api/ai-mode": (init) =>
        init?.method === "POST"
          ? { mode: "privacy", ok: true }
          : AI_MODE_RESPONSE,
      "/api/path-denylist": () => DENYLIST_RESPONSE,
    });

    renderWithProviders(<PrivacySettingsPage />, { locale: "zh-CN" });

    const privacyCard = await screen.findByRole("button", {
      pressed: false,
      name: /隐私模式/,
    });
    fireEvent.click(privacyCard);

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        ([url, init]: [string, RequestInit | undefined]) =>
          typeof url === "string" &&
          url.includes("/api/ai-mode") &&
          init?.method === "POST",
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse(String(postCall![1]?.body));
      expect(body).toEqual({ mode: "privacy" });
    });
    expect(await screen.findByText(/16 GB RAM/)).toBeInTheDocument();
    expect(screen.getByText(/本地模型可用/)).toBeInTheDocument();
  });

  it("uses the selected locale instead of backend-provided Chinese labels", async () => {
    installFetchRouter({
      "/api/config/identity-lock": () => IDENTITY_LOCK_RESPONSE,
      "/api/safety/constitution-profile": () => CONSTITUTION_PROFILE_RESPONSE,
      "/api/safety/llm-judge": () => JUDGE_RESPONSE,
      "/api/ai-mode": () => AI_MODE_RESPONSE,
      "/api/path-denylist": () => DENYLIST_RESPONSE,
    });

    renderWithProviders(<PrivacySettingsPage />, { locale: "en-US" });

    expect(
      await screen.findByRole("button", { name: /Efficiency/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Privacy/ })).toBeInTheDocument();
    expect(screen.queryByText("效率模式")).not.toBeInTheDocument();
    expect(screen.queryByText("隐私模式")).not.toBeInTheDocument();
  });
});

describe("PrivacySettingsPage · path denylist section", () => {
  it("renders the existing paths returned by GET /api/path-denylist", async () => {
    installFetchRouter({
      "/api/config/identity-lock": () => IDENTITY_LOCK_RESPONSE,
      "/api/safety/constitution-profile": () => CONSTITUTION_PROFILE_RESPONSE,
      "/api/safety/llm-judge": () => JUDGE_RESPONSE,
      "/api/ai-mode": () => AI_MODE_RESPONSE,
      "/api/path-denylist": () => DENYLIST_RESPONSE,
    });

    renderWithProviders(<PrivacySettingsPage />, { locale: "zh-CN" });

    await waitFor(() => {
      expect(screen.getByText("C:/Users/me/secrets")).toBeInTheDocument();
      expect(screen.getByText("/home/me/.ssh")).toBeInTheDocument();
    });
  });

  it("asks for confirmation before removing a protected path", async () => {
    let denylistPaths = [...DENYLIST_RESPONSE.paths];
    installFetchRouter({
      "/api/config/identity-lock": () => IDENTITY_LOCK_RESPONSE,
      "/api/safety/constitution-profile": () => CONSTITUTION_PROFILE_RESPONSE,
      "/api/safety/llm-judge": () => JUDGE_RESPONSE,
      "/api/ai-mode": () => AI_MODE_RESPONSE,
      "/api/path-denylist": (init) => {
        if (init?.method === "DELETE") {
          denylistPaths = denylistPaths.filter(
            (path) => path !== "C:/Users/me/secrets",
          );
          return { ok: true };
        }
        return { paths: denylistPaths };
      },
    });

    renderWithProviders(<PrivacySettingsPage />, { locale: "zh-CN" });
    await screen.findByText("C:/Users/me/secrets");

    fireEvent.click(
      screen.getByRole("button", {
        name: "删除保护路径: C:/Users/me/secrets",
      }),
    );

    const dialog = await screen.findByRole("dialog", {
      name: "删除保护路径",
    });
    expect(
      within(dialog).getByText(
        "移除“C:/Users/me/secrets”后，Agent 将不再自动拒绝访问该路径。",
      ),
    ).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(
        ([url, init]: [string, RequestInit | undefined]) =>
          url.includes("/api/path-denylist") && init?.method === "DELETE",
      ),
    ).toBe(false);

    fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));

    await waitFor(() => {
      const deleteCall = fetchMock.mock.calls.find(
        ([url, init]: [string, RequestInit | undefined]) =>
          url.includes("/api/path-denylist") && init?.method === "DELETE",
      );
      expect(deleteCall).toBeTruthy();
      expect(JSON.parse(String(deleteCall![1]?.body))).toEqual({
        path: "C:/Users/me/secrets",
      });
      expect(screen.queryByText("C:/Users/me/secrets")).not.toBeInTheDocument();
    });
  });

  it("opens the add-path dialog and POSTs the new path then refreshes", async () => {
    let denylistPaths = [...DENYLIST_RESPONSE.paths];
    installFetchRouter({
      "/api/config/identity-lock": () => IDENTITY_LOCK_RESPONSE,
      "/api/safety/constitution-profile": () => CONSTITUTION_PROFILE_RESPONSE,
      "/api/safety/llm-judge": () => JUDGE_RESPONSE,
      "/api/ai-mode": () => AI_MODE_RESPONSE,
      "/api/path-denylist": () => ({ paths: denylistPaths }),
    });

    renderWithProviders(<PrivacySettingsPage />, { locale: "zh-CN" });

    await waitFor(() =>
      expect(screen.getByText("C:/Users/me/secrets")).toBeInTheDocument(),
    );

    // Click "新增" to open the dialog.
    fireEvent.click(
      screen.getByRole("button", { name: "添加不可读取文件夹" }),
    );

    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText(/路径/);

    // Pre-load the post-add server state so the refetch returns the
    // new path. The handler reads denylistPaths at call time.
    fireEvent.change(input, { target: { value: "/tmp/new-secret" } });
    denylistPaths = [...DENYLIST_RESPONSE.paths, "/tmp/new-secret"];

    fireEvent.click(within(dialog).getByRole("button", { name: /确认/ }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        ([url, init]: [string, RequestInit | undefined]) =>
          typeof url === "string" &&
          url.includes("/api/path-denylist") &&
          init?.method === "POST",
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse(String(postCall![1]?.body));
      expect(body).toEqual({ path: "/tmp/new-secret" });
    });

    await waitFor(() =>
      expect(screen.getByText("/tmp/new-secret")).toBeInTheDocument(),
    );
  });

  it("rejects relative paths that would resolve against the server directory", async () => {
    installFetchRouter({
      "/api/config/identity-lock": () => IDENTITY_LOCK_RESPONSE,
      "/api/safety/constitution-profile": () => CONSTITUTION_PROFILE_RESPONSE,
      "/api/safety/llm-judge": () => JUDGE_RESPONSE,
      "/api/ai-mode": () => AI_MODE_RESPONSE,
      "/api/path-denylist": () => DENYLIST_RESPONSE,
    });

    renderWithProviders(<PrivacySettingsPage />, { locale: "zh-CN" });
    await screen.findByText("C:/Users/me/secrets");
    fireEvent.click(
      screen.getByRole("button", { name: "添加不可读取文件夹" }),
    );

    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText(/路径/);
    fireEvent.change(input, { target: { value: "relative/secrets" } });

    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(within(dialog).getByRole("button", { name: /确认/ })).toBeDisabled();
  });
});

describe("PrivacySettingsPage · factory reset", () => {
  it("keeps browser state and the dialog open when the backend reports an incomplete reset", async () => {
    installFetchRouter({
      "/api/config/identity-lock": () => IDENTITY_LOCK_RESPONSE,
      "/api/safety/constitution-profile": () => CONSTITUTION_PROFILE_RESPONSE,
      "/api/safety/llm-judge": () => JUDGE_RESPONSE,
      "/api/ai-mode": () => AI_MODE_RESPONSE,
      "/api/path-denylist": () => DENYLIST_RESPONSE,
      "/api/system/factory-reset": () => ({
        ok: false,
        errors: ["data directory busy"],
      }),
    });
    window.localStorage.setItem("echo:test-state", "preserve-me");

    renderWithProviders(<PrivacySettingsPage />, { locale: "zh-CN" });
    await screen.findByText("效率模式");
    fireEvent.click(screen.getAllByRole("button", { name: "恢复出厂设置" })[0]);

    const dialog = await screen.findByRole("dialog", {
      name: "恢复出厂设置",
    });
    fireEvent.change(within(dialog).getByLabelText(/RESET ECHO/), {
      target: { value: "RESET ECHO" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "确认恢复" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/system/factory-reset"),
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(screen.getByRole("dialog", { name: "恢复出厂设置" })).toBeVisible();
    expect(window.localStorage.getItem("echo:test-state")).toBe(
      "preserve-me",
    );
    window.localStorage.removeItem("echo:test-state");
  });
});

describe("PrivacySettingsPage · recoverable loading states", () => {
  it("does not expose inert controls when privacy endpoints fail", async () => {
    fetchMock.mockResolvedValue(jsonResponse({}, 503));

    renderWithProviders(<PrivacySettingsPage />, { locale: "zh-CN" });

    const identitySection = screen.getByTestId("identity-protection-section");
    const aiModeSection = screen.getByTestId("ai-mode-section");
    const denylistSection = screen.getByTestId("path-denylist-section");

    expect(await within(identitySection).findByRole("alert")).toHaveTextContent(
      "原有配置没有被更改",
    );
    expect(
      within(identitySection).queryByRole("button", {
        name: "关闭产品身份保护",
      }),
    ).not.toBeInTheDocument();
    expect(within(aiModeSection).getByRole("alert")).toBeInTheDocument();
    expect(
      within(aiModeSection).queryByRole("button", { name: /效率模式/ }),
    ).not.toBeInTheDocument();
    expect(within(denylistSection).getByRole("alert")).toBeInTheDocument();
    expect(
      within(denylistSection).getByRole("button", {
        name: "添加不可读取文件夹",
      }),
    ).toBeDisabled();
  });
});
