import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";

import { renderWithProviders } from "@/test/harness";

import { ChatBox } from "./chat-box";

const mocks = vi.hoisted(() => ({
  deselect: vi.fn(),
  selectArtifact: vi.fn(),
  setArtifacts: vi.fn(),
  setArtifactsOpen: vi.fn(),
  threadArtifacts: [] as string[],
}));

vi.mock("@/core/artifacts/use-workspace-artifacts", () => ({
  useWorkspaceArtifacts: () => ({ data: ["workspace/report.md"] }),
}));

vi.mock("../artifacts", () => ({
  ArtifactPanel: () => <div>artifact drawer</div>,
  useArtifacts: () => ({
    artifacts: ["workspace/report.md"],
    open: false,
    autoOpen: true,
    selectedArtifact: null,
    setOpen: mocks.setArtifactsOpen,
    setArtifacts: mocks.setArtifacts,
    select: mocks.selectArtifact,
    deselect: mocks.deselect,
  }),
}));

vi.mock("../messages/context", () => ({
  useThread: () => ({
    thread: {
      isLoading: false,
      values: { artifacts: mocks.threadArtifacts },
    },
  }),
}));

describe("ChatBox artifact panel ownership", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.threadArtifacts = [];
  });

  it("does not auto-open legacy artifact state in external mode", async () => {
    renderWithProviders(
      <ChatBox artifactPanelMode="external" threadId="thread-1">
        <div>conversation</div>
      </ChatBox>,
    );

    expect(screen.getByText("conversation")).toBeInTheDocument();
    await waitFor(() => expect(mocks.setArtifacts).toHaveBeenCalled());
    expect(mocks.setArtifactsOpen).not.toHaveBeenCalled();
    expect(screen.queryByText("artifact drawer")).not.toBeInTheDocument();
  });

  it("filters internal planning files recorded in thread history", async () => {
    mocks.threadArtifacts = [
      "workspace-output:final:plan.md",
      "workspace-output:final:US10792461B2-full.jsonl",
      "workspace-output:final:final-report.md",
    ];

    renderWithProviders(
      <ChatBox artifactPanelMode="external" threadId="thread-1">
        <div>conversation</div>
      </ChatBox>,
    );

    await waitFor(() => expect(mocks.setArtifacts).toHaveBeenCalled());
    const updater = mocks.setArtifacts.mock.calls.at(-1)?.[0] as (
      current: string[],
    ) => string[];
    expect(updater([])).toEqual([
      "workspace/report.md",
      "workspace-output:final:final-report.md",
    ]);
  });

  it("preserves auto-open behavior for the default drawer owner", async () => {
    renderWithProviders(
      <ChatBox threadId="thread-1">
        <div>conversation</div>
      </ChatBox>,
    );

    await waitFor(() =>
      expect(mocks.setArtifactsOpen).toHaveBeenCalledWith(true),
    );
    expect(screen.getByText("artifact drawer")).toBeInTheDocument();
  });
});
