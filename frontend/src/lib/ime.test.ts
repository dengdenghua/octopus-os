import { describe, expect, it } from "vitest";

import { isIMEComposing } from "./ime";

function fakeEvent(overrides: {
  isComposing?: boolean;
  keyCode?: number;
  nativeIsComposing?: boolean;
}): Parameters<typeof isIMEComposing>[0] {
  return {
    isComposing: overrides.isComposing ?? false,
    keyCode: overrides.keyCode ?? 13,
    nativeEvent: {
      isComposing: overrides.nativeIsComposing ?? false,
    },
  } as unknown as Parameters<typeof isIMEComposing>[0];
}

describe("isIMEComposing", () => {
  it("returns true when isComposing param is true", () => {
    expect(isIMEComposing(fakeEvent({}), true)).toBe(true);
  });

  it("returns true when nativeEvent.isComposing is true", () => {
    expect(isIMEComposing(fakeEvent({ nativeIsComposing: true }))).toBe(true);
  });

  it("returns true when keyCode is 229 (IME processing)", () => {
    expect(isIMEComposing(fakeEvent({ keyCode: 229 }))).toBe(true);
  });

  it("returns false for normal Enter key", () => {
    expect(isIMEComposing(fakeEvent({ keyCode: 13 }))).toBe(false);
  });

  it("returns false when all signals are negative", () => {
    expect(isIMEComposing(fakeEvent({}))).toBe(false);
  });
});
