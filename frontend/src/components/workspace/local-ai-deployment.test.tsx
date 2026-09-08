import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LocalAiDeployment } from "./local-ai-deployment";

const mocks = vi.hoisted(() => ({ activate: vi.fn(), principal: "admin-a" }));
vi.mock("@/providers/AuthProvider", () => ({
  useAuth: () => ({ user: { actor_id: mocks.principal } }),
}));
vi.mock("@/core/auth/api", () => ({
  jsonAuthHeaders: () => ({ "Content-Type": "application/json" }),
}));
vi.mock("@/core/cookbook/use-cookbook", () => ({
  useCookbookPull: () => ({
    activate: mocks.activate,
    pendingTag: null,
    activatedTag: null,
    error: null,
  }),
}));

const plan = {
  plan_id: "plan-a",
  tag: "fixture:1b",
  label: "Fixture",
  model_memory_gb: 2,
  runtime_download_bytes: 1024 ** 3,
  model_estimate_bytes: 2 * 1024 ** 3,
  free_disk_bytes: 100 * 1024 ** 3,
  required_disk_bytes: 26 * 1024 ** 3,
  storage_path: "D:/echo/data/local-ai",
};
beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  mocks.principal = "admin-a";
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (url: string) =>
        new Response(
          JSON.stringify(
            url.endsWith("/plan")
              ? plan
              : url.endsWith("/start")
                ? { job_id: "job-a", tag: "fixture:1b", stage: "installing" }
                : { stage: "idle" },
          ),
        ),
    ),
  );
});
afterEach(() => vi.unstubAllGlobals());

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const ui = () => (
    <QueryClientProvider client={client}>
      <LocalAiDeployment tag="fixture:1b" disabled={false} />
    </QueryClientProvider>
  );
  return { ...render(ui()), client, ui };
}

it("only downloads after reviewing the path, estimates and confirming", async () => {
  mount();
  await userEvent.click(screen.getByRole("button", { name: "检查部署方案" }));
  expect(
    await screen.findByText(/D:\/echo\/data\/local-ai/),
  ).toBeInTheDocument();
  expect(screen.getByText(/模型预计 2.0 GiB/)).toBeInTheDocument();
  expect(
    vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith("/start")),
  ).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "确认部署" }));
  expect(await screen.findByText(/安装运行环境 · fixture/)).toBeInTheDocument();
  expect(mocks.activate).not.toHaveBeenCalled();
});

it("uses the confirmed job consent exactly once after verification", async () => {
  const { client } = mount();
  await userEvent.click(screen.getByRole("button", { name: "检查部署方案" }));
  await userEvent.click(
    await screen.findByRole("button", { name: "确认部署" }),
  );
  await screen.findByText(/安装运行环境 · fixture/);
  act(() =>
    client.setQueryData(["local-ai-deployment", "admin-a"], {
      job_id: "job-a",
      tag: "fixture:1b",
      stage: "ready",
    }),
  );
  await waitFor(() =>
    expect(mocks.activate).toHaveBeenCalledExactlyOnceWith("fixture:1b"),
  );
  expect(sessionStorage.getItem("local-ai-default:admin-a")).toBeNull();
});

it("resumes this user's consent on return to settings", async () => {
  sessionStorage.setItem("local-ai-default:admin-a", "job-a");
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            job_id: "job-a",
            tag: "fixture:1b",
            stage: "ready",
          }),
        ),
    ),
  );
  mount();
  await waitFor(() =>
    expect(mocks.activate).toHaveBeenCalledExactlyOnceWith("fixture:1b"),
  );
});

it("does not activate a failed job or another user's consent", async () => {
  sessionStorage.setItem("local-ai-default:admin-b", "job-a");
  const { client } = mount();
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  act(() =>
    client.setQueryData(["local-ai-deployment", "admin-a"], {
      job_id: "job-a",
      tag: "fixture:1b",
      stage: "error",
      error: "disk full",
    }),
  );
  expect(await screen.findByText("disk full")).toBeInTheDocument();
  act(() =>
    client.setQueryData(["local-ai-deployment", "admin-a"], {
      job_id: "job-a",
      tag: "fixture:1b",
      stage: "ready",
    }),
  );
  expect(
    await screen.findByRole("button", { name: "设为默认" }),
  ).toBeInTheDocument();
  expect(mocks.activate).not.toHaveBeenCalled();
});

it("honors an unchecked default option", async () => {
  const { client } = mount();
  await userEvent.click(screen.getByRole("button", { name: "检查部署方案" }));
  await userEvent.click(await screen.findByRole("checkbox"));
  await userEvent.click(screen.getByRole("button", { name: "确认部署" }));
  await screen.findByText(/安装运行环境 · fixture/);
  act(() =>
    client.setQueryData(["local-ai-deployment", "admin-a"], {
      job_id: "job-a",
      tag: "fixture:1b",
      stage: "ready",
    }),
  );
  expect(
    await screen.findByRole("button", { name: "设为默认" }),
  ).toBeInTheDocument();
  expect(mocks.activate).not.toHaveBeenCalled();
});
