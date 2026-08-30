import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

describe("public share route contract", () => {
  it("keeps the capability-token viewer outside the authenticated workspace", () => {
    const source = readFileSync(join(process.cwd(), "src/router.tsx"), "utf8");
    const publicRoute = source.indexOf(
      '<Route path="/share/:token" element={<PublicThreadSharePage />} />',
    );
    const authBoundary = source.indexOf("<Route element={<ProtectedRoute />}>");

    expect(publicRoute).toBeGreaterThanOrEqual(0);
    expect(authBoundary).toBeGreaterThan(publicRoute);
  });
});
