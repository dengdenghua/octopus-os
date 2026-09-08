import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ArtifactLoadError } from "@/core/artifacts/loader";
import { renderWithProviders } from "@/test/harness";

import { PreviewPane } from "../agent-workbench-panel";
import { ArtifactFileDetail } from "./artifact-file-detail";
import { ArtifactInlinePreview } from "./artifact-file-list";

const contentState = vi.hoisted(() => ({
  error: null as unknown,
  refetch: vi.fn(),
}));
vi.mock("@/core/artifacts/hooks", () => ({
  useArtifactContent: () => ({
    content: "OLD ATTACHMENT CONTENT",
    error: contentState.error,
    refetch: contentState.refetch,
    isLoading: false,
  }),
  useArtifactDiff: () => ({ isDiffAvailable: false }),
}));
vi.mock("@/core/streamdown", () => ({ useStreamdownPlugins: () => ({}) }));
vi.mock("./context", () => ({
  useArtifacts: () => ({
    artifacts: ["/authorized/report.md"],
    select: vi.fn(),
    clearSelection: vi.fn(),
  }),
}));
vi.mock("./use-install-skill", () => ({
  useInstallSkill: () => ({ installingFile: null, install: vi.fn() }),
}));
vi.mock("../messages/context", () => ({
  useThread: () => ({ isMock: false, thread: { isLoading: false } }),
}));

describe("source read failure surfaces", () => {
  beforeEach(() => contentState.refetch.mockClear());
  it.each(["detail", "inline", "workbench"])(
    "%s shows the source failure instead of stale content or an empty editor",
    (surface) => {
      contentState.error = new ArtifactLoadError(
        403,
        "/private/should-not-appear",
      );
      const file = "/authorized/report.md";
      renderWithProviders(
        surface === "detail" ? (
          <ArtifactFileDetail filepath={file} threadId="t1" />
        ) : surface === "inline" ? (
          <ArtifactInlinePreview files={[file]} threadId="t1" />
        ) : (
          <PreviewPane
            filepath={file}
            threadId="t1"
            streamdownPlugins={{}}
            onBack={vi.fn()}
          />
        ),
        { locale: "zh-CN" },
      );
      expect(screen.getByRole("alert")).toHaveTextContent(
        "当前任务有权访问所在目录",
      );
      expect(
        screen.queryByText("OLD ATTACHMENT CONTENT"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText("/private/should-not-appear"),
      ).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "重新加载预览" }));
      expect(contentState.refetch).toHaveBeenCalledTimes(1);
    },
  );
});
