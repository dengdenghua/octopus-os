import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ actor: "account-a" }));
vi.mock("@/core/auth/api", () => ({ currentActorId: () => identity.actor }));

import {
  addPublishedPost,
  readFavorites,
  readFollowing,
  readLikes,
  writeLikes,
  readPublished,
  toggleFavorite,
  toggleFollowing,
} from "./community-data";

describe("community account storage", () => {
  beforeEach(() => {
    localStorage.clear();
    identity.actor = "account-a";
  });

  it("does not leak community interactions or published posts between actors", () => {
    toggleFollowing("作者 A");
    toggleFavorite("post-a");
    writeLikes(["post-a"]);
    addPublishedPost({
      title: "账号 A 的帖子",
      content: "仅属于账号 A",
      tag: "测试",
      topic: "test",
    });

    identity.actor = "account-b";
    expect(readFollowing()).toEqual([]);
    expect(readFavorites()).toEqual([]);
    expect(readLikes()).toEqual([]);
    expect(readPublished()).toEqual([]);

    identity.actor = "account-a";
    expect(readFollowing()).toEqual(["作者 A"]);
    expect(readFavorites()).toEqual(["post-a"]);
    expect(readLikes()).toEqual(["post-a"]);
    expect(readPublished()).toHaveLength(1);
  });
});
