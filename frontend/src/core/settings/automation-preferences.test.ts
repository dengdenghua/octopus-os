import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ actor: "account-a" }));
vi.mock("@/core/auth/api", () => ({ currentActorId: () => identity.actor }));

import {
  getLinkOpenTarget,
  linkOpenTargetStorageKey,
  setLinkOpenTarget,
} from "./automation-preferences";

describe("automation preferences", () => {
  beforeEach(() => {
    localStorage.clear();
    identity.actor = "account-a";
  });

  it("keeps link routing preferences isolated per actor", () => {
    setLinkOpenTarget("in_app");
    expect(localStorage.getItem(linkOpenTargetStorageKey())).toBe("in_app");

    identity.actor = "account-b";
    expect(getLinkOpenTarget()).toBe("external");
    setLinkOpenTarget("in_app");
    expect(localStorage.getItem(linkOpenTargetStorageKey())).toBe("in_app");

    identity.actor = "account-a";
    expect(getLinkOpenTarget()).toBe("in_app");
  });
});
