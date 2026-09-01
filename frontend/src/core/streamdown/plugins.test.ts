import { renderHook } from "@testing-library/react";
import type { Root } from "hast";
import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import { describe, expect, it } from "vitest";

import {
  useHumanMessagePlugins,
  useStreamdownPlugins,
  useStreamdownPluginsWithWordAnimation,
} from "./plugins";

// A bundle entry is either a bare plugin function or a
// `[plugin, options]` tuple; compare by plugin reference.
function pluginRefs(
  plugins: unknown[] | undefined,
): unknown[] {
  return (plugins ?? []).map((entry) =>
    Array.isArray(entry) ? entry[0] : entry,
  );
}

// Regression guard for audit item R-01: BASE_PLUGINS renders
// agent-authored content (wiki / workbench / memory / artifact
// panels). Without a sanitizer after rehype-raw, HTML echoed by the
// agent — potentially attacker-controlled, e.g. from a fetched page —
// executes in the UI.
describe("streamdown plugin bundles sanitize agent content", () => {
  it("places rehype-sanitize after rehype-raw and before katex", () => {
    const { rehypePlugins } = useStreamdownPlugins();
    const refs = pluginRefs(rehypePlugins as unknown[]);
    const rawIdx = refs.indexOf(rehypeRaw);
    const sanitizeIdx = refs.indexOf(rehypeSanitize);
    const katexIdx = refs.indexOf(rehypeKatex);

    expect(rawIdx).toBeGreaterThanOrEqual(0);
    // sanitize after raw so raw-parsed injection is stripped
    expect(sanitizeIdx).toBeGreaterThan(rawIdx);
    // sanitize before katex so math markup survives
    expect(katexIdx).toBeGreaterThan(sanitizeIdx);
  });

  it("keeps sanitization in the word-animation bundle", () => {
    const { result } = renderHook(() =>
      useStreamdownPluginsWithWordAnimation(),
    );
    const refs = pluginRefs(result.current.rehypePlugins as unknown[]);
    const rawIdx = refs.indexOf(rehypeRaw);
    const sanitizeIdx = refs.indexOf(rehypeSanitize);

    expect(rawIdx).toBeGreaterThanOrEqual(0);
    expect(sanitizeIdx).toBeGreaterThan(rawIdx);
  });

  it("never enables rehype-raw for human-authored messages", () => {
    const { rehypePlugins } = useHumanMessagePlugins();
    const refs = pluginRefs(rehypePlugins as unknown[]);
    expect(refs).not.toContain(rehypeRaw);
  });

  it("strips event-handler attributes from injected HTML", () => {
    // Documents the security contract of the sanitize step used above:
    // an <img onerror=...> payload loses its handler attribute.
    const tree: Root = {
      type: "root",
      children: [
        {
          type: "element",
          tagName: "img",
          properties: { src: "x", onerror: "alert(1)" },
          children: [],
        },
        {
          type: "element",
          tagName: "script",
          properties: {},
          children: [{ type: "text", value: "alert(1)" }],
        },
      ],
    };

    const clean = rehypeSanitize()(tree);

    const html = JSON.stringify(clean);
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("script");
    expect(html).not.toContain("alert(1)");
  });
});
