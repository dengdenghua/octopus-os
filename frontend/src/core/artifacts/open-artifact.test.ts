import { describe, expect, it } from "vitest";

import {
  artifactRefFromMarkdownHref,
  dispatchOpenArtifact,
  OPEN_ARTIFACT_EVENT,
} from "./open-artifact";

describe("markdown artifact handoff", () => {
  it("maps generated output links into scoped workspace artifacts", () => {
    expect(artifactRefFromMarkdownHref("out/deck.pptx")).toBe(
      "workspace-output:final:out/deck.pptx",
    );
    expect(artifactRefFromMarkdownHref("output/final/reports/model.xlsx")).toBe(
      "workspace-output:final:reports/model.xlsx",
    );
    expect(
      artifactRefFromMarkdownHref(
        "/api/threads/t1/outputs/report.pdf?area=final&download=true",
      ),
    ).toBe("workspace-output:final:report.pdf");
  });

  it("does not hijack external or ambiguous relative links", () => {
    expect(
      artifactRefFromMarkdownHref("https://example.com/report.pdf"),
    ).toBeNull();
    expect(artifactRefFromMarkdownHref("docs/report.pdf")).toBeNull();
    expect(artifactRefFromMarkdownHref("https://example.com")).toBeNull();
  });

  it("reports whether the current workspace accepted the open request", () => {
    const handler = (event: Event) => event.preventDefault();
    window.addEventListener(OPEN_ARTIFACT_EVENT, handler);
    expect(dispatchOpenArtifact("workspace-output:final:deck.pptx")).toBe(true);
    window.removeEventListener(OPEN_ARTIFACT_EVENT, handler);
    expect(dispatchOpenArtifact("workspace-output:final:deck.pptx")).toBe(
      false,
    );
  });
});
