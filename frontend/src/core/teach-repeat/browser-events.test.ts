import { describe, expect, it } from "vitest";

import {
  browserRecorderDrainScript,
  normalizeBrowserRecordingEvents,
} from "./browser-events";

describe("browser recorder events", () => {
  it("installs a bounded, privacy-aware webview recorder", () => {
    const script = browserRecorderDrainScript();
    expect(script).toContain("__echoRecorderBrowserV1");
    expect(script).toContain("[REDACTED]");
    expect(script).toContain("buffer.length > 200");
  });

  it("accepts only browser recording events", () => {
    expect(
      normalizeBrowserRecordingEvents([
        { ts: "2026-08-25T00:00:00Z", source: "browser", kind: "pointerdown" },
        { ts: "2026-08-25T00:00:01Z", source: "human", kind: "click" },
        null,
      ]),
    ).toHaveLength(1);
  });
});
