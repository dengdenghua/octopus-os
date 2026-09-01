import { describe, expect, it } from "vitest";

import { resolveModelContextWindow } from "./context-window";
import type { Model } from "./types";

function model(id: string, contextWindow?: number): Model {
  return {
    id,
    name: id,
    model: id,
    display_name: id,
    context_window: contextWindow,
  };
}

describe("resolveModelContextWindow", () => {
  it("uses the explicit 1M setting returned by model configuration", () => {
    expect(resolveModelContextWindow(model("deepseek-v4-pro", 1_000_000))).toBe(
      1_000_000,
    );
  });

  it("keeps ordinary custom models at the safe default", () => {
    expect(resolveModelContextWindow(model("custom-model"))).toBe(128_000);
  });
});
