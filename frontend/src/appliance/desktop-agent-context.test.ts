import { expect, it } from "vitest";
import { desktopAgentDraft } from "./desktop-agent-context";
import { parseDesktopAction } from "./desktop-actions";

it("keeps a photo's exact path and query in its return link", () => {
  const draft = desktopAgentDraft({
    app: "photos",
    kind: "photo",
    path: "旅行/a & b.jpg",
    query: "日落",
  });
  const href = draft.match(/\]\(([^)]+)\)/)?.[1];
  expect(parseDesktopAction(href!.split("?")[1]!)).toEqual({
    type: "photos.reveal",
    path: "旅行/a & b.jpg",
    query: "日落",
  });
});
