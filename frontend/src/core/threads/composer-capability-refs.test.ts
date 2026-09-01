import { describe, expect, it } from "vitest";

import {
  addComposerCapabilityRef,
  parseComposerDraft,
  removeComposerCapabilityRef,
  serializeComposerDraft,
  setComposerDraftMode,
} from "./composer-capability-refs";

describe("composer capability references", () => {
  it("parses a mode, capability references, and task body", () => {
    expect(
      parseComposerDraft(
        "/mode goal\n@Browser @plugin:seedance @skill:video-generate\nMake a launch clip",
      ),
    ).toEqual({
      mode: "goal",
      refs: [
        { type: "surface", id: "browser" },
        { type: "plugin", id: "seedance" },
        { type: "skill", id: "video-generate" },
      ],
      body: "Make a launch clip",
    });
  });

  it("deduplicates references and preserves the canonical wire format", () => {
    let draft = addComposerCapabilityRef("Audit this", {
      type: "plugin",
      id: "github",
    });
    draft = addComposerCapabilityRef(draft, {
      type: "plugin",
      id: "github",
    });
    draft = addComposerCapabilityRef(draft, {
      type: "skill",
      id: "code-review",
    });

    expect(draft).toBe("@plugin:github @skill:code-review\nAudit this");
  });

  it("switches Browser and Chrome as mutually exclusive surfaces", () => {
    let draft = addComposerCapabilityRef("@Browser\nInspect this page", {
      type: "surface",
      id: "chrome",
    });

    expect(draft).toBe("@Chrome\nInspect this page");
    expect(parseComposerDraft(draft).refs).toEqual([
      { type: "surface", id: "chrome" },
    ]);

    draft = addComposerCapabilityRef(draft, {
      type: "surface",
      id: "browser",
    });
    expect(draft).toBe("@Browser\nInspect this page");
  });

  it("removes references without losing mode or body", () => {
    const raw = serializeComposerDraft({
      mode: "plan",
      refs: [
        { type: "plugin", id: "github" },
        { type: "surface", id: "browser" },
      ],
      body: "Inspect the issue",
    });
    expect(
      removeComposerCapabilityRef(raw, { type: "plugin", id: "github" }),
    ).toBe("/mode plan\n@Browser\nInspect the issue");
  });

  it("switches mutually exclusive modes while retaining capability refs", () => {
    expect(setComposerDraftMode("/mode spec\n@Browser\nDraft it", "goal")).toBe(
      "/mode goal\n@Browser\nDraft it",
    );
  });

  it("serializes Milestone mode as the real Project OS run command", () => {
    const projectDraft = setComposerDraftMode(
      "/mode goal\n@Browser\nShip the release",
      "project",
    );

    expect(projectDraft).toBe("/project run\n@Browser\nShip the release");
    expect(parseComposerDraft(projectDraft)).toEqual({
      mode: "project",
      refs: [{ type: "surface", id: "browser" }],
      body: "Ship the release",
    });
  });

  it("does not reinterpret Project OS report commands as task mode", () => {
    expect(parseComposerDraft("/project report")).toEqual({
      mode: undefined,
      refs: [],
      body: "/project report",
    });
  });

  it("preserves ordinary draft spacing and trailing spaces while typing", () => {
    expect(parseComposerDraft("  draft with trailing  ").body).toBe(
      "  draft with trailing  ",
    );
    expect(parseComposerDraft("/mode plan\ndraft with trailing  ").body).toBe(
      "draft with trailing  ",
    );
  });
});
