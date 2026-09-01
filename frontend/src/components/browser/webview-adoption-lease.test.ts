import { describe, expect, it } from "vitest";

import { browserWebContentsAdoptionLease } from "./webview-tab";

describe("browser webContents adoption lease", () => {
  it("is stable per logical tab and distinct across tabs", () => {
    expect(browserWebContentsAdoptionLease("tab-a")).toBe(
      "echo-webcontents:tab-a",
    );
    expect(browserWebContentsAdoptionLease("tab-a")).toBe(
      browserWebContentsAdoptionLease("tab-a"),
    );
    expect(browserWebContentsAdoptionLease("tab-a")).not.toBe(
      browserWebContentsAdoptionLease("tab-b"),
    );
  });
});
