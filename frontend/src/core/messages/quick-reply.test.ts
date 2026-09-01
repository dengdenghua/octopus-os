import { describe, expect, it } from "vitest";

import {
  dispatchQuickReply,
  QUICK_REPLY_EVENT,
  quickReplyTextForThread,
  type QuickReplyDetail,
} from "./quick-reply";

describe("quickReplyTextForThread", () => {
  it("accepts a quick reply scoped to the current task", () => {
    expect(
      quickReplyTextForThread(
        { text: "  update hero  ", threadId: "t1" },
        "t1",
      ),
    ).toBe("update hero");
  });

  it("rejects a quick reply from a stale task", () => {
    expect(
      quickReplyTextForThread({ text: "update hero", threadId: "old" }, "new"),
    ).toBeNull();
  });

  it("keeps legacy unscoped quick replies working", () => {
    expect(quickReplyTextForThread({ text: "continue" }, "t1")).toBe(
      "continue",
    );
  });
});

describe("dispatchQuickReply", () => {
  it("returns true only when the active task acknowledges the request", () => {
    const listener = (event: Event) => {
      const detail = (event as CustomEvent<QuickReplyDetail>).detail;
      if (quickReplyTextForThread(detail, "t1")) event.preventDefault();
    };
    window.addEventListener(QUICK_REPLY_EVENT, listener);
    try {
      expect(dispatchQuickReply({ text: "edit", threadId: "t1" })).toBe(true);
      expect(dispatchQuickReply({ text: "edit", threadId: "t2" })).toBe(false);
    } finally {
      window.removeEventListener(QUICK_REPLY_EVENT, listener);
    }
  });
});
