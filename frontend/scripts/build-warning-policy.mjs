// @ts-check

/**
 * Fail when a module is both statically and dynamically imported. In that
 * state Rollup keeps it in the eager chunk, so the apparent lazy boundary is
 * misleading and can silently grow the desktop startup bundle.
 *
 * @param {string | null | undefined} code
 * @param {string} message
 */
export function rejectDefeatedCodeSplitting(code, message) {
  if (code === "DYNAMIC_IMPORT_WILL_NOT_MOVE_MODULE") {
    throw new Error(
      `Mixed static/dynamic import defeats code splitting: ${message}`,
    );
  }
}

/**
 * Fail a production build when one emitted JavaScript chunk exceeds the
 * parse-unit budget. Vite's chunkSizeWarningLimit only prints a warning, so a
 * separate assertion is required to make the budget enforceable in CI.
 *
 * @param {string} fileName
 * @param {number} byteLength
 * @param {number} limitKiB
 */
export function rejectOversizedJavaScriptChunk(fileName, byteLength, limitKiB) {
  const limitBytes = limitKiB * 1024;
  if (byteLength > limitBytes) {
    const actualKiB = (byteLength / 1024).toFixed(1);
    throw new Error(
      `JavaScript chunk ${fileName} is ${actualKiB} KiB; limit is ${limitKiB} KiB`,
    );
  }
}
