// Vite SPA: server-side cookie access not needed.
// These functions use client-side document.cookie instead.

import { DEFAULT_LOCALE, normalizeLocale, type Locale } from "./locale";
import type { Translations } from "./locales";
import { getLocaleFromCookie, setLocaleInCookie } from "./cookies";
import { loadTranslations } from "./translations";

export function detectLocaleServer(): Locale {
  return normalizeLocale(getLocaleFromCookie() ?? undefined);
}

export function setLocale(locale: string | Locale): Locale {
  const normalizedLocale = normalizeLocale(locale);
  setLocaleInCookie(normalizedLocale);
  return normalizedLocale;
}

export async function getI18n(localeOverride?: string | Locale): Promise<{
  locale: Locale;
  t: Translations;
}> {
  const locale = localeOverride
    ? normalizeLocale(localeOverride)
    : detectLocaleServer();
  const t = await loadTranslations(locale).catch(() =>
    loadTranslations(DEFAULT_LOCALE),
  );
  return { locale, t };
}
