/** Browser-safe bearer transport for authenticated WebSocket handshakes. */

export const WEBSOCKET_BEARER_PROTOCOL = "bearer.b64";

export function encodeWebSocketBearerToken(token: string): string {
  const bytes = new TextEncoder().encode(token);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

export function webSocketAuthProtocols(
  token: string | null | undefined,
): string[] | undefined {
  if (!token) return undefined;
  return [WEBSOCKET_BEARER_PROTOCOL, encodeWebSocketBearerToken(token)];
}

export function openAuthenticatedWebSocket(
  url: string,
  token: string | null | undefined,
): WebSocket {
  const protocols = webSocketAuthProtocols(token);
  return protocols ? new WebSocket(url, protocols) : new WebSocket(url);
}
