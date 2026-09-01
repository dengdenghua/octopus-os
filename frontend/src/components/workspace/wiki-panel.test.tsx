import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { WikiPanel } from "./wiki-panel";

const hooks = vi.hoisted(() => ({
  useWikiStatus: vi.fn(),
  useWikiDocs: vi.fn(),
  useWikiDocument: vi.fn(),
  useGenerateWiki: vi.fn(),
  useUpdateWiki: vi.fn(),
}));

vi.mock("@/core/wiki/hooks", () => hooks);
vi.mock("@/core/workspace/use-active-project-root", () => ({
  useActiveProjectRoot: () => "/Users/eve/echo",
  activateProjectRoot: vi.fn(),
}));
vi.mock("@/components/ai-elements/streamdown-host", () => ({
  default: ({ children }: { children: string }) => <div>{children}</div>,
}));

describe("<WikiPanel />", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    hooks.useWikiStatus.mockReturnValue({
      data: {
        exists: true,
        status: "current",
        generated_at: "2026-08-04T08:00:00Z",
        files_analyzed: 42,
        generated_files: ["00-overview.md"],
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      refetch: vi.fn(),
    });
    hooks.useWikiDocs.mockReturnValue({
      data: {
        docs: [{ path: "00-overview.md", name: "00-overview", size: 2048 }],
        lang: "zh",
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      refetch: vi.fn(),
    });
    hooks.useWikiDocument.mockReturnValue({
      data: {
        path: "00-overview.md",
        content: "# Echo 架构总览",
        size: 2048,
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    hooks.useGenerateWiki.mockReturnValue({
      isPending: false,
      mutateAsync: vi.fn(),
    });
    hooks.useUpdateWiki.mockReturnValue({
      isPending: false,
      mutateAsync: vi.fn(),
    });
  });

  it("shows real wiki status, documents and rendered content", async () => {
    renderWithProviders(<WikiPanel />);

    expect(screen.getByText("项目 Wiki")).toBeInTheDocument();
    expect(screen.getByText("1 篇知识页 · 42 个文件")).toBeInTheDocument();
    expect(screen.getByText("已同步")).toBeInTheDocument();
    expect(screen.getByText("00-overview")).toBeInTheDocument();
    expect(await screen.findByText("# Echo 架构总览")).toBeInTheDocument();
  });
});
