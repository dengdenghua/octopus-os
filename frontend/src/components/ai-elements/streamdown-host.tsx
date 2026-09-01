import { useEffect, useRef } from "react";
import { Streamdown } from "streamdown";
import "katex/dist/katex.min.css";

import type { StreamdownProps } from "streamdown";

export default Streamdown;
export type { StreamdownProps } from "streamdown";

// Map of English text/labels from streamdown's hard-coded UI to Chinese.
// streamdown doesn't expose i18n props, so we localize via a post-render
// DOM patch inside a wrapper. This is fragile but acceptable because
// (a) these labels are stable across patch versions and (b) if the
// library ever adds i18n support we can remove this wrapper entirely.
const TITLE_REPLACEMENTS: Record<string, string> = {
  "Copy table as markdown": "复制表格为 Markdown",
  "Copy table as csv": "复制表格为 CSV",
  "Download table": "下载表格",
  "Download file": "下载文件",
  "Download image": "下载图片",
};

const TEXT_REPLACEMENTS: Record<string, string> = {
  "Loading diagram...": "正在加载图表...",
  "Mermaid Error:": "Mermaid 错误：",
  "Show Code": "显示代码",
};

function localizeStreamdownDom(root: HTMLElement) {
  // Patch button titles (native tooltips)
  root.querySelectorAll<HTMLButtonElement>("button[title]").forEach((btn) => {
    const originalTitle = btn.getAttribute("title");
    if (originalTitle) {
      const newTitle = (TITLE_REPLACEMENTS as Record<string, string>)[
        originalTitle
      ];
      if (newTitle) btn.setAttribute("title", newTitle);
    }
  });

  // Patch text nodes inside menu items and status spans. Each node stores
  // the exact text it was last processed with; unchanged nodes skip the
  // trim + lookup work on subsequent passes. A node whose text changed
  // IN PLACE (streaming mermaid status labels do exactly that) no longer
  // matches its stored value and is re-examined, so a permanent tag can
  // never strand a stale English label.
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  const textNodes: Text[] = [];
  let currentNode: Node | null;
  while ((currentNode = walker.nextNode())) {
    const node = currentNode as Text & { __octoLocalized?: string };
    if (node.__octoLocalized === node.textContent) continue;
    textNodes.push(node);
  }
  for (const textNode of textNodes) {
    const node = textNode as Text & { __octoLocalized?: string };
    const text = node.textContent ?? "";
    const trimmed = text.trim();
    if (trimmed) {
      const replacement = (TEXT_REPLACEMENTS as Record<string, string>)[
        trimmed
      ];
      if (replacement) {
        // Preserve surrounding whitespace
        const leading = text.startsWith(" ") ? " " : "";
        const trailing = text.endsWith(" ") ? " " : "";
        node.textContent = leading + replacement + trailing;
      }
    }
    // Remember the exact text just processed (the localized value when a
    // patch was applied). Next pass compares cheaply and skips unchanged
    // nodes; in-place text updates mismatch and get reprocessed.
    node.__octoLocalized = node.textContent;
  }
}

/**
 * Wrapper around Streamdown that patches the library's hard-coded English
 * UI labels into Chinese after each render.
 */
export function LocalizedStreamdown(props: StreamdownProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const isAnimating = Boolean((props as { isAnimating?: boolean }).isAnimating);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // Run once on mount / animation-state change
    localizeStreamdownDom(container);

    // Watch for async additions (mermaid finishes rendering, copy/download
    // dropdown opens). While the markdown body is streaming we observe only
    // direct-child structural changes: MutationObserver callbacks never
    // interrupt JS execution, so per-token subtree scans can only ever run
    // AFTER the paint they meant to precede — pure waste. Deep observation
    // resumes once the stream settles (the transient surfaces we localize —
    // mermaid status labels, dropdown menus — appear via structural
    // insertions and are caught either by the direct-child filter or by the
    // settled-state effect re-run).
    let raf = 0;
    const observer = new MutationObserver(() => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        localizeStreamdownDom(container);
      });
    });
    observer.observe(
      container,
      isAnimating
        ? { childList: true }
        : { childList: true, subtree: true, characterData: true },
    );
    return () => {
      observer.disconnect();
      if (raf) cancelAnimationFrame(raf);
    };
  }, [isAnimating]);

  return (
    <div ref={containerRef} className="streamdown-localized">
      <Streamdown {...props} />
    </div>
  );
}
