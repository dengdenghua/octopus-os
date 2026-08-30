import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MemoryData } from "@/core/memory/types";
import { renderWithProviders } from "@/test/harness";

import MemorySettingsPage, {
  MAX_MEMORY_IMPORT_BYTES,
  isImportedMemory,
} from "./memory-settings-page";

const mocks = vi.hoisted(() => ({
  memoryState: {} as Record<string, unknown>,
  configState: {} as Record<string, unknown>,
  refetchConfig: vi.fn(),
  updateConfig: { isPending: false, mutateAsync: vi.fn() },
  clearMemory: { isPending: false, mutateAsync: vi.fn() },
  createFact: { isPending: false, mutateAsync: vi.fn() },
  deleteFact: { isPending: false, mutateAsync: vi.fn() },
  importMemory: { isPending: false, mutateAsync: vi.fn() },
  updateFact: { isPending: false, mutateAsync: vi.fn() },
  searchMemory: vi.fn(),
  exportMemory: vi.fn(),
}));

vi.mock("@/core/memory/hooks", () => ({
  useMemory: () => mocks.memoryState,
  useMemoryConfig: () => mocks.configState,
  useUpdateMemoryConfig: () => mocks.updateConfig,
  useClearMemory: () => mocks.clearMemory,
  useCreateMemoryFact: () => mocks.createFact,
  useDeleteMemoryFact: () => mocks.deleteFact,
  useImportMemory: () => mocks.importMemory,
  useUpdateMemoryFact: () => mocks.updateFact,
}));

vi.mock("@/core/memory/api", () => ({
  searchMemory: mocks.searchMemory,
  exportMemory: mocks.exportMemory,
}));

vi.mock("@/core/streamdown", () => ({
  useStreamdownPlugins: () => ({}),
}));

vi.mock("@/components/ai-elements/streamdown-host", () => ({
  default: ({ children }: { children: string }) => <div>{children}</div>,
}));

const EMPTY_MEMORY: MemoryData = {
  version: "1",
  lastUpdated: "2026-07-20T00:00:00Z",
  user: {
    workContext: { summary: "", updatedAt: "" },
    personalContext: { summary: "", updatedAt: "" },
    topOfMind: { summary: "", updatedAt: "" },
  },
  history: {
    recentMonths: { summary: "", updatedAt: "" },
    earlierContext: { summary: "", updatedAt: "" },
    longTermBackground: { summary: "", updatedAt: "" },
  },
  facts: [],
};

describe("MemorySettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.memoryState = {
      memory: EMPTY_MEMORY,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isRefreshing: false,
    };
    mocks.configState = {
      config: {
        enabled: true,
        storage_path: "memory.json",
        auto_capture_enabled: true,
        debounce_seconds: 1,
        max_facts: 100,
        fact_confidence_threshold: 0.5,
        injection_enabled: true,
        max_injection_tokens: 1_000,
      },
      isLoading: false,
      error: null,
      refetch: mocks.refetchConfig,
      isRefreshing: false,
    };
    mocks.searchMemory.mockResolvedValue([]);
  });

  it("does not portray unknown memory controls as enabled", () => {
    mocks.configState = {
      config: null,
      isLoading: true,
      error: null,
      refetch: mocks.refetchConfig,
      isRefreshing: false,
    };

    renderWithProviders(<MemorySettingsPage />, { locale: "zh-CN" });

    expect(screen.getByRole("status")).toHaveTextContent("正在读取记忆开关");
    for (const control of screen.getAllByRole("switch")) {
      expect(control).not.toBeChecked();
      expect(control).toBeDisabled();
    }
  });

  it("offers an in-place retry when memory controls fail to load", async () => {
    const user = userEvent.setup();
    mocks.configState = {
      config: null,
      isLoading: false,
      error: new Error("raw backend details"),
      refetch: mocks.refetchConfig,
      isRefreshing: false,
    };

    renderWithProviders(<MemorySettingsPage />, { locale: "zh-CN" });

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("暂时无法读取记忆开关");
    expect(alert).not.toHaveTextContent("raw backend details");
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(mocks.refetchConfig).toHaveBeenCalledTimes(1);
  });

  it("names search and filters and disables destructive clearing when empty", () => {
    renderWithProviders(<MemorySettingsPage />, { locale: "zh-CN" });

    expect(screen.getByRole("textbox", { name: "搜索记忆" })).toBeVisible();
    expect(screen.getByRole("group", { name: "筛选记忆类型" })).toBeVisible();
    expect(screen.getByRole("button", { name: "清空所有记忆" })).toBeDisabled();
  });

  it("explains fact editing and only enables save for valid input", async () => {
    const user = userEvent.setup();
    renderWithProviders(<MemorySettingsPage />, { locale: "zh-CN" });

    await user.click(screen.getByRole("button", { name: "添加记忆" }));
    const dialog = screen.getByRole("dialog", { name: "添加新记忆" });
    expect(dialog).toHaveTextContent("保存一条可检索的事实记忆");
    const save = screen.getByRole("button", { name: "保存记忆" });
    expect(save).toBeDisabled();

    await user.type(screen.getByLabelText("内容"), "偏好使用深色主题");
    expect(save).toBeEnabled();
    await user.clear(screen.getByLabelText("置信度"));
    await user.type(screen.getByLabelText("置信度"), "2");
    expect(screen.getByLabelText("置信度")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(save).toBeDisabled();
  });

  it("ignores a stale backend search response after the query changes", async () => {
    const user = userEvent.setup();
    const appleFact = {
      id: "apple",
      content: "Apple preference",
      category: "preference",
      confidence: 0.9,
      createdAt: "2026-07-20T00:00:00Z",
      source: "manual",
      relevance: 1,
    };
    const bananaFact = {
      ...appleFact,
      id: "banana",
      content: "Banana preference",
    };
    mocks.memoryState = {
      ...mocks.memoryState,
      memory: { ...EMPTY_MEMORY, facts: [appleFact, bananaFact] },
    };
    let resolveApple: ((value: (typeof appleFact)[]) => void) | undefined;
    let resolveBanana: ((value: (typeof bananaFact)[]) => void) | undefined;
    mocks.searchMemory.mockImplementation((query: string) => {
      return new Promise((resolve) => {
        if (query === "apple") resolveApple = resolve;
        if (query === "banana") resolveBanana = resolve;
      });
    });
    renderWithProviders(<MemorySettingsPage />, { locale: "zh-CN" });

    const search = screen.getByRole("textbox", { name: "搜索记忆" });
    await user.type(search, "apple");
    await waitFor(() =>
      expect(mocks.searchMemory).toHaveBeenCalledWith("apple", 100),
    );
    await user.clear(search);
    await user.type(search, "banana");
    await waitFor(() =>
      expect(mocks.searchMemory).toHaveBeenCalledWith("banana", 100),
    );

    await act(async () => resolveApple?.([appleFact]));
    expect(screen.queryByText("Apple preference")).not.toBeInTheDocument();
    expect(screen.getByText("Banana preference")).toBeInTheDocument();

    await act(async () => resolveBanana?.([bananaFact]));
    expect(screen.getByText("Banana preference")).toBeInTheDocument();
  });
});

describe("memory import validation", () => {
  it("accepts a well-formed export and rejects unsafe confidence or empty facts", () => {
    const valid = {
      ...EMPTY_MEMORY,
      facts: [
        {
          id: "fact-1",
          content: "Use dark mode",
          category: "preference",
          confidence: 0.9,
          createdAt: "2026-07-20T00:00:00Z",
          source: "manual",
        },
      ],
    };
    expect(isImportedMemory(valid)).toBe(true);
    expect(
      isImportedMemory({
        ...valid,
        facts: [{ ...valid.facts[0], confidence: 1.5 }],
      }),
    ).toBe(false);
    expect(
      isImportedMemory({
        ...valid,
        facts: [{ ...valid.facts[0], content: "   " }],
      }),
    ).toBe(false);
    expect(
      isImportedMemory({
        ...valid,
        facts: [{ ...valid.facts[0], createdAt: "not-a-date" }],
      }),
    ).toBe(false);
    expect(
      isImportedMemory({
        ...valid,
        facts: [valid.facts[0], { ...valid.facts[0] }],
      }),
    ).toBe(false);
    expect(MAX_MEMORY_IMPORT_BYTES).toBe(5 * 1024 * 1024);
  });
});
