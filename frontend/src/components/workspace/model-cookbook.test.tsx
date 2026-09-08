import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { ModelCookbook } from "./model-cookbook";
vi.mock("./local-ai-deployment", () => ({ LocalAiDeployment: () => null }));

const state = vi.hoisted(() => ({
  snapshot: {} as any,
  pull: vi.fn(),
  verify: vi.fn(),
  activate: vi.fn(),
  useCookbook: vi.fn(),
  error: null as Error | null,
}));
vi.mock("@/core/i18n/hooks", () => ({ useI18n: () => ({ locale: "zh-CN" }) }));
vi.mock("@/core/cookbook/use-cookbook", () => ({
  useCookbook: (...args: any[]) => {
    state.useCookbook(...args);
    return { snapshot: state.snapshot, isLoading: false, error: null };
  },
  useCookbookPull: () => ({
    pull: state.pull,
    verify: state.verify,
    activate: state.activate,
    pendingTag: null,
    error: state.error,
    activatedTag: null,
  }),
}));
beforeEach(() => {
  vi.clearAllMocks();
  state.error = null;
  state.snapshot = {
    hardware: { backend: "cpu", vram_gb: 8, ram_gb: 16, unified_memory: false },
    ollama_available: true,
    recommendations: [
      {
        tag: "fixture:1b",
        label: "Fixture",
        est_mem_gb: 2,
        verdict: "fits",
        installed: false,
      },
    ],
    pulls: {},
    verifications: {},
  };
});

it("offers an explicit network download and verification", async () => {
  render(<ModelCookbook />);
  await userEvent.click(screen.getByRole("button", { name: "下载并验证" }));
  expect(state.pull).toHaveBeenCalledWith("fixture:1b");
  expect(state.activate).not.toHaveBeenCalled();
});

it("an installed model still requires verification", async () => {
  state.snapshot.recommendations[0].installed = true;
  render(<ModelCookbook />);
  expect(
    screen.queryByRole("button", { name: "设为默认" }),
  ).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "本机验证" }));
  expect(state.verify).toHaveBeenCalledWith("fixture:1b");
});

it("only verified models offer default activation with capability results", async () => {
  state.snapshot.verifications["fixture:1b"] = {
    supports_tool_use: false,
    supports_vision: true,
    latency_ms: 123,
  };
  render(<ModelCookbook />);
  expect(screen.getByText(/工具未通过/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "设为默认" }));
  expect(state.activate).toHaveBeenCalledWith("fixture:1b");
});

it("shows preparation failures and permits a retry", () => {
  state.snapshot.pulls["fixture:1b"] = "error: 磁盘不足";
  render(<ModelCookbook />);
  expect(screen.getByRole("alert")).toHaveTextContent("磁盘不足");
  expect(screen.getByRole("button", { name: "下载并验证" })).toBeEnabled();
});

it("disables actions while verification is running", () => {
  state.snapshot.pulls["fixture:1b"] = "verifying";
  render(<ModelCookbook />);
  expect(screen.getByRole("button", { name: "验证中…" })).toBeDisabled();
});

it("changing context requests a new resource estimate", async () => {
  render(<ModelCookbook />);
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "估算上下文" }),
    "16384",
  );
  expect(state.useCookbook).toHaveBeenLastCalledWith(16384);
});
