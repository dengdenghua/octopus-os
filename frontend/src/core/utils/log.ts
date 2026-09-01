const isDev =
  typeof process !== "undefined" && process.env.NODE_ENV === "development";

function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

export function swallow(error: unknown, context?: string) {
  if (isAbortError(error)) return;
  if (isDev) {
    console.warn(`[swallow${context ? `:${context}` : ""}]`, error);
  }
}
