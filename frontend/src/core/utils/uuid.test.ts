import { describe, expect, it, vi } from "vitest";

vi.mock("nanoid", () => ({
  nanoid: vi.fn(() => "-2494-9AMFYgjfm9Ym0uN"),
}));

import { uuid } from "./uuid";

describe("uuid", () => {
  it("keeps nanoid's leading punctuation away from the thread-id boundary", () => {
    const id = uuid();

    expect(id).toBe("t-2494-9AMFYgjfm9Ym0uN");
    expect(id).toMatch(/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/);
  });
});
