/**
 * Browser layer for the share-card engine: rasterise the (self-contained,
 * text-only) SVG card to a PNG and hand it to the user via download or
 * clipboard. The SVG embeds no external images, so the canvas is never
 * tainted and ``toBlob`` always succeeds cross-origin-free.
 */

import {
  SHARE_CARD_HEIGHT,
  SHARE_CARD_WIDTH,
  buildShareCard,
  renderShareCardSvg,
  type ShareCardOptions,
} from "./share-card";

/** Rasterise an SVG string to a PNG Blob at ``scale`` device pixels. */
export async function svgToPngBlob(svg: string, scale = 2): Promise<Blob> {
  const svgBlob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(svgBlob);
  try {
    const img = new Image();
    img.decoding = "async";
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve();
      img.onerror = () => reject(new Error("share image failed to load"));
      img.src = url;
    });

    const canvas = document.createElement("canvas");
    canvas.width = SHARE_CARD_WIDTH * scale;
    canvas.height = SHARE_CARD_HEIGHT * scale;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("share image: no 2d canvas context");
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

    return await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob(
        (blob) =>
          blob
            ? resolve(blob)
            : reject(new Error("share image: toBlob returned null")),
        "image/png",
      );
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** Build the PNG for a task/result from caller-supplied strings. */
export async function buildTaskShareImage(
  opts: ShareCardOptions,
): Promise<Blob> {
  return svgToPngBlob(renderShareCardSvg(buildShareCard(opts)));
}

function safeFilename(title: string): string {
  const slug = title
    .toLowerCase()
    .replace(/[^a-z0-9一-龥]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
  return `echo-${slug || "share"}.png`;
}

/** Trigger a browser download of the PNG. */
export function downloadPng(blob: Blob, title: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = safeFilename(title);
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke on the next tick so the download has grabbed the blob.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** Whether the runtime can copy an image to the clipboard. */
export function canCopyImageToClipboard(): boolean {
  return (
    typeof navigator !== "undefined" &&
    !!navigator.clipboard &&
    typeof navigator.clipboard.write === "function" &&
    typeof ClipboardItem !== "undefined"
  );
}

/** Copy the PNG to the clipboard (for pasting into chat / social apps). */
export async function copyPngToClipboard(blob: Blob): Promise<void> {
  await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
}
