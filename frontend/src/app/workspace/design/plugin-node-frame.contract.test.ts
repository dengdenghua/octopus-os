import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const source = readFileSync(
  join(process.cwd(), "src/app/workspace/design/plugin-node-frame.tsx"),
  "utf8",
);

describe("plugin node frame contract", () => {
  it("scopes child state requests to the parent-selected capability", () => {
    expect(source).toContain("event.origin !== frameOrigin");
    expect(source).toContain(
      "event.source !== frameRef.current?.contentWindow",
    );
    expect(source).toContain("encodeURIComponent(projectId)");
    expect(source).toContain("encodeURIComponent(nodeId)");
    expect(source).toContain("plugin_id: pluginId");
    expect(source).toContain(
      'sandbox="allow-scripts allow-same-origin allow-downloads"',
    );
  });

  it("supports revisioned get, set and delete without exposing auth", () => {
    expect(source).toContain('action: "get" | "set" | "delete"');
    expect(source).toContain("expected_revision");
    expect(source).toContain("echo.plugin-state.response");
    expect(source).not.toContain("Authorization:");
  });
});
