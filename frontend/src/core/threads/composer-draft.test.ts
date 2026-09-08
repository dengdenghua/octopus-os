import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ actor: "actor-one" }));
vi.mock("@/core/auth/api", () => ({
  currentActorId: () => identity.actor,
}));

import {
  loadComposerDraft,
  NEW_THREAD_DRAFT_KEY,
  saveComposerDraft,
} from "./composer-draft";

describe("composer-draft", () => {
  beforeEach(() => {
    window.localStorage.clear();
    identity.actor = "actor-one";
  });

  it("round-trips a draft per thread", () => {
    saveComposerDraft("t-1", "写了一半的消息");
    expect(loadComposerDraft("t-1")).toBe("写了一半的消息");
    expect(loadComposerDraft("t-2")).toBeNull();
  });

  it("stores the new-thread composer under a dedicated key", () => {
    saveComposerDraft(undefined, "新会话草稿");
    const raw = window.localStorage.getItem(
      `echo:composer-draft:${encodeURIComponent(identity.actor)}:${NEW_THREAD_DRAFT_KEY}`,
    );
    expect(raw).not.toBeNull();
    const envelope = JSON.parse(raw ?? "{}") as { v?: number; text?: string };
    expect(envelope.v).toBe(1);
    expect(envelope.text).toBe("新会话草稿");
    expect(loadComposerDraft(null)).toBe("新会话草稿");
  });

  it("prunes drafts older than 30 days while keeping fresh ones", () => {
    const oldKey = `echo:composer-draft:${encodeURIComponent(identity.actor)}:stale-thread`;
    window.localStorage.setItem(
      oldKey,
      JSON.stringify({
        v: 1,
        text: "旧草稿",
        savedAt: Date.now() - 31 * 24 * 60 * 60 * 1000,
      }),
    );
    saveComposerDraft("fresh-thread", "新草稿");
    expect(window.localStorage.getItem(oldKey)).toBeNull();
    expect(loadComposerDraft("fresh-thread")).toBe("新草稿");
  });

  it("still reads legacy plain-text drafts", () => {
    window.localStorage.setItem(
      `echo:composer-draft:${encodeURIComponent(identity.actor)}:legacy`,
      "老格式草稿",
    );
    expect(loadComposerDraft("legacy")).toBe("老格式草稿");
  });

  it("clears the entry when the draft is emptied", () => {
    saveComposerDraft("t-1", "abc");
    saveComposerDraft("t-1", "");
    expect(loadComposerDraft("t-1")).toBeNull();
  });

  it("keeps drafts isolated when the authenticated actor changes", () => {
    saveComposerDraft("shared-thread", "账号一草稿");
    identity.actor = "actor-two";
    expect(loadComposerDraft("shared-thread")).toBeNull();
    saveComposerDraft("shared-thread", "账号二草稿");
    identity.actor = "actor-one";
    expect(loadComposerDraft("shared-thread")).toBe("账号一草稿");
  });

  it("never throws when storage is unavailable", () => {
    const original = window.localStorage;
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: {
        getItem: () => {
          throw new Error("denied");
        },
        setItem: () => {
          throw new Error("denied");
        },
        removeItem: () => {
          throw new Error("denied");
        },
      },
    });
    expect(() => saveComposerDraft("t-1", "x")).not.toThrow();
    expect(loadComposerDraft("t-1")).toBeNull();
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: original,
    });
  });
});
