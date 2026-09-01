import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

import { MemoryAssetsPanel } from "./memory-assets-panel";

const hooks = vi.hoisted(() => ({
  useMemoryAssets: vi.fn(),
  useMemoryAssetTrace: vi.fn(),
  useUpdateMemoryFact: vi.fn(),
}));

vi.mock("@/core/memory/hooks", () => hooks);

const asset = {
  id: "asset-1",
  asset_type: "atom" as const,
  layer: "L1" as const,
  title: "发布审核规则",
  content: "每次发布必须经过 reviewer 审核",
  owner: "local-user",
  visibility: "team" as const,
  status: "active" as const,
  version: 2,
  scope: "project",
  confidence: 0.92,
  created_at: "2026-08-04T08:00:00Z",
  updated_at: "2026-08-04T09:00:00Z",
  team_id: "echo-core",
  agent_id: "",
  project: "echo",
  allowed_users: [],
  allowed_roles: ["reviewer"],
  allowed_agents: ["release-agent"],
  tags: ["release"],
  provenance: {
    source_type: "conversation",
    source_id: "thread-42",
    source_uri: "",
    captured_at: "2026-08-04T08:00:00Z",
    parent_ids: ["conversation-1"],
    evidence: "用户确认发布前需要审核。",
  },
};

describe("<MemoryAssetsPanel />", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    hooks.useMemoryAssets.mockReturnValue({
      data: { items: [asset], count: 1 },
      isLoading: false,
      isFetching: false,
      isError: false,
      refetch: vi.fn(),
    });
    hooks.useMemoryAssetTrace.mockReturnValue({
      data: {
        asset_id: asset.id,
        layer: asset.layer,
        source: asset.provenance,
        parent_ids: asset.provenance.parent_ids,
        trace_complete: true,
      },
      isLoading: false,
    });
    hooks.useUpdateMemoryFact.mockReturnValue({
      isPending: false,
      mutateAsync: vi.fn(),
    });
  });

  it("shows governed memory assets and summary counts", () => {
    renderWithProviders(<MemoryAssetsPanel />);

    expect(screen.getByText("发布审核规则")).toBeInTheDocument();
    expect(screen.getByText("当前资产")).toBeInTheDocument();
    expect(screen.getByText("可追溯")).toBeInTheDocument();
    expect(screen.getByText("已共享")).toBeInTheDocument();
    expect(screen.getByText("团队")).toBeInTheDocument();
  });

  it("opens source and access details from an asset row", async () => {
    renderWithProviders(<MemoryAssetsPanel />);

    fireEvent.click(screen.getByRole("button", { name: /发布审核规则/ }));

    expect(await screen.findByText("访问与装备")).toBeInTheDocument();
    expect(screen.getByText("来源追溯")).toBeInTheDocument();
    expect(screen.getByText("thread-42")).toBeInTheDocument();
    expect(screen.getByText("用户确认发布前需要审核。")).toBeInTheDocument();
    expect(screen.getByText("release-agent")).toBeInTheDocument();
  });
});
