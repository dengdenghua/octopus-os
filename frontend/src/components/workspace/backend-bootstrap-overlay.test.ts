import { describe, expect, it } from "vitest";

import { isPackagedShell } from "./backend-bootstrap-overlay";

describe("isPackagedShell", () => {
  it("recognizes the secure packaged desktop protocol", () => {
    expect(
      isPackagedShell({
        location: { protocol: "echo-app:" },
        echo: { isElectron: true },
      }),
    ).toBe(true);
  });

  it("rejects legacy file URLs and ordinary web sessions", () => {
    expect(
      isPackagedShell({
        location: { protocol: "file:" },
        echo: { isElectron: true },
      }),
    ).toBe(false);
    expect(
      isPackagedShell({
        location: { protocol: "http:" },
        echo: { isElectron: false },
      }),
    ).toBe(false);
  });
});
