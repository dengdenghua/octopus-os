import { describe, expect, it } from "vitest";

import {
  encodeSessionReferenceUri,
  formatSessionReferenceMention,
} from "./mention-autocomplete";

// Fixtures are the authoritative output of the backend encoder
// (runtime/execution/tool_engine/session_reference_uri.py) — the frontend
// must byte-match so the backend's strict canonical re-encode check accepts
// what the UI inserts.
describe("Echo session-reference URI encoding", () => {
  it("encodes ASCII ids", () => {
    expect(encodeSessionReferenceUri("abc123")).toBe(
      "echo-session:ImFiYzEyMyI",
    );
  });

  it("encodes non-ASCII ids with ASCII JSON escapes", () => {
    expect(encodeSessionReferenceUri("中文会话")).toBe(
      "echo-session:Ilx1NGUyZFx1NjU4N1x1NGYxYVx1OGJkZCI",
    );
  });

  it("encodes ids with punctuation and spaces", () => {
    expect(encodeSessionReferenceUri("sess-01_f/x:y")).toBe(
      "echo-session:InNlc3MtMDFfZi94Onki",
    );
    expect(encodeSessionReferenceUri("a b")).toBe("echo-session:ImEgYiI");
  });

  it("formats canonical mentions with escaped labels", () => {
    expect(formatSessionReferenceMention("abc123", "Researcher] A\\B")).toBe(
      "@[Researcher\\] A\\\\B](echo-session:ImFiYzEyMyI)",
    );
    expect(formatSessionReferenceMention("abc123")).toBe(
      "@[abc123](echo-session:ImFiYzEyMyI)",
    );
  });
});
