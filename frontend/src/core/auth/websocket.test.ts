import { describe, expect, it } from "vitest";

import {
  WEBSOCKET_BEARER_PROTOCOL,
  encodeWebSocketBearerToken,
  webSocketAuthProtocols,
} from "./websocket";

describe("WebSocket bearer transport", () => {
  it("encodes arbitrary UTF-8 credentials as an RFC-safe base64url value", () => {
    const encoded = encodeWebSocketBearerToken("令牌 with spaces/(test)");

    expect(encoded).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(encoded).not.toContain("=");
  });

  it("returns a non-secret marker followed by the encoded credential", () => {
    expect(webSocketAuthProtocols("sk-alice")).toEqual([
      WEBSOCKET_BEARER_PROTOCOL,
      "c2stYWxpY2U",
    ]);
  });

  it("omits protocols when no credential is available", () => {
    expect(webSocketAuthProtocols(null)).toBeUndefined();
    expect(webSocketAuthProtocols("")).toBeUndefined();
  });
});
