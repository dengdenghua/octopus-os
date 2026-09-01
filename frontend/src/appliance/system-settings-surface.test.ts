import { describe, expect, it } from "vitest";

import { resolveSystemSettingsSurface } from "./system-settings-surface";

describe("resolveSystemSettingsSurface", () => {
  it("keeps native operating-system settings in a desktop shell", () => {
    expect(resolveSystemSettingsSurface(true)).toBe("native");
  });

  it("opens Echo settings on browser and NAS desktops", () => {
    expect(resolveSystemSettingsSurface(false)).toBe("echo");
  });
});
