import { describe, expect, it } from "vitest";

import type { AgentThread } from "./types";
import { threadVisibleInPersonaHistory } from "./persona-history";

function thread(owner?: string): AgentThread {
  return {
    thread_id: owner || "ownerless",
    metadata: owner ? { agent: owner } : {},
    values: { title: "test", messages: [], artifacts: [] },
    updated_at: "2026-01-01T00:00:00Z",
  } as AgentThread;
}

describe("persona history", () => {
  it("keeps White Ghost histories isolated from one another", () => {
    expect(threadVisibleInPersonaHistory(thread("coder"), "coder")).toBe(true);
    expect(threadVisibleInPersonaHistory(thread("coder"), "general")).toBe(
      false,
    );
  });

  it("shares legacy on-demand actor histories instead of hiding them", () => {
    for (const owner of [
      "research-advisor",
      "installed_code_reviewer",
      "mobile_phone1",
    ]) {
      expect(threadVisibleInPersonaHistory(thread(owner), "general")).toBe(
        true,
      );
      expect(threadVisibleInPersonaHistory(thread(owner), "coder")).toBe(true);
    }
  });

  it("keeps ownerless legacy history in the default lane", () => {
    expect(threadVisibleInPersonaHistory(thread(), "general")).toBe(true);
    expect(threadVisibleInPersonaHistory(thread(), "coder")).toBe(false);
  });
});
