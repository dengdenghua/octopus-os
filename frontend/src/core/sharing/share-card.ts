/**
 * Share-card engine — turns a task / result into a self-contained, branded
 * 1200×630 image (the standard social-preview aspect) the user can share.
 *
 * Pure + dependency-free: it produces an SVG *string* (deterministic, unit-
 * testable) which the UI layer rasterises to PNG via the browser canvas.
 * Nothing here touches the DOM, the network, or auth — and because the
 * caller passes plain strings (not a live thread object), the "what exactly
 * gets shared" decision stays explicit at the call site. Sharing is an
 * outward-facing publish action, so the card only ever contains text the
 * caller hands in — never a raw DOM scrape that might leak secrets.
 */

export interface ShareCardOptions {
  /** The headline — usually the user's task / thread title. */
  title: string;
  /** Optional "做同款" prompt so a recipient can recreate the task. */
  prompt?: string;
  /** Optional one-line result summary. */
  summary?: string;
  /** Brand line. Defaults to "EchoAI". */
  brand?: string;
  /** Footer note (e.g. a date). Caller formats it (no Date.now() here). */
  footer?: string;
}

export interface ShareCard {
  title: string;
  prompt: string;
  summary: string;
  brand: string;
  footer: string;
}

export const SHARE_CARD_WIDTH = 1200;
export const SHARE_CARD_HEIGHT = 630;

export function buildShareCard(opts: ShareCardOptions): ShareCard {
  return {
    title: (opts.title ?? "").trim() || "Untitled task",
    prompt: (opts.prompt ?? "").trim(),
    summary: (opts.summary ?? "").trim(),
    brand: (opts.brand ?? "EchoAI").trim(),
    footer: (opts.footer ?? "").trim(),
  };
}

/** XML-escape so user text can never break out of the SVG (or inject markup). */
export function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Greedy word-wrap to at most ``maxLines`` lines of ~``maxChars`` each. The
 * last line is ellipsised when text overflows. CJK has no spaces, so we also
 * hard-break any single "word" longer than a line. Returns plain (un-escaped)
 * lines — escaping happens at render time.
 */
export function wrapLines(
  text: string,
  maxChars: number,
  maxLines: number,
): string[] {
  const clean = text.replace(/\s+/g, " ").trim();
  if (!clean) return [];

  const words = clean.split(" ");
  const lines: string[] = [];
  let current = "";

  const pushWord = (word: string) => {
    // Hard-break a word that can't fit on its own line (e.g. long CJK run).
    while (word.length > maxChars) {
      if (current) {
        lines.push(current);
        current = "";
      }
      lines.push(word.slice(0, maxChars));
      word = word.slice(maxChars);
    }
    if (!current) {
      current = word;
    } else if (current.length + 1 + word.length <= maxChars) {
      current += " " + word;
    } else {
      lines.push(current);
      current = word;
    }
  };

  for (const word of words) pushWord(word);
  if (current) lines.push(current);

  if (lines.length <= maxLines) return lines;
  const kept = lines.slice(0, maxLines);
  // Ellipsise the final kept line (trim a char if it's already at the edge).
  const last = kept[maxLines - 1] ?? "";
  kept[maxLines - 1] =
    last.length >= maxChars ? last.slice(0, maxChars - 1) + "…" : last + " …";
  return kept;
}

function tspans(
  lines: string[],
  x: number,
  yStart: number,
  lineHeight: number,
): string {
  return lines
    .map(
      (line, i) =>
        `<tspan x="${x}" y="${yStart + i * lineHeight}">${escapeXml(line)}</tspan>`,
    )
    .join("");
}

/**
 * Render the card to an SVG string (1200×630). Deterministic — given the same
 * card, the same bytes. Branded palette (not theme-dependent) so a shared
 * image looks intentional regardless of the sharer's light/dark setting.
 */
export function renderShareCardSvg(card: ShareCard): string {
  const W = SHARE_CARD_WIDTH;
  const H = SHARE_CARD_HEIGHT;
  const padX = 80;

  const titleLines = wrapLines(card.title, 34, 3);
  const promptLines = card.prompt ? wrapLines(card.prompt, 64, 4) : [];
  const summaryLines = card.summary ? wrapLines(card.summary, 64, 2) : [];

  const titleSvg = tspans(titleLines, padX, 220, 64);
  const summarySvg = summaryLines.length
    ? `<text font-family="system-ui, -apple-system, sans-serif" font-size="30" fill="#94a3b8">${tspans(
        summaryLines,
        padX,
        220 + titleLines.length * 64 + 36,
        40,
      )}</text>`
    : "";

  const promptBlock = promptLines.length
    ? `<rect x="${padX}" y="${H - 250}" width="${W - padX * 2}" height="170" rx="16" fill="#1e293b" stroke="#334155" stroke-width="1"/>` +
      `<text x="${padX + 24}" y="${H - 210}" font-family="system-ui, -apple-system, sans-serif" font-size="20" fill="#64748b">做同款 · prompt</text>` +
      `<text font-family="ui-monospace, SFMono-Regular, Menlo, monospace" font-size="24" fill="#cbd5e1">${tspans(
        promptLines,
        padX + 24,
        H - 170,
        34,
      )}</text>`
    : "";

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#0f172a"/><stop offset="1" stop-color="#020617"/>
</linearGradient></defs>
<rect width="${W}" height="${H}" fill="url(#bg)"/>
<rect x="0" y="0" width="${W}" height="6" fill="#6366f1"/>
<text x="${padX}" y="110" font-family="system-ui, -apple-system, sans-serif" font-size="28" font-weight="600" fill="#818cf8">${escapeXml(
    card.brand,
  )}</text>
<text font-family="system-ui, -apple-system, sans-serif" font-size="52" font-weight="700" fill="#f8fafc">${titleSvg}</text>
${summarySvg}
${promptBlock}
<text x="${padX}" y="${H - 40}" font-family="system-ui, -apple-system, sans-serif" font-size="20" fill="#475569">${escapeXml(
    card.footer,
  )}</text>
</svg>`;
}
