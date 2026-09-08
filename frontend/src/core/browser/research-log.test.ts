import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ actor: "alice" }));
vi.mock("@/core/auth/api", () => ({
  currentActorId: () => identity.actor,
}));

import {
  browserResearchLogStorageKey,
  readBrowserResearchLog,
  writeBrowserResearchLog,
} from "./research-log";

describe("browser research log storage", () => {
  beforeEach(() => {
    identity.actor = "alice";
    sessionStorage.clear();
  });

  it("round-trips valid entries in the actor namespace", () => {
    const entry = {
      id: "entry-1",
      createdAt: 1,
      platform: "Gemini",
      title: "Release",
      note: "A useful change",
      url: "https://example.com",
    };
    writeBrowserResearchLog([entry]);

    expect(sessionStorage.getItem(browserResearchLogStorageKey())).toContain(
      "entry-1",
    );
    expect(readBrowserResearchLog()).toEqual([entry]);
  });

  it("does not reuse another actor's log", () => {
    writeBrowserResearchLog([
      {
        id: "entry-a",
        createdAt: 1,
        platform: "Gemini",
        title: "A",
        note: "A",
      },
    ]);
    identity.actor = "bob";
    expect(readBrowserResearchLog()).toEqual([]);
  });
});
