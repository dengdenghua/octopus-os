import { useMemo } from "react";
import type { StreamdownProps } from "streamdown";

import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { rehypeSplitWordsIntoSpans } from "../rehype";

type StreamdownPluginBundle = Pick<
  StreamdownProps,
  "remarkPlugins" | "rehypePlugins"
>;

// ─── Eager plugin bundles ──────────────────────────────────────
//
// These plugins used to be loaded via dynamic `import()` behind a
// `useState(EMPTY_PLUGINS) + useEffect(load)` dance. That created
// a first-render window where `rehypePlugins = []` and any raw
// HTML in the streamed message — `<details>`, `<summary>`, etc. —
// got escaped and rendered as literal text. A backend ReAct trace
// wrapped in `<details>…</details>` would ship to the user looking
// broken whenever the message arrived before the async import
// resolved (which is every first-render in practice).
//
// The four plugin bundles are small in total (`rehype-raw` ~4kB,
// `rehype-katex` ~80kB mostly loaded on demand anyway, `remark-gfm`
// ~15kB, `remark-math` ~8kB). Paying that cost once at page load
// is cheaper than mis-rendering every first-render message.
//
// If you're tempted to go dynamic again for code-splitting, make
// sure the initial `useState` returns a complete bundle — an
// EMPTY rehypePlugins array is the exact bug we're avoiding here.

const BASE_PLUGINS: StreamdownPluginBundle = {
  remarkPlugins: [
    remarkGfm,
    [remarkMath, { singleDollarTextMath: true }],
  ] as StreamdownProps["remarkPlugins"],
  // Security (audit R-01): agent-authored content flows through this
  // bundle in the wiki / workbench / memory / artifact panels, and an
  // agent can echo attacker-controlled HTML (e.g. a malicious page it
  // fetched). `rehype-sanitize` MUST sit between `rehype-raw` (which
  // parses raw HTML into the tree) and `rehype-katex` — after raw so
  // injected markup is stripped, before katex so math output survives.
  // The chat chain in `core/rehype/index.ts` keeps the same order.
  rehypePlugins: [
    rehypeRaw,
    rehypeSanitize,
    [rehypeKatex, { output: "html" }],
  ] as StreamdownProps["rehypePlugins"],
};

// Human-authored messages don't want rehype-raw — they should not
// be able to inject HTML into the DOM. Math is still useful.
const HUMAN_PLUGINS: StreamdownPluginBundle = {
  remarkPlugins: [
    [remarkMath, { singleDollarTextMath: true }],
  ] as StreamdownProps["remarkPlugins"],
  rehypePlugins: [
    [rehypeKatex, { output: "html" }],
  ] as StreamdownProps["rehypePlugins"],
};

export function useStreamdownPlugins(): StreamdownPluginBundle {
  return BASE_PLUGINS;
}

export function useStreamdownPluginsWithWordAnimation(): StreamdownPluginBundle {
  // useMemo keeps the composed array identity stable so downstream
  // components that `useMemo` on `rehypePlugins` don't thrash.
  return useMemo<StreamdownPluginBundle>(
    () => ({
      remarkPlugins: BASE_PLUGINS.remarkPlugins,
      rehypePlugins: [
        ...(BASE_PLUGINS.rehypePlugins ?? []).filter(
          (plugin) => plugin !== undefined,
        ),
        rehypeSplitWordsIntoSpans,
      ] as StreamdownProps["rehypePlugins"],
    }),
    [],
  );
}

export function useHumanMessagePlugins(): StreamdownPluginBundle {
  return HUMAN_PLUGINS;
}
