import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  BROWSER_OPEN_URL_ACK_EVENT,
  BROWSER_OPEN_URL_REQUEST_EVENT,
  type BrowserOpenUrlAck,
  type BrowserOpenUrlRequest,
} from "@/components/browser/browser-store";

import { setLinkOpenTarget } from "@/core/settings/automation-preferences";
import {
  OPEN_ARTIFACT_EVENT,
  type OpenArtifactDetail,
} from "@/core/artifacts/open-artifact";

import { ArtifactLink } from "./artifact-link";

describe("ArtifactLink", () => {
  let acknowledge: (event: Event) => void;

  beforeEach(() => {
    window.localStorage.clear();
    window.location.hash = "#/workspace";
    acknowledge = (event) => {
      const request = (event as CustomEvent<BrowserOpenUrlRequest>).detail;
      window.dispatchEvent(
        new CustomEvent<BrowserOpenUrlAck>(BROWSER_OPEN_URL_ACK_EVENT, {
          detail: { requestId: request.requestId!, accepted: true },
        }),
      );
    };
    window.addEventListener(BROWSER_OPEN_URL_REQUEST_EVENT, acknowledge);
  });

  afterEach(() => {
    window.removeEventListener(BROWSER_OPEN_URL_REQUEST_EVENT, acknowledge);
  });

  it("routes ordinary artifact web links through the user's preference", () => {
    setLinkOpenTarget("in_app");
    render(
      <ArtifactLink href="https://example.com/report">Report</ArtifactLink>,
    );

    fireEvent.click(screen.getByRole("link", { name: "Report" }));

    expect(window.location.hash).toBe("#/browser");
  });

  it("keeps relative artifact links native", () => {
    render(<ArtifactLink href="/workspace/report">Local report</ArtifactLink>);

    expect(
      screen.getByRole("link", { name: "Local report" }),
    ).not.toHaveAttribute("target");
  });

  it("hands generated office files to the artifact workbench", () => {
    let opened: OpenArtifactDetail | null = null;
    const openArtifact = (event: Event) => {
      opened = (event as CustomEvent<OpenArtifactDetail>).detail;
      event.preventDefault();
    };
    window.addEventListener(OPEN_ARTIFACT_EVENT, openArtifact);
    render(<ArtifactLink href="out/deck.pptx">Deck</ArtifactLink>);

    fireEvent.click(screen.getByRole("link", { name: "Deck" }));

    expect(opened).toEqual({ path: "workspace-output:final:out/deck.pptx" });
    window.removeEventListener(OPEN_ARTIFACT_EVENT, openArtifact);
  });
});
