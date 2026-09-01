import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const pageSource = readFileSync(
  join(process.cwd(), "src/app/workspace/realtime/[thread_id]/page.tsx"),
  "utf8",
);

describe("fresh realtime persona contract", () => {
  it("does not let temporary /new thread metadata override the requested persona", () => {
    const start = pageSource.indexOf("const effectiveAgentId =");
    const end = pageSource.indexOf("// A bound project owns", start);
    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);

    const resolver = pageSource.slice(start, end);
    expect(resolver).toContain("isNewThread");
    expect(resolver).toContain("? activeAgentId");
    expect(resolver.indexOf("? activeAgentId")).toBeLessThan(
      resolver.indexOf("resolvedThreadOwnerAgentId"),
    );
  });
});
