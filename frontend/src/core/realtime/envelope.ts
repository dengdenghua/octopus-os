// JSON-RPC 2.0 envelope types — wire-level shape mirroring runtime/protocol/envelope.py.
//
// This file is the single source of truth for the realtime protocol on the
// client side. Every other module in core/realtime imports from here.
// Keep field names matching the Python side exactly (camelCase preserved
// across the wire by Pydantic alias_generator settings server-side).

export type JsonRpcId = number | string;

export interface JsonRpcRequest<P = Record<string, unknown>> {
  jsonrpc: "2.0";
  id: JsonRpcId;
  method: string;
  params: P;
}

export interface JsonRpcResponse<R = unknown> {
  jsonrpc: "2.0";
  id: JsonRpcId;
  result?: R;
  error?: JsonRpcError;
}

export interface JsonRpcError {
  code: number;
  message: string;
  data?: unknown;
}

export interface Notification<P = Record<string, unknown>> {
  jsonrpc: "2.0";
  method: string;
  params: P;
}

export type Envelope = JsonRpcRequest | JsonRpcResponse | Notification;

export const JsonRpcErrorCode = {
  PARSE_ERROR: -32700,
  INVALID_REQUEST: -32600,
  METHOD_NOT_FOUND: -32601,
  INVALID_PARAMS: -32602,
  INTERNAL_ERROR: -32603,
  APPROVAL_DENIED: -32000,
  APPROVAL_TIMEOUT: -32001,
  THREAD_NOT_FOUND: -32010,
  TURN_NOT_ACTIVE: -32011,
  UNAUTHORIZED: -32020,
} as const;

export type JsonRpcErrorCode =
  (typeof JsonRpcErrorCode)[keyof typeof JsonRpcErrorCode];

// Type guards.

export function isResponse(msg: Envelope): msg is JsonRpcResponse {
  return (
    "id" in msg && ("result" in msg || "error" in msg) && !("method" in msg)
  );
}

export function isRequest(msg: Envelope): msg is JsonRpcRequest {
  return "id" in msg && "method" in msg;
}

export function isNotification(msg: Envelope): msg is Notification {
  return "method" in msg && !("id" in msg);
}
