import type { Locale } from "./locale";
import type { Translations } from "./locales";

const loaders: Record<Locale, () => Promise<Translations>> = {
  "en-US": () => import("./locales/en-US").then((module) => module.enUS),
  "zh-CN": () => import("./locales/zh-CN").then((module) => module.zhCN),
  "ja-JP": () => import("./locales/ja-JP").then((module) => module.jaJP),
  "ko-KR": () => import("./locales/ko-KR").then((module) => module.koKR),
};

const translationCache = new Map<Locale, Translations>();

export async function loadTranslations(locale: Locale): Promise<Translations> {
  const cached = translationCache.get(locale);
  if (cached) {
    return cached;
  }
  const loaded = await loaders[locale]();
  translationCache.set(locale, loaded);
  return loaded;
}

export function primeTranslations(
  locale: Locale,
  translations: Translations,
): void {
  translationCache.set(locale, translations);
}
