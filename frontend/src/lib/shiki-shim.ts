/**
 * Shiki bundle-size shim — aliased to the bare "shiki" specifier in
 * vite.config.ts (regex /^shiki$/ so subpath imports like
 * "shiki/langs/bash.mjs" still resolve to the real package).
 *
 * Why: the full "shiki" entry drags ~200 lazy language chunks (several MB
 * of grammars: emacs-lisp ~780KB, cpp ~626KB, …) plus the Oniguruma WASM
 * engine (~622KB) into the build. The app only highlights chat code
 * fences, so this shim swaps in a JavaScript-regex-engine core highlighter
 * with a curated language whitelist. Unknown languages degrade gracefully
 * to plain text instead of pulling a grammar.
 *
 * Runtime consumers:
 * - src/components/ai-elements/code-block.tsx → codeToHtml
 * - streamdown (node_modules, static import)  → bundledLanguages, createHighlighter
 *
 * Type-only imports from "shiki" (BundledLanguage, ShikiTransformer) are
 * erased at compile time and still resolve against the real package, so
 * call sites need no changes.
 */
import { createHighlighterCore } from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";

type ModuleLoader = () => Promise<unknown>;

/**
 * Whitelisted languages + common fence aliases → lazy grammar loaders.
 * Alias files re-export the canonical grammar, so the bundler dedupes
 * them into one chunk per real language. Everything outside this list is
 * tree-shaken out of the build.
 */
const LANGUAGE_LOADERS: Record<string, ModuleLoader> = {
  bash: () => import("shiki/langs/bash.mjs"),
  sh: () => import("shiki/langs/sh.mjs"),
  shell: () => import("shiki/langs/shell.mjs"),
  shellscript: () => import("shiki/langs/shellscript.mjs"),
  c: () => import("shiki/langs/c.mjs"),
  cpp: () => import("shiki/langs/cpp.mjs"),
  csharp: () => import("shiki/langs/csharp.mjs"),
  css: () => import("shiki/langs/css.mjs"),
  diff: () => import("shiki/langs/diff.mjs"),
  docker: () => import("shiki/langs/docker.mjs"),
  dockerfile: () => import("shiki/langs/dockerfile.mjs"),
  go: () => import("shiki/langs/go.mjs"),
  graphql: () => import("shiki/langs/graphql.mjs"),
  html: () => import("shiki/langs/html.mjs"),
  ini: () => import("shiki/langs/ini.mjs"),
  java: () => import("shiki/langs/java.mjs"),
  javascript: () => import("shiki/langs/javascript.mjs"),
  js: () => import("shiki/langs/js.mjs"),
  json: () => import("shiki/langs/json.mjs"),
  json5: () => import("shiki/langs/json5.mjs"),
  jsonc: () => import("shiki/langs/jsonc.mjs"),
  jsx: () => import("shiki/langs/jsx.mjs"),
  kotlin: () => import("shiki/langs/kotlin.mjs"),
  less: () => import("shiki/langs/less.mjs"),
  lua: () => import("shiki/langs/lua.mjs"),
  markdown: () => import("shiki/langs/markdown.mjs"),
  md: () => import("shiki/langs/md.mjs"),
  mdx: () => import("shiki/langs/mdx.mjs"),
  php: () => import("shiki/langs/php.mjs"),
  py: () => import("shiki/langs/py.mjs"),
  python: () => import("shiki/langs/python.mjs"),
  rb: () => import("shiki/langs/rb.mjs"),
  rs: () => import("shiki/langs/rs.mjs"),
  ruby: () => import("shiki/langs/ruby.mjs"),
  rust: () => import("shiki/langs/rust.mjs"),
  scss: () => import("shiki/langs/scss.mjs"),
  sql: () => import("shiki/langs/sql.mjs"),
  swift: () => import("shiki/langs/swift.mjs"),
  toml: () => import("shiki/langs/toml.mjs"),
  ts: () => import("shiki/langs/ts.mjs"),
  tsx: () => import("shiki/langs/tsx.mjs"),
  typescript: () => import("shiki/langs/typescript.mjs"),
  vue: () => import("shiki/langs/vue.mjs"),
  xml: () => import("shiki/langs/xml.mjs"),
  yaml: () => import("shiki/langs/yaml.mjs"),
  yml: () => import("shiki/langs/yml.mjs"),
};

/**
 * Themes used by the app ("one-light"/"one-dark-pro" in code-block.tsx)
 * and by streamdown's defaults ("github-light"/"github-dark").
 */
const THEME_LOADERS: Record<string, ModuleLoader> = {
  "one-light": () => import("shiki/themes/one-light.mjs"),
  "one-dark-pro": () => import("shiki/themes/one-dark-pro.mjs"),
  "github-light": () => import("shiki/themes/github-light.mjs"),
  "github-dark": () => import("shiki/themes/github-dark.mjs"),
};

function resolveLanguageInput(input: unknown): unknown {
  if (typeof input !== "string") return input;
  return LANGUAGE_LOADERS[input] ?? null;
}

function resolveThemeInput(input: unknown): unknown {
  if (typeof input !== "string") return input;
  const loader = THEME_LOADERS[input];
  if (!loader) {
    throw new Error(`[shiki-shim] Unsupported theme: ${input}`);
  }
  return loader;
}

/**
 * Matches shiki's `bundledLanguages` shape closely enough for consumers
 * that probe support with `Object.hasOwn(bundledLanguages, name)` (e.g.
 * streamdown). Only whitelisted names report as supported; everything
 * else falls back to plain text upstream.
 */
export const bundledLanguages = LANGUAGE_LOADERS;

type CreateHighlighterOptions = {
  themes?: unknown[];
  langs?: unknown[];
  engine?: unknown;
};

/**
 * Drop-in replacement for shiki's `createHighlighter` that additionally
 * accepts plain language/theme NAME strings (the full bundle resolves
 * them against its bundled registry; the core highlighter does not) by
 * mapping them onto the whitelist loaders above.
 */
export async function createHighlighter(
  options: CreateHighlighterOptions = {},
) {
  const engine =
    options.engine ?? createJavaScriptRegexEngine({ forgiving: true });
  const highlighter = await createHighlighterCore({
    themes: (options.themes ?? []).map(resolveThemeInput) as never[],
    langs: (options.langs ?? [])
      .map(resolveLanguageInput)
      .filter(Boolean) as never[],
    engine: engine as never,
  });
  const coreLoadLanguage = highlighter.loadLanguage.bind(highlighter);
  highlighter.loadLanguage = ((...langs: unknown[]) =>
    coreLoadLanguage(
      ...(langs.map(resolveLanguageInput).filter(Boolean) as never[]),
    )) as typeof highlighter.loadLanguage;
  return highlighter;
}

type Highlighter = Awaited<ReturnType<typeof createHighlighter>>;

type CodeToHtmlOptions = {
  lang: string;
  theme: string;
  transformers?: unknown[];
};

const highlighterCache = new Map<string, Promise<Highlighter>>();
// Per-theme map of language → in-flight (or settled) load promise. Sharing
// the PROMISE — not just a "loaded" flag — is what makes concurrent
// codeToHtml calls safe: marking a flag before `loadLanguage` resolves let
// a second caller skip the wait and hit shiki's "Language not found".
const loadedLanguages = new Map<string, Map<string, Promise<unknown>>>();

function getHighlighter(theme: string): Promise<Highlighter> {
  let pending = highlighterCache.get(theme);
  if (!pending) {
    pending = createHighlighter({ themes: [theme], langs: [] });
    highlighterCache.set(theme, pending);
    loadedLanguages.set(theme, new Map());
  }
  return pending;
}

/**
 * Drop-in replacement for shiki's `codeToHtml`. Maintains one lazily
 * created highlighter per theme and loads whitelisted grammars on demand;
 * non-whitelisted languages render as plain text (shiki core special-cases
 * "text"/"plain"/"plaintext"/"txt" without needing a grammar).
 */
export async function codeToHtml(code: string, options: CodeToHtmlOptions) {
  const { theme } = options;
  const lang = LANGUAGE_LOADERS[options.lang] ? options.lang : "text";
  const highlighter = await getHighlighter(theme);
  const loaded = loadedLanguages.get(theme);
  if (loaded && lang !== "text") {
    // Concurrent highlights of the same language await the SAME load
    // promise — whether it was just created here or is already in flight
    // from an earlier caller — so nobody proceeds before the grammar is
    // actually registered on the highlighter instance.
    let load = loaded.get(lang);
    if (!load) {
      load = highlighter
        .loadLanguage(lang as never)
        .then(() => undefined)
        .catch((error: unknown) => {
          // Un-cache the failed load so a later call can retry instead of
          // caching a rejection forever.
          loaded.delete(lang);
          throw error;
        });
      loaded.set(lang, load);
    }
    await load;
  }
  return highlighter.codeToHtml(code, {
    ...options,
    lang,
    transformers: options.transformers as never[],
  });
}
