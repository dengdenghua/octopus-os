import { describe, expect, it } from "vitest";
import { activeAgentIdForLocation } from "./active";
import { conversationAgentId } from "./conversation-identity";

describe("conversation identity", () => {
  it("uses the roster-resolved Echo for a new task even after visiting a Coder thread", () => {
    const activeAgentId = activeAgentIdForLocation(
      "/workspace/realtime/new",
      "?agent=coder",
      "coder",
      ["general"],
    );
    expect(
      conversationAgentId({
        isNewThread: true,
        threadId: "draft",
        activeAgentId: activeAgentId!,
        threadOwnerAgentId: "coder",
      }),
    ).toBe("general");
  });

  it("preserves an existing task's owner even if it is absent from the selectable roster", () => {
    expect(
      conversationAgentId({
        isNewThread: false,
        threadId: "existing-task",
        activeAgentId: "general",
        threadOwnerAgentId: "coder",
      }),
    ).toBe("coder");
  });

  it("keeps the fixed assistant identity when historical metadata disagrees", () => {
    expect(
      conversationAgentId({
        isNewThread: false,
        threadId: "echo-assistant",
        activeAgentId: "market_researcher",
        threadOwnerAgentId: "general",
      }),
    ).toBe("echo");
  });
});
