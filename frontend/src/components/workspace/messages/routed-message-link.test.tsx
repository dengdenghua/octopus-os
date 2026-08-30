import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import {
  OPEN_ARTIFACT_EVENT,
  type OpenArtifactDetail,
} from "@/core/artifacts/open-artifact";

import { RoutedMessageLink } from "./message-list-item";

it("opens a generated Office link in the current artifact workbench", () => {
  let opened: OpenArtifactDetail | null = null;
  const handleOpen = (event: Event) => {
    opened = (event as CustomEvent<OpenArtifactDetail>).detail;
    event.preventDefault();
  };
  window.addEventListener(OPEN_ARTIFACT_EVENT, handleOpen);
  render(<RoutedMessageLink href="out/deck.pptx">下载 PPT</RoutedMessageLink>);

  fireEvent.click(screen.getByRole("link", { name: "下载 PPT" }));

  expect(opened).toEqual({ path: "workspace-output:final:out/deck.pptx" });
  window.removeEventListener(OPEN_ARTIFACT_EVENT, handleOpen);
});
