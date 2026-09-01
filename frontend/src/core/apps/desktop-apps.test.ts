import { describe, expect, it } from "vitest";

import { desktopWindowURL } from "./desktop-apps";

describe("desktop app catalog helpers", () => {
  it("keeps route query parameters inside the shell hash", () => {
    expect(
      desktopWindowURL("/workspace/storage?surface=company&library=images"),
    ).toBe(
      "echo-app://app/index.html#/workspace/storage?surface=company&library=images&embedded=app",
    );
  });

  it("normalizes route-less app paths", () => {
    expect(desktopWindowURL("workspace/realtime/new")).toBe(
      "echo-app://app/index.html#/workspace/realtime/new?embedded=app",
    );
  });
});
