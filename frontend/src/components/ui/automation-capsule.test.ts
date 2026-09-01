import { describe, expect, it } from "vitest";

import {
  AUTOMATION_CAPSULE_CONTROLS_CLASS_NAME,
  AUTOMATION_CAPSULE_OVERLAY_CLASS_NAME,
  AUTOMATION_CAPSULE_SURFACE_CLASS_NAME,
} from "./automation-capsule";

describe("automation capsule visual contract", () => {
  it("keeps overlays click-through while restoring control interaction", () => {
    expect(AUTOMATION_CAPSULE_OVERLAY_CLASS_NAME).toBe("pointer-events-none");
    expect(AUTOMATION_CAPSULE_CONTROLS_CLASS_NAME).toBe("pointer-events-auto");
  });

  it("keeps the shared 12px secondary glass surface", () => {
    expect(AUTOMATION_CAPSULE_SURFACE_CLASS_NAME.split(" ")).toEqual(
      expect.arrayContaining([
        "rounded-[12px]",
        "bg-secondary/90",
        "backdrop-blur-sm",
        "ring-[0.5px]",
        "ring-border/70",
        "shadow-[0px_8px_16px_-4px_rgba(0,0,0,.12)]",
      ]),
    );
  });
});
