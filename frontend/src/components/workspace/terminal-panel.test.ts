import { describe, expect, it } from "vitest";

import { __testing } from "./terminal-panel";

describe("terminal shell label", () => {
  it("does not identify a macOS terminal as PowerShell", () => {
    expect(__testing.terminalShellLabel("MacIntel")).toBe("zsh");
  });

  it("keeps the Windows terminal label explicit", () => {
    expect(__testing.terminalShellLabel("Win32")).toBe("PowerShell");
  });

  it("uses a neutral label for other platforms", () => {
    expect(__testing.terminalShellLabel("Linux x86_64")).toBe("shell");
  });
});
