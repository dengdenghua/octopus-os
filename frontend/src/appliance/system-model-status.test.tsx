import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import {
  coderQueryKeys,
  getCoderAccount,
  getCoderModelProfile,
  getCoderModels,
  getCoderRateLimits,
  getCoderUsage,
  updateCoderModelProfile,
} from "@/core/coder/api";
import { getLocalSettings } from "@/core/settings/local";
import { SystemModelStatus } from "./system-model-status";

const modelCatalog = vi.hoisted(() => ({
  error: null as Error | null,
  refetch: vi.fn(),
}));

vi.mock("@/core/models/hooks", () => ({
  useModels: () => ({
    models: [
      {
        name: "model-a",
        selection_id: "provider/model-a",
        display_name: "Model A",
      },
    ],
    isLoading: false,
    error: modelCatalog.error,
    refetch: modelCatalog.refetch,
  }),
}));
const auth = vi.hoisted(() => ({
  user: { actor_id: "person-a" },
  isLoading: false,
}));
vi.mock("@/providers/AuthProvider", () => ({ useAuth: () => auth }));
vi.mock("@/core/coder/api", async (importOriginal) => ({
  ...(await importOriginal()),
  getCoderRateLimits: vi.fn(),
  getCoderAccount: vi.fn(),
  getCoderUsage: vi.fn(),
  getCoderModelProfile: vi.fn(),
  getCoderModels: vi.fn(),
  updateCoderModelProfile: vi.fn(),
}));
beforeEach(() => {
  localStorage.clear();
  vi.resetAllMocks();
  modelCatalog.error = null;
  auth.user = { actor_id: "person-a" };
  auth.isLoading = false;
  vi.mocked(getCoderRateLimits).mockResolvedValue({ buckets: [] } as Awaited<
    ReturnType<typeof getCoderRateLimits>
  >);
  vi.mocked(getCoderUsage).mockResolvedValue({
    summary: { lifetime_tokens: null },
  } as Awaited<ReturnType<typeof getCoderUsage>>);
  vi.mocked(getCoderModels).mockResolvedValue({
    models: [{ id: "gpt-test", display_name: "Account Test Model" }],
  } as Awaited<ReturnType<typeof getCoderModels>>);
  vi.mocked(getCoderAccount).mockResolvedValue({
    account: { type: "chatgpt" },
  } as Awaited<ReturnType<typeof getCoderAccount>>);
  vi.mocked(getCoderModelProfile).mockResolvedValue({
    source: "codex_account",
    selected_model: "gpt-5.6-sol",
    effective_model: "gpt-5.6-sol",
    system_model: "gpt-5.6-sol",
    reasoning_effort: null,
    model_source: "role",
    compatible: true,
    compatibility_reason: null,
    provider: "codex_account",
    proxy_required: false,
  });
  vi.mocked(updateCoderModelProfile).mockResolvedValue({
    source: "follow_system",
    selected_model: "provider/model-a",
    effective_model: "provider/model-a",
    system_model: "provider/model-a",
    reasoning_effort: null,
    model_source: "role",
    compatible: true,
    compatibility_reason: null,
    provider: "echo_responses_proxy",
    proxy_required: true,
  });
});
function mount({ desktopRoot = false }: { desktopRoot?: boolean } = {}) {
  const onOpenSettings = vi.fn();
  const status = <SystemModelStatus onOpenSettings={onOpenSettings} />;
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {desktopRoot ? (
        <main className="macos-desktop-root">{status}</main>
      ) : (
        status
      )}
    </QueryClientProvider>,
  );
  return onOpenSettings;
}

it("loads subscription models and commits their selection through the shared profile", async () => {
  vi.mocked(updateCoderModelProfile).mockResolvedValue({
    ...(await getCoderModelProfile()),
    source: "codex_account",
    selected_model: "gpt-test",
    effective_model: "gpt-test",
  });
  mount();
  expect(getCoderModels).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: /模型与用量/ }));
  const option = await screen.findByRole("option", {
    name: "Account Test Model",
  });
  expect(option).toHaveValue("chatgpt/gpt-test");
  expect(option.closest("optgroup")).toHaveAttribute("label", "ChatGPT 订阅");
  await userEvent.selectOptions(
    screen.getByRole("combobox"),
    "chatgpt/gpt-test",
  );
  await waitFor(() =>
    expect(updateCoderModelProfile).toHaveBeenCalledWith({
      source: "codex_account",
      model: "gpt-test",
    }),
  );
  await waitFor(() =>
    expect(getLocalSettings().context.model_name).toBe("chatgpt/gpt-test"),
  );
});

it("keeps account choices usable when the Echo catalog fails", async () => {
  modelCatalog.error = new Error("Echo offline");
  mount();
  await userEvent.click(screen.getByRole("button", { name: /模型与用量/ }));
  await screen.findByRole("option", { name: "Account Test Model" });
  expect(screen.getByRole("combobox")).toBeEnabled();
  expect(
    screen.getByRole("option", { name: "Model A" }).closest("optgroup"),
  ).toBeDisabled();
});

it("retries the account model catalog separately from usage", async () => {
  vi.mocked(getCoderModels).mockRejectedValueOnce(new Error("catalog offline"));
  mount();
  await userEvent.click(screen.getByRole("button", { name: /模型与用量/ }));
  expect(
    await screen.findByText("账户模型目录暂不可用，额度信息不受影响。"),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "重试账户模型" }));
  expect(
    await screen.findByRole("option", { name: "Account Test Model" }),
  ).toBeInTheDocument();
});

it("does not fetch subscription choices without a connected account", async () => {
  vi.mocked(getCoderAccount).mockResolvedValue({ account: null } as Awaited<
    ReturnType<typeof getCoderAccount>
  >);
  mount();
  await userEvent.click(screen.getByRole("button", { name: /模型与用量/ }));
  await screen.findByText(/尚未连接 Codex 账户/);
  expect(getCoderModels).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("option", { name: "Account Test Model" }),
  ).not.toBeInTheDocument();
});

it("keeps the desktop model menu inside the desktop material root", async () => {
  const user = userEvent.setup();
  mount({ desktopRoot: true });
  await user.click(screen.getByRole("button", { name: /模型与用量/ }));
  const menu = document.querySelector('[data-slot="dropdown-menu-content"]');
  expect(menu).toHaveAttribute("data-liquid-surface", "thick-dark");
  expect(menu?.closest(".macos-desktop-root")).not.toBeNull();
});

it("offers an in-place retry when the model catalog is unavailable", async () => {
  modelCatalog.error = new Error("catalog offline");
  const user = userEvent.setup();
  mount();
  await user.click(screen.getByRole("button", { name: /模型与用量/ }));
  expect(
    screen.getByText("模型目录暂不可用，请检查连接后重试。"),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "重试" }));
  expect(modelCatalog.refetch).toHaveBeenCalledOnce();
});

it("adopts an externally committed Codex profile", async () => {
  mount();
  await waitFor(() =>
    expect(getLocalSettings().context.model_name).toBe("chatgpt/gpt-5.6-sol"),
  );
});
it("surfaces model execution readiness in the system status menu", async () => {
  vi.mocked(getCoderModelProfile).mockResolvedValueOnce({
    source: "follow_system",
    selected_model: "provider/model-a",
    effective_model: "provider/model-a",
    system_model: "provider/model-a",
    reasoning_effort: null,
    model_source: "system",
    compatible: false,
    compatibility_reason: "该模型不支持当前任务类型",
    provider: "provider",
    proxy_required: true,
    execution_available: false,
    execution_unavailable_reason: "上游服务未连接",
  });
  const user = userEvent.setup();
  mount();
  const trigger = screen.getByRole("button", { name: /模型与用量/ });
  await waitFor(() => expect(trigger).toHaveAccessibleName(/模型不可用/));
  await user.click(trigger);
  expect(screen.getByText("执行连接：模型不可用")).toBeInTheDocument();
  expect(screen.getByText("上游服务未连接")).toBeInTheDocument();
});
it("switches the shared default and shows account usage with remaining quota", async () => {
  vi.mocked(getCoderUsage).mockResolvedValue({
    summary: { lifetime_tokens: 1234 },
  } as Awaited<ReturnType<typeof getCoderUsage>>);
  vi.mocked(getCoderRateLimits).mockResolvedValue({
    buckets: [
      {
        limit_name: "Codex",
        primary: {
          used_percent: 25,
          remaining_percent: 75,
          window_duration_mins: 300,
          resets_at: 1800000000,
        },
        secondary: null,
      },
    ],
    reset_credits_available: null,
  } as Awaited<ReturnType<typeof getCoderRateLimits>>);
  const user = userEvent.setup();
  const openSettings = mount();
  expect(getCoderUsage).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: /模型与用量/ }));
  expect(await screen.findByText("已用 25% · 剩余 75%")).toBeInTheDocument();
  expect(screen.getByText("累计使用：1,234 tokens")).toBeInTheDocument();
  expect(screen.getByRole("progressbar")).toHaveAccessibleName(
    "Codex 5 小时已用额度",
  );
  await user.selectOptions(
    screen.getByLabelText("切换模型"),
    "provider/model-a",
  );
  await waitFor(() =>
    expect(getLocalSettings().context.model_name).toBe("provider/model-a"),
  );
  expect(updateCoderModelProfile).toHaveBeenCalledWith({
    source: "follow_system",
    model: "provider/model-a",
  });
  await user.click(screen.getByRole("button", { name: "打开模型设置…" }));
  expect(openSettings).toHaveBeenCalledOnce();
});
it("keeps the shared model unchanged when profile sync fails", async () => {
  vi.mocked(getCoderUsage).mockResolvedValue({
    summary: { lifetime_tokens: 0 },
  } as Awaited<ReturnType<typeof getCoderUsage>>);
  vi.mocked(getCoderRateLimits).mockResolvedValue({
    buckets: [],
    reset_credits_available: null,
  } as Awaited<ReturnType<typeof getCoderRateLimits>>);
  vi.mocked(updateCoderModelProfile).mockRejectedValueOnce(
    new Error("profile unavailable"),
  );
  const user = userEvent.setup();
  mount();
  await user.click(screen.getByRole("button", { name: /模型与用量/ }));
  await user.selectOptions(
    screen.getByLabelText("切换模型"),
    "provider/model-a",
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("模型同步失败");
  expect(getLocalSettings().context.model_name).toBe("chatgpt/gpt-5.6-sol");
  expect(screen.getByLabelText("切换模型")).toHaveValue("chatgpt/gpt-5.6-sol");
});
it("does not present unavailable usage as zero", async () => {
  vi.mocked(getCoderUsage).mockRejectedValue(new Error("offline"));
  vi.mocked(getCoderRateLimits).mockResolvedValue({
    buckets: [],
    reset_credits_available: null,
  });
  mount();
  await userEvent.click(screen.getByRole("button", { name: /模型与用量/ }));
  expect(await screen.findByText("累计使用：暂不可用")).toBeInTheDocument();
  expect(await screen.findByText(/服务未提供额度/)).toBeInTheDocument();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
});

it("shares fresh account usage and responds to account invalidation", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const keys = coderQueryKeys("person-a");
  client.setQueryData(keys.usage, { summary: { lifetime_tokens: 100 } });
  client.setQueryData(keys.rateLimits, {
    buckets: [],
    reset_credits_available: null,
  });
  vi.mocked(getCoderUsage).mockResolvedValue({
    summary: { lifetime_tokens: 200 },
  } as Awaited<ReturnType<typeof getCoderUsage>>);
  render(
    <QueryClientProvider client={client}>
      <SystemModelStatus onOpenSettings={vi.fn()} />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByRole("button", { name: /模型与用量/ }));
  expect(await screen.findByText("累计使用：100 tokens")).toBeInTheDocument();
  expect(getCoderUsage).not.toHaveBeenCalled();
  expect(getCoderRateLimits).not.toHaveBeenCalled();
  await client.invalidateQueries({ queryKey: keys.usage });
  expect(await screen.findByText("累计使用：200 tokens")).toBeInTheDocument();
});

it("does not reuse another user's cached usage after an account switch", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  for (const [principal, tokens] of [
    ["person-a", 111],
    ["person-b", 222],
  ] as const) {
    const keys = coderQueryKeys(principal);
    client.setQueryData(keys.usage, { summary: { lifetime_tokens: tokens } });
    client.setQueryData(keys.rateLimits, {
      buckets: [],
      reset_credits_available: null,
    });
  }
  const view = () => (
    <QueryClientProvider client={client}>
      <SystemModelStatus onOpenSettings={vi.fn()} />
    </QueryClientProvider>
  );
  const { rerender } = render(view());
  await userEvent.click(screen.getByRole("button", { name: /模型与用量/ }));
  expect(await screen.findByText("累计使用：111 tokens")).toBeInTheDocument();
  auth.user = { actor_id: "person-b" };
  rerender(view());
  expect(await screen.findByText("累计使用：222 tokens")).toBeInTheDocument();
  expect(screen.queryByText("累计使用：111 tokens")).not.toBeInTheDocument();
});

it.each([
  [null, "尚未连接 Codex 账户"],
  [{ type: "apiKey" }, "API Key 账户不提供订阅额度"],
])(
  "does not request subscription usage for account %j",
  async (account, message) => {
    vi.mocked(getCoderAccount).mockResolvedValue({ account } as Awaited<
      ReturnType<typeof getCoderAccount>
    >);
    mount();
    await userEvent.click(screen.getByRole("button", { name: /模型与用量/ }));
    expect(await screen.findByText(new RegExp(message))).toBeInTheDocument();
    expect(getCoderUsage).not.toHaveBeenCalled();
    expect(getCoderRateLimits).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "刷新用量" }));
    await waitFor(() => expect(getCoderAccount).toHaveBeenCalledTimes(2));
    expect(getCoderUsage).not.toHaveBeenCalled();
  },
);

it("hides cached usage when the connected account logs out", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const keys = coderQueryKeys("person-a");
  client.setQueryData(keys.account, { account: { type: "chatgpt" } });
  client.setQueryData(keys.usage, { summary: { lifetime_tokens: 500 } });
  client.setQueryData(keys.rateLimits, {
    buckets: [],
    reset_credits_available: null,
  });
  render(
    <QueryClientProvider client={client}>
      <SystemModelStatus onOpenSettings={vi.fn()} />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByRole("button", { name: /模型与用量/ }));
  expect(await screen.findByText("累计使用：500 tokens")).toBeInTheDocument();
  client.setQueryData(keys.account, { account: null });
  expect(await screen.findByText(/尚未连接 Codex 账户/)).toBeInTheDocument();
  expect(screen.queryByText("累计使用：500 tokens")).not.toBeInTheDocument();
  expect(getCoderUsage).not.toHaveBeenCalled();
});

it("reports account lookup failure without requesting usage", async () => {
  vi.mocked(getCoderAccount).mockRejectedValue(new Error("offline"));
  mount();
  await userEvent.click(screen.getByRole("button", { name: /模型与用量/ }));
  expect(
    await screen.findByText("账户连接暂不可用，请刷新重试。"),
  ).toBeInTheDocument();
  expect(getCoderUsage).not.toHaveBeenCalled();
  expect(getCoderRateLimits).not.toHaveBeenCalled();
});

it("does not present unknown duration or invalid token counts as zero usage", async () => {
  vi.mocked(getCoderUsage).mockResolvedValue({
    summary: { lifetime_tokens: Number.NaN },
  } as Awaited<ReturnType<typeof getCoderUsage>>);
  vi.mocked(getCoderRateLimits).mockResolvedValue({
    buckets: [
      {
        limit_name: "Test",
        primary: {
          used_percent: 25,
          remaining_percent: 75,
          window_duration_mins: 0,
          resets_at: 0,
        },
        secondary: null,
      },
    ],
  } as Awaited<ReturnType<typeof getCoderRateLimits>>);
  mount();
  await userEvent.click(screen.getByRole("button", { name: /模型与用量/ }));
  expect(await screen.findByText("累计使用：服务未提供")).toBeInTheDocument();
  expect(screen.getByText("周期未知额度")).toBeInTheDocument();
  expect(screen.queryByText(/0 小时/)).not.toBeInTheDocument();
  expect(screen.getByText("已用 25% · 剩余 75%")).toBeInTheDocument();
});
