import { describe, expect, it } from "vitest";

import { isLocalPreviewUrl, localPreviewPort } from "./local-services";

describe("local service preview routing", () => {
  it("recognizes loopback pages independently of their development port", () => {
    expect(isLocalPreviewUrl("http://localhost:5173/app")).toBe(true);
    expect(isLocalPreviewUrl("http://127.0.0.1:3001")).toBe(true);
    expect(isLocalPreviewUrl("http://[::1]:8080")).toBe(true);
  });

  it("keeps normal web pages in browser mode", () => {
    expect(isLocalPreviewUrl("https://example.com")).toBe(false);
    expect(isLocalPreviewUrl("echo://home")).toBe(false);
    expect(isLocalPreviewUrl("not-a-url")).toBe(false);
  });

  it("exposes a compact port label for the preview toolbar", () => {
    expect(localPreviewPort("http://localhost:5173/app")).toBe("5173");
    expect(localPreviewPort("http://localhost/app")).toBe("80");
  });
});
