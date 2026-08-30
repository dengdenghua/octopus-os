import { swallow } from "@/core/utils/log";
import { parse } from "best-effort-json-parser";

export function tryParseJSON(json: string) {
  const trimmed = json.trim();
  try {
    return JSON.parse(trimmed);
  } catch (e) {
    // The recovery parser is useful for streamed object/array fragments, but
    // it also treats arbitrary prose as a scalar value. Only invoke it for
    // input that actually starts like a structured JSON document.
    if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) {
      swallow(e);
      return undefined;
    }
    try {
      return parse(trimmed);
    } catch (recoveryError) {
      swallow(recoveryError);
      return undefined;
    }
  }
}
