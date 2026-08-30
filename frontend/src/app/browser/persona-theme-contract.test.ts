import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const browserSource = readFileSync(
  join(process.cwd(), "src/app/browser/page.tsx"),
  "utf8",
);
const workspaceSource = readFileSync(
  join(process.cwd(), "src/app/workspace/layout.tsx"),
  "utf8",
);
const stylesSource = readFileSync(
  join(process.cwd(), "src/styles/globals.css"),
  "utf8",
);

describe("shared persona theme scope", () => {
  it("uses the same persona theme contract in workspace and AI browser shells", () => {
    expect(workspaceSource).toContain(
      'className="persona-shell workspace-shell',
    );
    expect(workspaceSource).toContain("data-persona-theme={personaThemeId}");
    expect(browserSource).toContain('className="persona-shell browser-shell');
    expect(browserSource).toContain("data-persona-theme={personaThemeId}");
    expect(browserSource).toContain(
      "workspacePresetForAgent(activeAgentId).themeId",
    );
    expect(stylesSource).toContain('.persona-shell[data-persona-theme="noah"]');
    expect(stylesSource).toContain(".dark .persona-shell[data-persona-theme]");
  });
});
