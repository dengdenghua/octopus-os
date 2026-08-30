/**
 * Cross-platform path utilities for workspace file operations.
 *
 * The frontend deals with Windows backslash paths (from workdir selector)
 * and forward-slash relative paths (from backend /api/fs/tree).
 * These helpers ensure consistent joining and normalization.
 */

/** Join a base directory with a relative path using forward slashes. */
export function joinPath(base: string, relative: string): string {
  const normalized = base.replace(/[\\/]+$/, "");
  return `${normalized}/${relative}`;
}

/** Normalize all backslashes to forward slashes. */
export function normalizePath(p: string): string {
  return p.replace(/\\/g, "/");
}

/** Return true when the path is absolute (Windows or POSIX). */
export function isAbsolutePath(p: string): boolean {
  const value = p.trim();
  if (!value) return false;
  return (
    /^[A-Za-z]:[\\/]/.test(value) ||
    value.startsWith("/") ||
    value.startsWith("\\\\")
  );
}

/** Extract the last segment of a path (file or folder name). */
export function basename(p: string): string {
  return p.split(/[\\/]/).pop() ?? p;
}
