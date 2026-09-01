import { screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/harness";

vi.mock("@/core/artifacts/hooks", () => ({
  useArtifactContent: () => ({
    content: "",
    isLoading: false,
    url: undefined,
  }),
}));

vi.mock("../messages/context", () => ({
  useThread: () => ({ isMock: false, thread: { isLoading: false } }),
}));

vi.mock("./artifact-file-detail", () => ({
  OfficePreview: ({
    displayPath,
    kind,
  }: {
    displayPath: string;
    kind: string;
  }) => (
    <div data-testid="office-inline-preview" data-kind={kind}>
      {displayPath}
    </div>
  ),
}));

import { ArtifactInlinePreview } from "./artifact-file-list";

it("surfaces office and PDF files in the preview tab", () => {
  renderWithProviders(
    <ArtifactInlinePreview
      files={[
        "workspace-output:final:deck.pptx",
        "workspace-output:final:model.xlsx",
        "workspace-output:final:report.pdf",
        "workspace-output:final:notes.txt",
      ]}
      threadId="thread-1"
    />,
    { locale: "zh-CN" },
  );

  const previews = screen.getAllByTestId("office-inline-preview");
  expect(previews).toHaveLength(3);
  expect(previews[0]).toHaveTextContent("deck.pptx");
  expect(previews[0]).toHaveAttribute("data-kind", "presentation");
  expect(previews[1]).toHaveTextContent("model.xlsx");
  expect(previews[1]).toHaveAttribute("data-kind", "spreadsheet");
  expect(previews[2]).toHaveTextContent("report.pdf");
  expect(previews[2]).toHaveAttribute("data-kind", "pdf");
  expect(screen.queryByText("notes.txt")).not.toBeInTheDocument();
});
