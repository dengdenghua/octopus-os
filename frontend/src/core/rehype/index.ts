import type { Element, Root, ElementContent } from "hast";
import { useMemo } from "react";
import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import { visit } from "unist-util-visit";
import type { BuildVisitor } from "unist-util-visit";
import type { StreamdownProps } from "streamdown";

const CJK_TEXT_RE =
  /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/gu;

const cjkSegmenter = new Intl.Segmenter("zh", { granularity: "grapheme" });
const wordSegmenter = new Intl.Segmenter("en", { granularity: "word" });

function isPrimarilyCJK(text: string): boolean {
  const cjkChars = text.match(CJK_TEXT_RE);
  if (!cjkChars) return false;
  return cjkChars.length / text.length > 0.5;
}

function splitCJKText(text: string): string[] {
  return Array.from(cjkSegmenter.segment(text)).map((s) => s.segment);
}

function splitNonCJKText(text: string): string[] {
  return Array.from(wordSegmenter.segment(text))
    .map((s) => s.segment)
    .filter(Boolean);
}

function splitTextIntoUnits(text: string): string[] {
  const isCJK = isPrimarilyCJK(text);
  return isCJK ? splitCJKText(text) : splitNonCJKText(text);
}

export type RehypeSplitWordsOptions = {
  // Text nodes longer than this are left untouched. Bounds the
  // per-token cost of the still-growing final block during streaming.
  maxTextLength?: number;
  // Only the trailing window is fed to Intl.Segmenter. The animated
  // unit lives at the very end of the text, and word/grapheme
  // boundaries near the end never depend on content hundreds of
  // characters earlier, so the output is identical to segmenting the
  // full string while the per-token cost stays O(tailWindow).
  tailWindow?: number;
};

const DEFAULT_MAX_TEXT_LENGTH = 8000;
const DEFAULT_TAIL_WINDOW = 200;

function animateLastVisibleUnit(
  text: string,
  tailWindow: number,
): ElementContent[] {
  const tailStart = Math.max(0, text.length - tailWindow);
  const head = text.slice(0, tailStart);
  const units = splitTextIntoUnits(text.slice(tailStart));
  let animatedIndex = units.length - 1;
  while (animatedIndex >= 0 && !units[animatedIndex]?.trim()) {
    animatedIndex -= 1;
  }
  if (animatedIndex < 0) return [{ type: "text", value: text }];

  const children: ElementContent[] = [];
  const prefix = head + units.slice(0, animatedIndex).join("");
  const animated = units[animatedIndex] ?? "";
  const suffix = units.slice(animatedIndex + 1).join("");

  if (prefix) children.push({ type: "text", value: prefix });
  children.push({
    type: "element",
    tagName: "span",
    properties: { className: ["animate-fade-in", "inline"] },
    children: [{ type: "text", value: animated }],
  });
  if (suffix) children.push({ type: "text", value: suffix });
  return children;
}

export function rehypeSplitWordsIntoSpans({
  maxTextLength = DEFAULT_MAX_TEXT_LENGTH,
  tailWindow = DEFAULT_TAIL_WINDOW,
}: RehypeSplitWordsOptions = {}) {
  return (tree: Root) => {
    visit(tree, "element", ((node: Element) => {
      if (
        ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "strong"].includes(
          node.tagName,
        ) &&
        node.children &&
        node.children.length > 0
      ) {
        const textChildren = node.children.filter((c) => c.type === "text");
        const hasMultipleTextNodes = textChildren.length > 1;
        let lastTextNodeIndex = -1;
        if (hasMultipleTextNodes) {
          for (let i = node.children.length - 1; i >= 0; i--) {
            const child = node.children[i];
            if (child && child.type === "text") {
              lastTextNodeIndex = i;
              break;
            }
          }
        }

        const newChildren: Array<ElementContent> = [];
        node.children.forEach((child, childIndex) => {
          if (child.type === "text") {
            const text = child.value;
            if (!text || text.length === 0) return;

            if (text.length > maxTextLength) {
              newChildren.push(child);
              return;
            }

            const isLastTextNode = hasMultipleTextNodes
              ? childIndex === lastTextNodeIndex
              : true;

            if (!isLastTextNode) {
              newChildren.push(child);
              return;
            }

            // Only the newest visible unit needs a wrapper. Wrapping every
            // historical word makes a long streaming answer grow thousands
            // of DOM nodes even though those older units no longer animate.
            newChildren.push(...animateLastVisibleUnit(text, tailWindow));
          } else {
            newChildren.push(child);
          }
        });
        node.children = newChildren;
      }
    }) as BuildVisitor<Root, "element">);
  };
}

/**
 * File-citation transform: turn inline code that looks like
 *   `router.tsx:16-23`  or  `src/app/x.ts:42`
 * into a <file-ref> element carrying `path` and `lines` data attrs, so
 * the markdown renderer can swap it for a <FileReferenceChip>. Only
 * touches inline <code> nodes (not code fences), and only when the text
 * has a filename-ish extension followed by `:lineRange`.
 */
const FILE_REF_RE = /^([\w./\\-]+\.[a-zA-Z0-9]+):(\d+(?:-\d+)?)$/;

export function rehypeFileReferences() {
  return (tree: Root) => {
    visit(tree, "element", ((node: Element, index, parent) => {
      if (
        node.tagName !== "code" ||
        !parent ||
        (parent as Element).tagName === "pre"
      ) {
        return;
      }
      const first = node.children[0];
      if (!first || first.type !== "text") return;
      const m = first.value.match(FILE_REF_RE);
      if (!m) return;
      const [, path, lines] = m;
      const replacement: Element = {
        type: "element",
        tagName: "file-ref",
        properties: { path, lines },
        children: [],
      };
      if (typeof index === "number") {
        (parent as Element).children[index] = replacement;
      }
    }) as BuildVisitor<Root, "element">);
  };
}

// Eager base plugins — see `streamdown/plugins.ts` for the full
// rationale. Short version: this used to be `useState + useEffect +
// dynamic import()`, which meant the first render ran with no
// `rehype-raw`. Any HTML in the streamed message (e.g. the
// `<details>` wrapper around a ReAct trace) got escaped as text.
// Static import fixes the first-render window; packages are
// already in the dependency bundle anyway.
//
// Security: rehype-sanitize removes unsafe HTML (XSS vectors) after
// rehype-raw parses it. The default schema allows safe tags/attrs.
const CHAT_REHYPE_BASE: StreamdownProps["rehypePlugins"] = [
  rehypeRaw,
  rehypeSanitize,
  [rehypeKatex, { output: "html" }],
] as StreamdownProps["rehypePlugins"];

export function useChatRehypePlugins({
  splitWords = true,
}: { splitWords?: boolean } = {}): StreamdownProps["rehypePlugins"] {
  return useMemo<StreamdownProps["rehypePlugins"]>(
    () =>
      [
        ...(CHAT_REHYPE_BASE ?? []),
        rehypeFileReferences,
        ...(splitWords ? [rehypeSplitWordsIntoSpans] : []),
      ] as StreamdownProps["rehypePlugins"],
    [splitWords],
  );
}

/** @deprecated Prefer ``useChatRehypePlugins({ splitWords: enabled })``. */
export function useRehypeSplitWordsIntoSpans(
  enabled = true,
): StreamdownProps["rehypePlugins"] {
  return useChatRehypePlugins({ splitWords: enabled });
}
