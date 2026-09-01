import { describe, expect, it } from "vitest";

import { pathOfThread, textOfMessage, titleOfThread } from "./utils";

describe("pathOfThread", () => {
  it("returns workspace realtime path", () => {
    expect(pathOfThread("abc-123")).toBe("/workspace/realtime/abc-123");
  });
});

describe("textOfMessage", () => {
  it("returns string content directly", () => {
    expect(textOfMessage({ type: "human", content: "hello" } as any)).toBe(
      "hello",
    );
  });

  it("extracts text from complex content array", () => {
    const msg = {
      type: "ai",
      content: [
        { type: "image_url", image_url: "http://img" },
        { type: "text", text: "caption" },
      ],
    } as any;
    expect(textOfMessage(msg)).toBe("caption");
  });

  it("returns null when no text part found", () => {
    const msg = {
      type: "ai",
      content: [{ type: "image_url", image_url: "http://img" }],
    } as any;
    expect(textOfMessage(msg)).toBeNull();
  });

  it("returns null for empty array content", () => {
    expect(textOfMessage({ type: "ai", content: [] } as any)).toBeNull();
  });
});

describe("titleOfThread", () => {
  it("returns title from thread values", () => {
    const thread = { values: { title: "My Chat" } } as any;
    expect(titleOfThread(thread)).toBe("My Chat");
  });

  it("returns Untitled when no title", () => {
    const thread = { values: {} } as any;
    expect(titleOfThread(thread)).toBe("Untitled");
  });

  it("returns Untitled when values is undefined", () => {
    const thread = {} as any;
    expect(titleOfThread(thread)).toBe("Untitled");
  });
});
