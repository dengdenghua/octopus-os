/**
 * Tiny browser-download helpers shared by the sharing exporters. ``shareSlug``
 * is pure (unit-tested); ``downloadTextFile`` is the DOM glue that hands a blob
 * to the browser's download mechanism.
 */

/** Slugify a title into a safe filename stem (ASCII + CJK, dash-separated). */
export function shareSlug(title: string): string {
  return (
    title
      .toLowerCase()
      .replace(/[^a-z0-9一-龥]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48) || "share"
  );
}

/** Trigger a browser download of a text/HTML file (no network, no server). */
export function downloadTextFile(
  content: string,
  filename: string,
  mime = "text/html",
): void {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke on the next tick so the download has grabbed the blob.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
