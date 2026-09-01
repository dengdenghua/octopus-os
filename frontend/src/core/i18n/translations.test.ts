import { describe, expect, it } from "vitest";

import { SUPPORTED_LOCALES, type Locale } from "./locale";
import { enUS, jaJP, koKR, zhCN, type Translations } from "./locales";
import { loadTranslations } from "./translations";

const TRANSLATIONS_BY_LOCALE: Record<Locale, Translations> = {
  "en-US": enUS,
  "zh-CN": zhCN,
  "ja-JP": jaJP,
  "ko-KR": koKR,
};

describe("translation bundles", () => {
  it("has a static bundle for every supported locale", () => {
    expect(Object.keys(TRANSLATIONS_BY_LOCALE).sort()).toEqual(
      [...SUPPORTED_LOCALES].sort(),
    );
  });

  it("keeps every locale structurally aligned with en-US", () => {
    const expectedShape = collectShape(enUS);

    for (const locale of SUPPORTED_LOCALES) {
      expect(collectShape(TRANSLATIONS_BY_LOCALE[locale]), locale).toEqual(
        expectedShape,
      );
    }
  });

  it("loads and caches every supported locale", async () => {
    for (const locale of SUPPORTED_LOCALES) {
      const first = await loadTranslations(locale);
      const second = await loadTranslations(locale);

      expect(first, locale).toBe(TRANSLATIONS_BY_LOCALE[locale]);
      expect(second, locale).toBe(first);
    }
  });

  // Guards against untranslated placeholder drift: non-en-US locales must not
  // carry values identical to en-US (except whitelisted URLs/brand tokens and
  // technical identifiers like "clientId"/"appSecret" that stay English in
  // every locale). 2026-07-28: ko-KR had ~3.4k English placeholders; this test
  // prevents regression while ja/ko backfill is in progress.
  it("non-en-US locales do not reuse en-US string values wholesale", () => {
    const enLeaves = collectStringLeaves(enUS);
    const remainingByLocale = new Map<Locale, string[]>();
    for (const locale of SUPPORTED_LOCALES) {
      if (locale === "en-US") continue;
      const leaves = collectStringLeaves(TRANSLATIONS_BY_LOCALE[locale]);
      const identical: string[] = [];
      for (const [path, value] of leaves) {
        if (enLeaves.get(path) !== value) continue;
        if (!isTranslationExempt(value)) identical.push(path);
      }
      if (identical.length > 0) remainingByLocale.set(locale, identical);
    }
    // Print a diagnostic inventory so CI logs show the full drift picture.
    if (remainingByLocale.size > 0) {
      const lines = [...remainingByLocale.entries()].map(
        ([locale, paths]) => `${locale}: ${paths.length} — ${paths.join(", ")}`,
      );
      expect.soft(lines, "untranslated en-US placeholders by locale").toEqual([]);
    }
  });
});

const STRING_LEAF_SKIP_PATHS = new Set(["$.workspaceComputer", "$.agentOperator"]);

function collectStringLeaves(
  value: unknown,
  path = "$",
  out = new Map<string, string>(),
): Map<string, string> {
  if (typeof value === "string") {
    out.set(path, value);
    return out;
  }
  if (typeof value === "function" || Array.isArray(value)) return out;
  if (value !== null && typeof value === "object") {
    if (STRING_LEAF_SKIP_PATHS.has(path)) return out;
    for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
      collectStringLeaves(child, `${path}.${key}`, out);
    }
  }
  return out;
}

// Exempt patterns: values that are intentionally identical across all locales
// (URLs, email addresses, file paths, ICU templates, pure technical identifiers,
// brand names, emoji-prefixed tokens, HTTP header examples, etc.)
const TRANSLATION_EXEMPT_RE =
  /^(https?:\/\/|mailto:|[\s\p{P}\p{S}\d]*$|[A-Z][\w+./#-]*$|[a-z][\w-]*$)/u;

function isTranslationExempt(value: string): boolean {
  const trimmed = value.trim();
  if (trimmed.length <= 1) return true;
  if (TRANSLATION_EXEMPT_RE.test(trimmed)) return true;
  // Email addresses
  if (/^[\w.+-]+@[\w.-]+\.\w+$/.test(trimmed)) return true;
  // File paths (e.g. docs/constitution.md)
  if (/^\w+\/[\w/.-]+\.\w+$/.test(trimmed)) return true;
  // Pure ICU message templates (e.g. "{action} {target}")
  if (/^\{[\w,]+\}(\s*\{[\w,]+\})*\s*$/.test(trimmed)) return true;
  // HTTP header examples (e.g. "User-Agent: ...")
  if (/^[A-Z][\w-]+:\s/.test(trimmed)) return true;
  // Emoji-prefixed or symbol-prefixed short tokens (common emoji ranges)
  if (/^[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(trimmed)) return true;
  // Placeholder examples (e.g. "skill-a, skill-b")
  if (/^[\w-]+(,\s*[\w-]+)+/.test(trimmed)) return true;
  // CJK characters are their own translation (e.g. "中文" in all locales)
  if (/[\u4e00-\u9fff]/.test(trimmed)) return true;
  // Brand name + punctuation (e.g. "Arms ·", "URL:")
  if (/^[A-Z]\w+\s*[\u00B7:].*$/.test(trimmed)) return true;
  return false;
}

const DYNAMIC_RECORD_PATHS = new Set([
  "$.personality.categories",
  "$.personality.templateDescriptions",
  "$.personality.templateNames",
  // These bundles use English-source-string keys: en-US is intentionally
  // empty (the key IS the value), other locales map English→translation.
  // Shape comparison must treat them as opaque records, not expand keys.
  "$.workspaceComputer",
  "$.agentOperator",
]);

function collectShape(value: unknown, path = "$"): string[] {
  if (Array.isArray(value)) {
    return [`${path}:array`];
  }

  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    const keys = Object.keys(record).sort();
    if (DYNAMIC_RECORD_PATHS.has(path)) {
      return [`${path}:object`];
    }
    return [
      `${path}:object:${keys.join(",")}`,
      ...keys.flatMap((key) => collectShape(record[key], `${path}.${key}`)),
    ];
  }

  return [`${path}:${typeof value}`];
}
