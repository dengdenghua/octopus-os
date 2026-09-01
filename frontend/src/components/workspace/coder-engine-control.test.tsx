import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import {
  CoderEngineControl,
  CoderEngineSettings,
} from "./coder-engine-control";

vi.mock("@/providers/AuthProvider", () => ({
  useAuth: () => ({
    user: { user_id: "actor-a", actor_id: "actor-a", username: "Alice" },
  }),
}));

const fetchMock = vi.fn();

const systemProfile = {
  mode: "follow_system",
  effective_model: "gpt-5.6",
  system_model: "gpt-5.6",
  reasoning_effort: "high",
  compatible: true,
  compatibility_reason: null,
  provider: "openai-compatible",
};

const accountProfile = {
  ...systemProfile,
  mode: "chatgpt",
  effective_model: "gpt-5.6-codex",
  provider: "openai",
};

const models = {
  source: "codex",
  models: [
    {
      id: "gpt-5.6-codex",
      display_name: "GPT-5.6 Codex",
      reasoning_efforts: ["medium", "high", "xhigh"],
      default_reasoning_effort: "high",
      hidden: false,
      is_default: true,
      input_modalities: ["text", "image"],
    },
  ],
};

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

function urlOf(input: RequestInfo | URL) {
  return typeof input === "string" ? input : input.toString();
}

beforeEach(() => {
  localStorage.clear();
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = urlOf(input);
    if (url.includes("/model-profile")) return jsonResponse(systemProfile);
    if (url.includes("/account")) {
      return jsonResponse({
        account: null,
        requires_openai_auth: false,
        login_pending: false,
        login_id: null,
        login_error: null,
      });
    }
    if (url.includes("/models")) return jsonResponse(models);
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  Reflect.deleteProperty(window, "echo");
});

describe("CoderEngineControl", () => {
  it("lets the Echo kernel switch between system and ChatGPT subscription models without mutating the Codex profile", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const onEffectiveModelChange = vi.fn();
    const onReasoningEffortChange = vi.fn();
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = urlOf(input);
      if (url.includes("/model-profile")) return jsonResponse(systemProfile);
      if (url.includes("/account")) {
        return jsonResponse({
          account: { type: "chatgpt", email: null, plan_type: "plus" },
          requires_openai_auth: true,
          login_pending: false,
        });
      }
      if (url.includes("/models")) return jsonResponse(models);
      return jsonResponse({});
    });

    renderWithProviders(
      <CoderEngineControl
        executionEngine="echo"
        value="echo-custom-model:v1:deepseek-selection"
        onChange={onChange}
        onEffectiveModelChange={onEffectiveModelChange}
        reasoningEffort="high"
        onReasoningEffortChange={onReasoningEffortChange}
        systemModels={[
          {
            name: "deepseek",
            display_name: "DeepSeek",
            source_display_name: "OpenCode Zen",
            entry_id: "deepseek-endpoint",
            selection_id: "echo-custom-model:v1:deepseek-selection",
            model: "deepseek-chat",
            reasoning_efforts: ["low", "high"],
          },
        ]}
      />,
      { locale: "zh-CN" },
    );

    expect(await screen.findByTestId("coder-engine-trigger")).toHaveAttribute(
      "aria-label",
      "OpenCode Zen · DeepSeek",
    );
    fireEvent.pointerDown(screen.getByTestId("coder-engine-trigger"), {
      button: 0,
      ctrlKey: false,
    });
    fireEvent.click(screen.getByTestId("coder-engine-trigger"));
    expect(await screen.findByRole("menu")).toHaveClass("w-72");
    expect(
      screen.getByRole("button", { name: /自动.*按任务智能选择/ }),
    ).toBeVisible();
    await user.click(await screen.findByText("ChatGPT 订阅"));
    await user.click(
      await screen.findByRole("button", { name: "GPT-5.6 Codex" }),
    );

    expect(onChange).toHaveBeenCalledWith("chatgpt/gpt-5.6-codex");
    expect(onEffectiveModelChange).toHaveBeenCalledWith("gpt-5.6-codex");
    expect(
      screen.queryByRole("radiogroup", { name: "上下文长度" }),
    ).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          urlOf(input as RequestInfo | URL).includes("/model-profile") &&
          (init as RequestInit | undefined)?.method === "PUT",
      ),
    ).toBe(false);

    await user.click(screen.getByText("Echo 模型"));
    await user.click(screen.getByRole("button", { name: "DeepSeek" }));
    expect(onChange).toHaveBeenLastCalledWith(
      "echo-custom-model:v1:deepseek-selection",
    );
    expect(onEffectiveModelChange).toHaveBeenLastCalledWith("DeepSeek");
    await user.click(screen.getByRole("button", { name: "高" }));
    expect(onReasoningEffortChange).toHaveBeenCalledWith("high");
  });

  it("offers both Echo and Codex model domains with system reasoning controls", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = urlOf(input);
        if (url.includes("/model-profile") && init?.method === "PUT") {
          const body = JSON.parse(String(init.body));
          return jsonResponse({
            ...systemProfile,
            selected_model: body.model || null,
            effective_model: body.model || systemProfile.system_model,
            model_source: body.model ? "role" : "system",
            reasoning_effort: body.reasoning_effort || null,
          });
        }
        if (url.includes("/model-profile")) return jsonResponse(systemProfile);
        if (url.includes("/account")) {
          return jsonResponse({
            account: null,
            requires_openai_auth: false,
            login_pending: false,
          });
        }
        return jsonResponse(models);
      },
    );
    renderWithProviders(
      <CoderEngineControl
        systemModels={[
          {
            name: "mix",
            display_name: "mix",
            model: "echo-mix",
          },
          {
            name: "deepseek",
            display_name: "DeepSeek",
            entry_id: "deepseek-endpoint",
            model: "deepseek-chat",
            context_window: 256_000,
            reasoning_efforts: ["low", "high"],
          },
          {
            name: "deepseek::1m",
            display_name: "DeepSeek",
            entry_id: "deepseek-endpoint",
            model: "deepseek-chat",
            context_window: 1_000_000,
            context_profile: "1m",
            reasoning_efforts: ["low", "high"],
          },
        ]}
      />,
      { locale: "zh-CN" },
    );

    expect(await screen.findByText("gpt-5.6")).toBeInTheDocument();
    expect(screen.getByTestId("coder-engine-trigger")).toHaveAttribute(
      "aria-label",
      "系统 · gpt-5.6",
    );
    await user.hover(screen.getByTestId("coder-engine-trigger"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "系统 · gpt-5.6",
    );
    await user.unhover(screen.getByTestId("coder-engine-trigger"));
    fireEvent.pointerDown(screen.getByTestId("coder-engine-trigger"), {
      button: 0,
      ctrlKey: false,
    });
    fireEvent.click(screen.getByTestId("coder-engine-trigger"));

    expect(await screen.findByText("Echo 模型")).toBeInTheDocument();
    expect(screen.getByText("ChatGPT 订阅")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "mix" }),
    ).not.toBeInTheDocument();
    expect(screen.getAllByText("DeepSeek")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "DeepSeek" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/model-profile"),
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"model":"deepseek-endpoint"'),
        }),
      ),
    );
    const context = await screen.findByRole("radiogroup", {
      name: "上下文长度",
    });
    expect(
      within(context).getByRole("radio", { name: "标准 · 256K" }),
    ).toHaveAttribute("aria-checked", "true");
    await user.click(within(context).getByRole("radio", { name: "Max · 1M" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([, init]) =>
          String((init as RequestInit | undefined)?.body).includes(
            '"model":"deepseek::1m"',
          ),
        ),
      ).toBe(true),
    );
    expect(await screen.findByRole("button", { name: "高" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "高" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([, init]) =>
          String((init as RequestInit | undefined)?.body).includes(
            '"reasoning_effort":"high"',
          ),
        ),
      ).toBe(true),
    );
  });

  it("explains that the system orchestrator cannot run Coder work directly", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = urlOf(input);
      if (url.includes("/model-profile")) {
        return jsonResponse({
          ...systemProfile,
          effective_model: "echo-mix",
          system_model: "echo-mix",
          compatible: false,
        });
      }
      if (url.includes("/account")) {
        return jsonResponse({
          account: null,
          requires_openai_auth: false,
          login_pending: false,
        });
      }
      return jsonResponse(models);
    });
    renderWithProviders(
      <CoderEngineControl
        systemModels={[
          { name: "mix", display_name: "mix", model: "echo-mix" },
          {
            name: "deepseek",
            display_name: "DeepSeek",
            model: "deepseek-chat",
          },
        ]}
      />,
      { locale: "zh-CN" },
    );

    fireEvent.pointerDown(await screen.findByTestId("coder-engine-trigger"), {
      button: 0,
      ctrlKey: false,
    });
    fireEvent.click(screen.getByTestId("coder-engine-trigger"));

    expect(
      await screen.findByText("当前是编排模型，请在下方选择实际模型"),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "mix" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "DeepSeek" })).toBeVisible();
  });

  it("updates the selected model immediately while the save finishes", async () => {
    const user = userEvent.setup();
    const onEffectiveModelChange = vi.fn();
    let finishSave:
      | ((response: ReturnType<typeof jsonResponse>) => void)
      | undefined;
    const pendingSave = new Promise<ReturnType<typeof jsonResponse>>(
      (resolve) => {
        finishSave = resolve;
      },
    );
    fetchMock.mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = urlOf(input);
        if (url.includes("/model-profile") && init?.method === "PUT") {
          return pendingSave;
        }
        if (url.includes("/model-profile")) return jsonResponse(systemProfile);
        if (url.includes("/account")) {
          return jsonResponse({
            account: { type: "chatgpt", email: null, plan_type: "plus" },
            requires_openai_auth: true,
            login_pending: false,
          });
        }
        if (url.includes("/models")) return jsonResponse(models);
        return jsonResponse({});
      },
    );
    const view = renderWithProviders(
      <CoderEngineControl onEffectiveModelChange={onEffectiveModelChange} />,
      { locale: "zh-CN" },
    );

    await screen.findByText("gpt-5.6");
    fireEvent.pointerDown(screen.getByTestId("coder-engine-trigger"), {
      button: 0,
      ctrlKey: false,
    });
    fireEvent.click(screen.getByTestId("coder-engine-trigger"));
    await user.click(await screen.findByText("ChatGPT 订阅"));
    await user.click(
      await screen.findByRole("button", { name: "GPT-5.6 Codex" }),
    );

    expect(screen.getByTestId("coder-engine-trigger")).toHaveTextContent(
      "gpt-5.6-codex",
    );
    expect(screen.getByTestId("coder-engine-trigger")).toHaveAttribute(
      "aria-label",
      "ChatGPT 订阅 · gpt-5.6-codex",
    );
    expect(view.container.querySelector(".animate-spin")).toBeNull();

    finishSave?.(jsonResponse(accountProfile));
    await waitFor(() =>
      expect(screen.getByTestId("coder-engine-trigger")).toHaveTextContent(
        "gpt-5.6-codex",
      ),
    );
    expect(onEffectiveModelChange).toHaveBeenCalledWith("gpt-5.6-codex");
  });

  it("explains when a system model controls reasoning automatically", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = urlOf(input);
      if (url.includes("/model-profile")) {
        return jsonResponse({
          ...systemProfile,
          selected_model: "deepseek",
          effective_model: "deepseek",
          model_source: "role",
        });
      }
      if (url.includes("/account")) {
        return jsonResponse({ account: null, login_pending: false });
      }
      return jsonResponse(models);
    });

    renderWithProviders(
      <CoderEngineControl
        systemModels={[
          {
            name: "deepseek",
            display_name: "DeepSeek",
            reasoning_efforts: [],
          },
        ]}
      />,
      { locale: "zh-CN" },
    );

    fireEvent.pointerDown(await screen.findByTestId("coder-engine-trigger"), {
      button: 0,
      ctrlKey: false,
    });
    fireEvent.click(screen.getByTestId("coder-engine-trigger"));

    expect(await screen.findByText("当前模型自动控制")).toBeInTheDocument();
  });
});

describe("CoderEngineSettings", () => {
  it("starts and explicitly cancels a device-code login without persisting the auth URL", async () => {
    const user = userEvent.setup();
    const openExternal = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window, "echo", {
      configurable: true,
      value: { app: { openExternal } },
    });
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");

    fetchMock.mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = urlOf(input);
        if (url.includes("/model-profile")) return jsonResponse(systemProfile);
        if (url.includes("/account")) {
          return jsonResponse({
            account: null,
            requires_openai_auth: false,
            login_pending: false,
          });
        }
        if (url.endsWith("/login") && init?.method === "POST") {
          return jsonResponse({
            type: "chatgptDeviceCode",
            login_id: "device-login-1",
            verification_url: "https://auth.example.test/device?state=one-time",
            user_code: "ABCD-EFGH",
          });
        }
        if (url.endsWith("/device-login-1/cancel")) {
          return jsonResponse({ cancelled: true });
        }
        return jsonResponse(models);
      },
    );

    renderWithProviders(<CoderEngineSettings />, { locale: "zh-CN" });
    await screen.findByRole("button", { name: "使用设备码" });
    await user.click(screen.getByRole("button", { name: "使用设备码" }));

    expect(await screen.findByText("ABCD-EFGH")).toBeInTheDocument();
    expect(openExternal).toHaveBeenCalledWith(
      "https://auth.example.test/device?state=one-time",
    );
    expect(storageWrite).not.toHaveBeenCalled();
    expect(localStorage.length).toBe(0);

    await user.click(screen.getByRole("button", { name: "取消授权" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/device-login-1/cancel"),
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("preserves an unfinished browser login across unmount and rehydrates it", async () => {
    const user = userEvent.setup();
    let pending = false;
    Object.defineProperty(window, "echo", {
      configurable: true,
      value: { app: { openExternal: vi.fn().mockResolvedValue(undefined) } },
    });
    fetchMock.mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = urlOf(input);
        if (url.includes("/model-profile")) return jsonResponse(systemProfile);
        if (url.includes("/account")) {
          return jsonResponse({
            account: null,
            requires_openai_auth: false,
            login_pending: pending,
            login_id: pending ? "browser-login-1" : null,
            login_error: null,
          });
        }
        if (url.endsWith("/login") && init?.method === "POST") {
          pending = true;
          return jsonResponse({
            type: "chatgpt",
            login_id: "browser-login-1",
            auth_url: "https://auth.example.test/oauth",
          });
        }
        if (url.endsWith("/browser-login-1/cancel")) {
          pending = false;
          return jsonResponse({ cancelled: true });
        }
        return jsonResponse({});
      },
    );

    const view = renderWithProviders(<CoderEngineSettings />, {
      locale: "en-US",
    });
    await screen.findByRole("button", { name: "Sign in with ChatGPT" });
    await user.click(
      screen.getByRole("button", { name: "Sign in with ChatGPT" }),
    );
    await screen.findAllByText("Authorization pending");
    view.unmount();

    expect(
      fetchMock.mock.calls.some(([input, init]) => {
        const url = urlOf(input as RequestInfo | URL);
        return (
          url.endsWith("/browser-login-1/cancel") &&
          (init as RequestInit | undefined)?.method === "POST"
        );
      }),
    ).toBe(false);

    renderWithProviders(<CoderEngineSettings />, { locale: "en-US" });
    const cancel = await screen.findByRole("button", {
      name: "Cancel authorization",
    });
    expect(
      fetchMock.mock.calls.filter(([input, init]) => {
        const url = urlOf(input as RequestInfo | URL);
        return url.endsWith("/login") && init?.method === "POST";
      }),
    ).toHaveLength(1);
    await user.click(cancel);
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/browser-login-1/cancel"),
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("submits an API key once, clears the input immediately, and switches to the Codex account source", async () => {
    const user = userEvent.setup();
    let connected = false;
    fetchMock.mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = urlOf(input);
        if (url.includes("/account")) {
          return jsonResponse({
            account: connected
              ? { type: "apiKey", email: null, plan_type: null }
              : null,
            requires_openai_auth: false,
            login_pending: false,
          });
        }
        if (url.endsWith("/login") && init?.method === "POST") {
          connected = true;
          return jsonResponse({ type: "apiKey" });
        }
        if (url.includes("/model-profile") && init?.method === "PUT") {
          return jsonResponse(accountProfile);
        }
        if (url.includes("/model-profile")) return jsonResponse(systemProfile);
        if (url.includes("/models")) return jsonResponse(models);
        return jsonResponse({});
      },
    );
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");

    renderWithProviders(<CoderEngineSettings />, { locale: "en-US" });
    await screen.findByText("Use API key");
    await user.click(screen.getByText("Use API key"));
    const keyInput = screen.getByLabelText("OpenAI API key");
    await user.type(keyInput, "sk-only-in-request");
    await user.click(screen.getByRole("button", { name: "Connect API key" }));

    expect(keyInput).toHaveValue("");
    await waitFor(() => {
      const request = fetchMock.mock.calls.find(([input, init]) => {
        const url = urlOf(input as RequestInfo | URL);
        return (
          url.endsWith("/login") && (init as RequestInit)?.method === "POST"
        );
      });
      expect(request).toBeDefined();
      expect(JSON.parse(String((request?.[1] as RequestInit).body))).toEqual({
        type: "apiKey",
        api_key: "sk-only-in-request",
      });
    });
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input, init]) => {
          const url = urlOf(input as RequestInfo | URL);
          const body = JSON.parse(
            String((init as RequestInit | undefined)?.body ?? "{}"),
          );
          return url.includes("/model-profile") && body.mode === "chatgpt";
        }),
      ).toBe(true);
    });
    expect(storageWrite).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain("sk-only-in-request");
  });

  it("does not start a second login when the backend reports one already pending", async () => {
    fetchMock.mockImplementation(
      async (input: RequestInfo | URL, _init?: RequestInit) => {
        const url = urlOf(input);
        if (url.includes("/model-profile")) return jsonResponse(systemProfile);
        if (url.includes("/account")) {
          return jsonResponse({
            account: null,
            requires_openai_auth: false,
            login_pending: true,
            login_id: "recovered-login-1",
            login_error: null,
          });
        }
        return jsonResponse({});
      },
    );

    renderWithProviders(<CoderEngineSettings />, { locale: "en-US" });

    expect(
      await screen.findByRole("button", { name: "Sign in with ChatGPT" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Use device code" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Connect API key" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Cancel authorization" }),
    ).toBeEnabled();

    fireEvent.click(
      screen.getByRole("button", { name: "Cancel authorization" }),
    );
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/recovered-login-1/cancel"),
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("leaves the pending state when App Server reports a failed login", async () => {
    let accountReads = 0;
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = urlOf(input);
      if (url.includes("/model-profile")) return jsonResponse(systemProfile);
      if (url.includes("/account")) {
        accountReads += 1;
        return jsonResponse(
          accountReads === 1
            ? {
                account: null,
                requires_openai_auth: true,
                login_pending: true,
                login_id: "failed-login-1",
                login_error: null,
              }
            : {
                account: null,
                requires_openai_auth: true,
                login_pending: false,
                login_id: null,
                login_error: "Codex login did not complete",
              },
        );
      }
      return jsonResponse({});
    });

    renderWithProviders(<CoderEngineSettings />, { locale: "en-US" });

    expect(
      await screen.findByText("Codex login did not complete"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Sign in with ChatGPT" }),
    ).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "Cancel authorization" }),
    ).not.toBeInTheDocument();
  });

  it("offers reasoning effort for the account default model", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = urlOf(input);
      if (url.includes("/model-profile")) {
        return jsonResponse({
          ...accountProfile,
          effective_model: null,
          reasoning_effort: null,
        });
      }
      if (url.includes("/account")) {
        return jsonResponse({
          account: {
            type: "chatgpt",
            email: "a@example.test",
            plan_type: "plus",
          },
          requires_openai_auth: true,
          login_pending: false,
          login_id: null,
          login_error: null,
        });
      }
      if (url.includes("/models")) return jsonResponse(models);
      return jsonResponse({});
    });

    renderWithProviders(<CoderEngineSettings />, { locale: "en-US" });

    const effort = await screen.findByLabelText("Reasoning effort");
    await waitFor(() => expect(effort).toBeEnabled());
  });

  it("shows ChatGPT quota remainder, reset time, and account token totals", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = urlOf(input);
        if (url.includes("/model-profile")) return jsonResponse(accountProfile);
        if (url.includes("/account")) {
          return jsonResponse({
            account: {
              type: "chatgpt",
              email: "a@example.test",
              plan_type: "plus",
            },
            requires_openai_auth: true,
            login_pending: false,
          });
        }
        if (url.includes("/models")) return jsonResponse(models);
        if (url.includes("/rate-limits")) {
          return jsonResponse({
            buckets: [
              {
                limit_id: "codex",
                limit_name: "Codex",
                primary: {
                  used_percent: 25,
                  remaining_percent: 75,
                  window_duration_mins: 15,
                  resets_at: 1_730_947_200,
                },
                secondary: null,
                plan_type: "plus",
                rate_limit_reached_type: null,
              },
            ],
            reset_credits_available: 2,
          });
        }
        if (url.includes("/usage")) {
          return jsonResponse({
            summary: {
              lifetime_tokens: 1_234_567,
              peak_daily_tokens: 45_678,
            },
            daily_usage_buckets: [],
          });
        }
        if (url.includes("/apps")) {
          if (init?.method === "PUT") {
            return jsonResponse({
              apps: [
                {
                  id: "google_drive",
                  name: "Google Drive",
                  description: "Search Drive files",
                  is_accessible: true,
                  is_enabled: false,
                  selected: true,
                },
              ],
            });
          }
          return jsonResponse({
            apps: [
              {
                id: "google_drive",
                name: "Google Drive",
                description: "Search Drive files",
                is_accessible: true,
                is_enabled: false,
                selected: false,
              },
            ],
          });
        }
        return jsonResponse({});
      },
    );

    renderWithProviders(<CoderEngineSettings />, { locale: "en-US" });

    const remaining = await screen.findByText("75% remaining");
    expect(remaining).not.toBeVisible();
    await user.click(
      screen.getByText("Connectors and usage", { selector: "summary" }),
    );
    expect(remaining).toBeVisible();
    expect(await screen.findByText("1,234,567")).toBeVisible();
    expect(screen.getByText("45,678")).toBeVisible();
    expect(screen.getByText("2")).toBeVisible();
    await user.click(
      await screen.findByRole("button", { name: /Google Drive/ }),
    );
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input, init]) => {
          const url = urlOf(input as RequestInfo | URL);
          return (
            url.includes("/apps") &&
            (init as RequestInit | undefined)?.method === "PUT" &&
            String((init as RequestInit).body).includes("google_drive")
          );
        }),
      ).toBe(true),
    );
  });
});
