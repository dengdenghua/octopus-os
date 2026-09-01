/**
 * Smoke tests for the unified "模型配置" panel on the model
 * settings page. After the list refactor, each custom-model row
 * carries an open-ended ``models`` array (index 0 = picker default,
 * index -1 = strongest slot for Auto mode). These tests exercise
 * the new shape — they use the edit form because it has the most
 * surface area (rows render the same data, but the inputs there
 * are editable and let us assert the "+ Add / × remove" controls).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithProviders } from "@/test/harness";
import {
  getLocalSettings,
  getThreadModelName,
  saveLocalSettings,
  saveThreadModelName,
} from "@/core/settings/local";

import ModelSettingsPage, {
  customModelMatchesSelection,
  customModelPreferredSelection,
} from "./model-settings-page";

// ModelCookbook fetches /api/cookbook/snapshot on mount — unrelated to the
// custom-model list under test here, and its fetch would consume the
// order-dependent mockResolvedValueOnce below. Stub it out so the page's
// /api/config/custom-models fetch deterministically receives the mock.
vi.mock("@/components/workspace/model-cookbook", () => ({
  ModelCookbook: () => null,
}));

vi.mock("@/providers/AuthProvider", () => ({
  useAuth: () => ({
    user: { user_id: "actor-a", actor_id: "actor-a", username: "Alice" },
  }),
}));

// MixSettingsSection fetches its own /api/mix-config + model list on mount.
// That's unrelated to the custom-model list under test here and would perturb
// the ordered fetch mocks below — stub it out.
vi.mock("./mix-settings-section", () => ({
  MixSettingsSection: () => null,
}));

const fetchMock = vi.fn();

beforeEach(() => {
  localStorage.clear();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  // The default model name is loaded from a /api/config call on mount.
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/config/custom-models/compat-diagnostics")) {
      return jsonOk({
        schema: "echo.openai_compat_diagnostics.v1",
        diagnostics: [],
      });
    }
    if (url.includes("/api/config/openai-compat-profiles")) {
      return jsonOk({
        schema: "echo.openai_compat_profile_catalog.v1",
        diagnostics: [],
      });
    }
    return jsonOk({ default: "", models: [] });
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

function jsonOk(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

function mockModelSettingsFetch({
  models,
  diagnostics = [],
  profileCatalog = [],
}: {
  models: unknown[];
  diagnostics?: unknown[];
  profileCatalog?: unknown[];
}) {
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/config/custom-models/compat-diagnostics")) {
      return jsonOk({
        schema: "echo.openai_compat_diagnostics.v1",
        diagnostics,
      });
    }
    if (url.includes("/api/config/openai-compat-profiles")) {
      return jsonOk({
        schema: "echo.openai_compat_profile_catalog.v1",
        diagnostics: profileCatalog,
      });
    }
    // Saving an entry is gated on a passing connection test, so the probe
    // endpoint must be stubbed ahead of the generic custom-models branch
    // (which would otherwise swallow it and answer without `ok`).
    if (url.includes("/api/config/custom-models/test")) {
      return jsonOk({ ok: true, latency_ms: 12, model: "upstream-model" });
    }
    if (url.includes("/api/config/custom-models")) {
      return jsonOk({ models });
    }
    return jsonOk({ default: "", models: [] });
  });
}

describe("custom model selection identity", () => {
  const model = {
    id: "provider-entry",
    name: "provider-alias",
    models: ["provider-model-fast", "provider-model-strong"],
    selection_ids: [
      "selection-provider-fast-default",
      "selection-provider-fast-1m",
    ],
  };

  it("recognizes entry ids, aliases, and concrete upstream model ids", () => {
    expect(customModelMatchesSelection(model, "provider-entry")).toBe(true);
    expect(customModelMatchesSelection(model, "provider-alias")).toBe(true);
    expect(customModelMatchesSelection(model, "provider-model-fast")).toBe(
      true,
    );
    expect(
      customModelMatchesSelection(model, "selection-provider-fast-1m"),
    ).toBe(true);
    expect(customModelMatchesSelection(model, "unrelated-model")).toBe(false);
  });

  it("prefers the exact default row selection and falls back for old catalogs", () => {
    expect(customModelPreferredSelection(model)).toBe(
      "selection-provider-fast-default",
    );
    expect(
      customModelPreferredSelection({
        id: "legacy-entry",
        name: "legacy-alias",
        models: ["legacy-model"],
      }),
    ).toBe("legacy-model");
    expect(
      customModelPreferredSelection({
        id: "empty-entry",
        name: "empty-alias",
        models: [],
      }),
    ).toBe("empty-entry");
  });
});

describe("ModelSettingsPage · custom-model list rendering", () => {
  it("renders the full models list for an entry with multiple slots", async () => {
    mockModelSettingsFetch({
      models: [
        {
          id: "openai-prod",
          name: "openai-prod",
          display_name: "My OpenAI",
          models: ["gpt-4o-mini", "gpt-4o", "gpt-4.1"],
          provider: "openai",
          base_url: "https://api.openai.com/v1",
          has_api_key: true,
          supports_thinking: false,
          supports_vision: false,
        },
      ],
    });

    renderWithProviders(<ModelSettingsPage />, { locale: "zh-CN" });

    await waitFor(() => {
      expect(screen.getByText("My OpenAI")).toBeInTheDocument();
    });

    // The "3 models" count chip surfaces in the row.
    expect(screen.getByText("3 个模型")).toBeInTheDocument();
    // All three model ids are visible inline.
    expect(screen.getByText("gpt-4o-mini")).toBeInTheDocument();
    expect(screen.getByText("gpt-4o")).toBeInTheDocument();
    expect(screen.getByText("gpt-4.1")).toBeInTheDocument();
    expect(screen.getByText("选择器默认")).toBeInTheDocument();
    expect(screen.getByText("备用")).toBeInTheDocument();
    expect(screen.getByText("高性能档")).toBeInTheDocument();
    expect(screen.getByText("1 个连接 · 3 个模型")).toBeInTheDocument();
    expect(
      screen.getByText(/新的服务可通过 API 连接或本地扫描接入/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "API 模型连接", level: 3 }),
    ).toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: "接入 API 模型" }),
    ).toHaveLength(1);
  });

  it("does not repeat an entry name and explains a single model's two roles", async () => {
    mockModelSettingsFetch({
      models: [
        {
          id: "same-name-entry",
          name: "Same Name",
          display_name: "Same Name",
          models: ["upstream-model"],
          provider: "openai",
          base_url: "https://api.openai.com/v1",
          has_api_key: true,
          supports_thinking: false,
          supports_vision: false,
        },
      ],
    });

    renderWithProviders(<ModelSettingsPage />, { locale: "zh-CN" });

    await waitFor(() => {
      expect(screen.getByText("Same Name")).toBeInTheDocument();
    });

    expect(screen.getAllByText("Same Name")).toHaveLength(1);
    expect(screen.getByText("默认 · 高性能")).toBeInTheDocument();
  });

  it("expands the editor inside the selected connection row", async () => {
    const user = userEvent.setup();
    mockModelSettingsFetch({
      models: [
        {
          id: "first-entry",
          name: "first-entry",
          display_name: "First connection",
          models: ["first-model"],
          provider: "openai",
          base_url: "https://first.example.test/v1",
          has_api_key: true,
        },
        {
          id: "second-entry",
          name: "second-entry",
          display_name: "Second connection",
          models: ["second-model"],
          provider: "openai",
          base_url: "https://second.example.test/v1",
          has_api_key: true,
        },
      ],
    });

    renderWithProviders(<ModelSettingsPage />, { locale: "zh-CN" });

    const firstEdit = await screen.findByRole("button", {
      name: "编辑: First connection",
    });
    const secondEdit = screen.getByRole("button", {
      name: "编辑: Second connection",
    });
    const firstRow = firstEdit.closest("li");
    const secondRow = secondEdit.closest("li");

    expect(firstRow).not.toBeNull();
    expect(secondRow).not.toBeNull();
    expect(firstEdit).toHaveAttribute("aria-expanded", "false");

    await user.click(firstEdit);

    await waitFor(() => {
      expect(
        within(firstRow as HTMLLIElement).getByLabelText("显示名称"),
      ).toHaveValue("First connection");
    });
    expect(firstEdit).toHaveAttribute("aria-expanded", "true");
    expect(
      within(secondRow as HTMLLIElement).queryByLabelText("显示名称"),
    ).not.toBeInTheDocument();

    await user.click(secondEdit);

    await waitFor(() => {
      expect(
        within(secondRow as HTMLLIElement).getByLabelText("显示名称"),
      ).toHaveValue("Second connection");
    });
    expect(firstEdit).toHaveAttribute("aria-expanded", "false");
    expect(secondEdit).toHaveAttribute("aria-expanded", "true");
    expect(
      within(firstRow as HTMLLIElement).queryByLabelText("显示名称"),
    ).not.toBeInTheDocument();
  });

  it("allows changing and saving a connection display name", async () => {
    const user = userEvent.setup();
    mockModelSettingsFetch({
      models: [
        {
          id: "display-name-entry",
          name: "display-name-entry",
          display_name: "Old display name",
          models: ["upstream-model"],
          provider: "openai",
          base_url: "https://api.example.test/v1",
          has_api_key: true,
        },
      ],
    });

    renderWithProviders(<ModelSettingsPage />, { locale: "zh-CN" });
    await screen.findByText("Old display name");
    await user.click(
      screen.getByRole("button", { name: "编辑: Old display name" }),
    );

    const displayName = await screen.findByLabelText("显示名称");
    await user.clear(displayName);
    await user.type(displayName, "New display name");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input, init]) => {
          const url = typeof input === "string" ? input : input.toString();
          if (
            !url.includes("/api/config/custom-models/display-name-entry") ||
            (init as RequestInit | undefined)?.method !== "PUT"
          ) {
            return false;
          }
          const body = JSON.parse(String((init as RequestInit).body));
          return body.display_name === "New display name";
        }),
      ).toBe(true);
    });
  });

  it("filters the default-effort dropdown to the model's capability set", async () => {
    const user = userEvent.setup();
    mockModelSettingsFetch({
      models: [
        {
          id: "deepseek-entry",
          name: "deepseek-entry",
          display_name: "DeepSeek Entry",
          models: ["deepseek-v4-pro"],
          provider: "openai",
          base_url: "https://api.deepseek.com/v1",
          has_api_key: true,
          supports_thinking: true,
          reasoning_efforts: ["off", "high", "xhigh"],
        },
      ],
    });

    renderWithProviders(<ModelSettingsPage />, { locale: "zh-CN" });
    await screen.findByText("DeepSeek Entry");
    await user.click(
      screen.getByRole("button", { name: "编辑: DeepSeek Entry" }),
    );

    // DeepSeek only distinguishes off/high/max on the wire — the dropdown
    // should offer 跟随 / 关闭 / 高 / 最高 / 不注入, not 低/中.
    const select = await screen.findByLabelText("默认推理等级");
    const options = Array.from(select.querySelectorAll("option")).map(
      (o) => o.textContent,
    );
    expect(options).toEqual([
      "跟随内置默认",
      "关闭",
      "高",
      "最高",
      "不注入默认",
    ]);
  });

  it("hides the default-effort dropdown when the model has no tiers", async () => {
    const user = userEvent.setup();
    mockModelSettingsFetch({
      models: [
        {
          id: "minimax-entry",
          name: "minimax-entry",
          display_name: "MiniMax Entry",
          models: ["minimax-m2"],
          provider: "openai",
          base_url: "https://api.minimaxi.com/v1",
          has_api_key: true,
          supports_thinking: true,
          reasoning_efforts: [],
        },
      ],
    });

    renderWithProviders(<ModelSettingsPage />, { locale: "zh-CN" });
    await screen.findByText("MiniMax Entry");
    await user.click(
      screen.getByRole("button", { name: "编辑: MiniMax Entry" }),
    );

    await screen.findByLabelText("显示名称");
    expect(screen.queryByLabelText("默认推理等级")).not.toBeInTheDocument();
  });

  it("renders OpenAI-compatible diagnostics for a strict domestic provider", async () => {
    const user = userEvent.setup();
    mockModelSettingsFetch({
      models: [
        {
          id: "kimi-code",
          name: "kimi-code",
          display_name: "Kimi Code",
          models: ["kimi-k2.7-code"],
          provider: "openai",
          base_url: "https://api.kimi.com/coding/v1",
          has_api_key: true,
          supports_thinking: false,
          supports_vision: false,
        },
      ],
      diagnostics: [
        {
          id: "kimi-code",
          provider: "openai",
          applicable: true,
          has_api_key: true,
          default_header_names: ["User-Agent"],
          upstreams: [
            {
              model: "kimi-k2.7-code",
              profile: "kimi_coding",
              profile_display_name: "Kimi Coding",
              compat_score: 82,
              normalization_hints: [
                "drop_sampling_parameters",
                "retry_without_tool_choice",
              ],
              compatibility_notes: [
                "coding endpoint rejects sampling knobs",
                "drops OpenAI reasoning/thinking extensions",
              ],
              normalization: {
                removed_fields: [
                  "parallel_tool_calls",
                  "reasoning_effort",
                  "temperature",
                  "tool_choice",
                ],
                added_fields: [],
                changed_fields: ["tools"],
              },
              fallback_retries: [
                { reason: "strict_tool_schema", removed_fields: [] },
                {
                  reason: "combined_compatibility_fallback",
                  removed_fields: [],
                },
              ],
            },
          ],
        },
      ],
    });

    renderWithProviders(<ModelSettingsPage />, { locale: "zh-CN" });

    await waitFor(() => {
      expect(screen.getByText("Kimi Code")).toBeInTheDocument();
    });

    const advancedTitle = screen.getByText("高级能力与兼容诊断");
    expect(screen.getByText("兼容诊断")).not.toBeVisible();
    await user.click(advancedTitle);
    expect(screen.getByText("兼容诊断")).not.toBeVisible();
    await user.click(screen.getByText("连接与网关诊断"));

    await waitFor(() => {
      expect(screen.getByText("兼容诊断")).toBeVisible();
    });
    expect(screen.getByText("Kimi Coding")).toBeInTheDocument();
    expect(screen.getByText("2 个 fallback")).toBeInTheDocument();
    expect(screen.getByText("兼容分 82")).toBeInTheDocument();
    expect(screen.getByText("请求头: User-Agent")).toBeInTheDocument();
    expect(screen.getByText(/drop_sampling_parameters/)).toBeInTheDocument();
    expect(screen.getByText(/coding endpoint rejects/)).toBeInTheDocument();
    expect(screen.getByText(/parallel_tool_calls/)).toBeInTheDocument();
    expect(
      screen.getByText(/combined_compatibility_fallback/),
    ).toBeInTheDocument();
  });

  it("renders the built-in OpenAI-compatible provider matrix", async () => {
    const user = userEvent.setup();
    mockModelSettingsFetch({
      models: [],
      profileCatalog: [
        {
          id: "kimi_coding",
          provider: "openai",
          applicable: true,
          built_in: true,
          has_api_key: false,
          sample_base_url: "https://api.kimi.com/coding/v1",
          upstreams: [
            {
              model: "kimi-k2.7-code",
              profile: "kimi_coding",
              profile_display_name: "Kimi Coding",
              compat_score: 82,
              normalization_hints: [
                "drop_sampling_parameters",
                "retry_without_tool_choice",
              ],
              normalization: {
                removed_fields: ["temperature", "thinking"],
              },
              fallback_retries: [
                { reason: "rename_max_tokens" },
                { reason: "combined_compatibility_fallback" },
              ],
            },
          ],
        },
        {
          id: "qwen",
          provider: "openai",
          applicable: true,
          built_in: true,
          has_api_key: false,
          upstreams: [
            {
              model: "qwen-plus",
              profile: "qwen",
              profile_display_name: "Alibaba Cloud Qwen / DashScope",
              compat_score: 90,
              normalization_hints: ["retry_max_tokens_as_completion_tokens"],
              normalization: { removed_fields: [] },
              fallback_retries: [{ reason: "rename_max_tokens" }],
            },
          ],
        },
      ],
    });

    renderWithProviders(<ModelSettingsPage />, { locale: "zh-CN" });

    const compatMatrix = await screen.findByText("OpenAI 兼容配置矩阵");
    expect(compatMatrix).not.toBeVisible();
    await user.click(screen.getByText("高级能力与兼容诊断"));
    expect(compatMatrix).not.toBeVisible();
    await user.click(screen.getByText("提供方兼容矩阵"));
    expect(compatMatrix).toBeVisible();
    expect(await screen.findByText("2 个配置")).toBeInTheDocument();
    expect(screen.getByText("高级")).toBeInTheDocument();
    expect(screen.getByDisplayValue("同源代理")).toBeInTheDocument();
    expect(screen.queryByText("Advanced")).not.toBeInTheDocument();
    expect(await screen.findByText(/无需配置 API Key/)).toBeInTheDocument();
    expect(await screen.findByText("Kimi Coding")).toBeInTheDocument();
    expect(await screen.findByText("kimi-k2.7-code")).toBeInTheDocument();
    expect(
      await screen.findByText(/drop_sampling_parameters/),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("Alibaba Cloud Qwen / DashScope"),
    ).toBeInTheDocument();
    expect(await screen.findByText("qwen-plus")).toBeInTheDocument();
    expect(
      (await screen.findAllByText(/rename_max_tokens/)).length,
    ).toBeGreaterThan(0);
    expect(await screen.findByText("兼容分 82")).toBeInTheDocument();
    expect(await screen.findByText("2 次回退")).toBeInTheDocument();
    expect((await screen.findAllByText(/归一化：/)).length).toBeGreaterThan(1);
    expect(await screen.findByText(/移除：/)).toBeInTheDocument();
    expect((await screen.findAllByText(/重试：/)).length).toBeGreaterThan(1);
  });

  it("renders a single-model entry without a trailing junk chip", async () => {
    mockModelSettingsFetch({
      models: [
        {
          id: "single",
          name: "single",
          display_name: "Single",
          models: ["gpt-4o-mini"],
          provider: "openai",
          base_url: "https://api.openai.com/v1",
          has_api_key: true,
          supports_thinking: false,
          supports_vision: false,
        },
      ],
    });

    renderWithProviders(<ModelSettingsPage />, { locale: "zh-CN" });

    await waitFor(() => {
      expect(screen.getByText("Single")).toBeInTheDocument();
    });

    // Singular "1 model" form is rendered.
    expect(screen.getByText("1 个模型")).toBeInTheDocument();
    expect(screen.getByText("gpt-4o-mini")).toBeInTheDocument();
  });

  it("deletes a custom model after confirmation and refreshes the list", async () => {
    const user = userEvent.setup();
    let models: unknown[] = [
      {
        id: "disposable",
        name: "disposable",
        display_name: "Disposable",
        models: ["gpt-4o-mini"],
        provider: "openai",
        base_url: "https://api.openai.com/v1",
        has_api_key: true,
        supports_thinking: false,
        supports_vision: false,
      },
    ];
    fetchMock.mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/api/config/custom-models/compat-diagnostics")) {
          return jsonOk({
            schema: "echo.openai_compat_diagnostics.v1",
            diagnostics: [],
          });
        }
        if (url.includes("/api/config/openai-compat-profiles")) {
          return jsonOk({
            schema: "echo.openai_compat_profile_catalog.v1",
            diagnostics: [],
          });
        }
        if (
          url.includes("/api/config/custom-models/disposable") &&
          init?.method === "DELETE"
        ) {
          models = [];
          return jsonOk({ ok: true, removed: true });
        }
        if (url.includes("/api/config/custom-models")) {
          return jsonOk({ models });
        }
        return jsonOk({ default: "", models: [] });
      },
    );

    renderWithProviders(<ModelSettingsPage />, { locale: "zh-CN" });

    await waitFor(() => {
      expect(screen.getByText("Disposable")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "删除: Disposable" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "删除" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input, init]) => {
          const url = typeof input === "string" ? input : input.toString();
          return (
            url.includes("/api/config/custom-models/disposable") &&
            (init as RequestInit | undefined)?.method === "DELETE"
          );
        }),
      ).toBe(true);
    });
    await waitFor(() => {
      expect(screen.queryByText("Disposable")).not.toBeInTheDocument();
    });
  });

  it("deletes by stable id when the display name and alias differ", async () => {
    const user = userEvent.setup();
    let models: unknown[] = [
      {
        id: "kimi-k3",
        name: "Kimi K3",
        display_name: "Kimi K3 (火山 Agent Plan)",
        models: ["kimi-k3-upstream"],
        provider: "openai",
        base_url: "https://ark.example.test/v1",
        has_api_key: true,
      },
    ];
    fetchMock.mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/api/config/custom-models/compat-diagnostics")) {
          return jsonOk({ diagnostics: [] });
        }
        if (url.includes("/api/config/openai-compat-profiles")) {
          return jsonOk({ diagnostics: [] });
        }
        if (
          url.includes("/api/config/custom-models/kimi-k3") &&
          init?.method === "DELETE"
        ) {
          models = [];
          return jsonOk({ ok: true, removed: true });
        }
        if (url.includes("/api/config/custom-models")) {
          return jsonOk({ models });
        }
        return jsonOk({ default: "", models: [] });
      },
    );

    renderWithProviders(<ModelSettingsPage />, { locale: "zh-CN" });
    await screen.findByText("Kimi K3 (火山 Agent Plan)");
    await user.click(
      screen.getByRole("button", {
        name: "删除: Kimi K3 (火山 Agent Plan)",
      }),
    );
    const dialog = await screen.findByRole("dialog", { name: "删除模型" });
    expect(
      within(dialog).getByText("模型“Kimi K3 (火山 Agent Plan)”将被永久删除。"),
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "删除" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input, init]) => {
          const url = typeof input === "string" ? input : input.toString();
          return (
            url.endsWith("/api/config/custom-models/kimi-k3") &&
            (init as RequestInit | undefined)?.method === "DELETE"
          );
        }),
      ).toBe(true);
      expect(
        screen.queryByText("Kimi K3 (火山 Agent Plan)"),
      ).not.toBeInTheDocument();
    });
  });

  it("keeps a failed deletion recoverable and leaves the model visible", async () => {
    const user = userEvent.setup();
    const models = [
      {
        id: "protected",
        name: "protected",
        display_name: "Protected",
        models: ["protected-model"],
        provider: "openai",
        base_url: "https://api.example.test/v1",
        has_api_key: true,
      },
    ];
    fetchMock.mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/api/config/custom-models/compat-diagnostics")) {
          return jsonOk({ diagnostics: [] });
        }
        if (url.includes("/api/config/openai-compat-profiles")) {
          return jsonOk({ diagnostics: [] });
        }
        if (
          url.includes("/api/config/custom-models/protected") &&
          init?.method === "DELETE"
        ) {
          return {
            ok: false,
            status: 409,
            json: async () => ({ detail: "模型正在使用，请稍后重试" }),
          };
        }
        if (url.includes("/api/config/custom-models")) {
          return jsonOk({ models });
        }
        return jsonOk({ default: "", models: [] });
      },
    );

    renderWithProviders(<ModelSettingsPage />, { locale: "zh-CN" });
    await screen.findByText("Protected");
    await user.click(screen.getByRole("button", { name: "删除: Protected" }));
    const dialog = await screen.findByRole("dialog", { name: "删除模型" });
    await user.click(within(dialog).getByRole("button", { name: "删除" }));

    await waitFor(() =>
      expect(
        within(dialog).getByRole("button", { name: "删除" }),
      ).toBeEnabled(),
    );
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText("Protected")).toBeInTheDocument();
  });

  it("switches the default and clears stale thread overrides when the default is a concrete model id", async () => {
    const user = userEvent.setup();
    saveLocalSettings({
      ...getLocalSettings(),
      context: {
        ...getLocalSettings().context,
        model_name: "gpt-4o-mini",
      },
    });
    saveThreadModelName("legacy-thread", "gpt-4o-mini");
    let models: unknown[] = [
      {
        id: "disposable",
        name: "disposable",
        display_name: "Disposable",
        models: ["gpt-4o-mini"],
        provider: "openai",
        base_url: "https://api.openai.com/v1",
        has_api_key: true,
      },
      {
        id: "backup",
        name: "backup",
        display_name: "Backup",
        models: ["gpt-4.1-mini"],
        provider: "openai",
        base_url: "https://api.openai.com/v1",
        has_api_key: true,
      },
    ];
    fetchMock.mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/api/config/custom-models/compat-diagnostics")) {
          return jsonOk({ diagnostics: [] });
        }
        if (url.includes("/api/config/openai-compat-profiles")) {
          return jsonOk({ diagnostics: [] });
        }
        if (
          url.includes("/api/config/custom-models/disposable") &&
          init?.method === "DELETE"
        ) {
          models = models.filter(
            (model) => (model as { name?: string }).name !== "disposable",
          );
          return jsonOk({ ok: true, removed: true });
        }
        if (url.includes("/api/config/custom-models")) {
          return jsonOk({ models });
        }
        return jsonOk({ default: "", models: [] });
      },
    );

    renderWithProviders(<ModelSettingsPage />, { locale: "zh-CN" });
    expect((await screen.findAllByText("Disposable")).length).toBeGreaterThan(0);
    expect(screen.getByText("系统默认")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "删除: Disposable" }));
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText(
        "这是当前默认模型。删除后将自动切换到“Backup”。",
      ),
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "删除" }));

    await waitFor(() => {
      expect(getLocalSettings().context.model_name).toBe("gpt-4.1-mini");
      expect(getThreadModelName("legacy-thread")).toBeUndefined();
      expect(screen.queryByText("Disposable")).not.toBeInTheDocument();
    });
  });

  it("distinguishes a load failure from an empty list and retries in place", async () => {
    const user = userEvent.setup();
    let listCalls = 0;
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/config/custom-models/compat-diagnostics")) {
        return jsonOk({ diagnostics: [] });
      }
      if (url.includes("/api/config/openai-compat-profiles")) {
        return jsonOk({ diagnostics: [] });
      }
      if (url.includes("/api/config/custom-models")) {
        listCalls += 1;
        if (listCalls === 1) {
          return {
            ok: false,
            status: 503,
            json: async () => ({ detail: "offline" }),
          };
        }
        return jsonOk({
          models:
            listCalls >= 2
              ? [
                  {
                    id: "recovered",
                    name: "recovered",
                    display_name: "Recovered model",
                    models: ["recovered-model"],
                  },
                ]
              : [],
        });
      }
      return jsonOk({ default: "", models: [] });
    });

    renderWithProviders(<ModelSettingsPage />, { locale: "zh-CN" });
    const errorState = await screen.findByRole("alert");
    expect(screen.queryByText("暂无自定义模型")).not.toBeInTheDocument();
    await user.click(
      within(errorState).getByRole("button", { name: "重新加载" }),
    );

    expect(await screen.findByText("Recovered model")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("ModelSettingsPage · add-model form · open-ended list", () => {
  it("renders the model list with an add button and a remove control on each row", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ModelSettingsPage />, { locale: "zh-CN" });

    // Wait for the settings page to mount + the initial list call to resolve.
    await waitFor(() => {
      expect(screen.getByText("接入 API 模型")).toBeInTheDocument();
    });

    // Open the add-model form
    await user.click(screen.getByRole("button", { name: "接入 API 模型" }));

    // Label and hint are both visible, anchoring the new shape.
    expect(screen.getByText("模型列表")).toBeInTheDocument();
    expect(screen.getByText(/首项作为默认模型/)).toBeInTheDocument();
    const apiKeyInput = screen.getByPlaceholderText("请输入 API Key");
    expect(apiKeyInput).toHaveValue("");
    expect(apiKeyInput).toHaveAttribute("type", "password");
    expect(apiKeyInput).toHaveAttribute("autocomplete", "new-password");
    expect(apiKeyInput).toHaveAttribute("data-1p-ignore", "true");
    expect(screen.getByPlaceholderText("例如：我的模型")).toHaveValue("");
    expect(
      screen.getByRole("button", { name: "显示 API Key" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "测试连接" }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "取消" })).toHaveLength(1);
    const addForm = apiKeyInput.closest("form");
    expect(addForm).not.toBeNull();
    expect(
      within(addForm as HTMLFormElement).getByRole("status"),
    ).toHaveTextContent("尚未测试连接");
    expect(screen.getByRole("switch", { name: "思考" })).not.toBeChecked();
    expect(screen.getByRole("switch", { name: "视觉" })).not.toBeChecked();

    // The initial row + the "add model id" button are present.
    expect(
      screen.getByRole("button", { name: /添加模型 ID/ }),
    ).toBeInTheDocument();

    // Clicking "+ Add model ID" appends a new input row. Each new row
    // exposes a remove control with the documented tooltip.
    await user.click(screen.getByRole("button", { name: /添加模型 ID/ }));
    const removeButtons = screen.getAllByRole("button", {
      name: "删除该模型 ID",
    });
    expect(removeButtons.length).toBeGreaterThanOrEqual(2);
  });
});

describe("ModelSettingsPage · local-model one-click import", () => {
  it("renders the scan button, runs the scan, and shows discovered services", async () => {
    const user = userEvent.setup();
    // URL-aware mock: the scan endpoint returns a discovered
    // service, everything else falls back to the empty default.
    // We can't rely on mockResolvedValueOnce here because the
    // settings page kicks off a handful of fetches in parallel
    // on mount (custom-models list, gateway status, llm-models)
    // and we don't want to count them precisely per test.
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/config/local-models/scan")) {
        return jsonOk({
          services: [
            {
              provider: "openai",
              base_url: "http://127.0.0.1:11434/v1",
              probe_path: "/v1/models",
              models: ["qwen2.5:7b", "llama3.1:8b"],
              status: "ok",
            },
          ],
        });
      }
      return jsonOk({ default: "", models: [] });
    });

    renderWithProviders(<ModelSettingsPage />, { locale: "zh-CN" });

    // The section title is rendered on first paint.
    await waitFor(() => {
      expect(screen.getByText("本地模型")).toBeInTheDocument();
    });

    // The scan button starts in idle state — clicking it dispatches
    // the GET /scan request, then the section re-renders with the
    // discovered service list and its import buttons.
    const scanButton = screen.getByRole("button", {
      name: /扫描本地服务/,
    });
    await user.click(scanButton);

    // The scan response surfaces the base_url and the model-count
    // subtitle for the discovered service.
    await waitFor(() => {
      expect(screen.getByText("http://127.0.0.1:11434/v1")).toBeInTheDocument();
    });
    // Import button is present for the discovered service.
    const importButtons = screen.getAllByRole("button", { name: "一键导入" });
    expect(importButtons.length).toBe(1);
  });

  it("shows the empty-state hint when the scan returns no services", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/config/local-models/scan")) {
        return jsonOk({ services: [] });
      }
      return jsonOk({ default: "", models: [] });
    });

    renderWithProviders(<ModelSettingsPage />, { locale: "zh-CN" });

    await waitFor(() => {
      expect(screen.getByText("本地模型")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /扫描本地服务/ }));

    // Empty-state hint guides the operator toward starting a service.
    await waitFor(() => {
      expect(
        screen.getByText(/请先启动 Ollama \/ LM Studio/),
      ).toBeInTheDocument();
    });
  });
});
