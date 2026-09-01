import { screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

vi.mock("@/core/artifacts/hooks", () => ({
  useArtifactContent: () => ({
    content: undefined,
    error: null,
    isLoading: false,
    url: undefined,
  }),
}));

vi.mock("@/components/workspace/artifacts/artifact-file-detail", () => ({
  OfficePreview: (
    props: ComponentProps<"div"> & {
      displayPath: string;
      filepath: string;
      kind: string;
    },
  ) => (
    <div
      data-testid="office-preview"
      data-display-path={props.displayPath}
      data-filepath={props.filepath}
      data-kind={props.kind}
    />
  ),
}));

import { PreviewPane } from "./agent-workbench-panel";

it("uses the Office preview inside the production workbench artifact pane", () => {
  renderWithProviders(
    <PreviewPane
      filepath="workspace-output:final:out/deck.pptx"
      onBack={() => undefined}
      streamdownPlugins={{ remarkPlugins: [], rehypePlugins: [] }}
      threadId="thread-1"
    />,
    { locale: "zh-CN" },
  );

  expect(screen.getByTestId("office-preview")).toHaveAttribute(
    "data-filepath",
    "workspace-output:final:out/deck.pptx",
  );
  expect(screen.getByTestId("office-preview")).toHaveAttribute(
    "data-kind",
    "presentation",
  );
});
