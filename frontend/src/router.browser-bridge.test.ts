import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

describe("Echo OS browser route", () => {
  it("keeps the complete AI browser inside the OS frontend", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/router.tsx"),
      "utf8",
    );

    expect(source).toContain('path="/browser"');
    expect(source).toContain("element={<TopBrowserPage />}");
    expect(source).not.toContain("AgentWorkspaceBridge");
  });
});
