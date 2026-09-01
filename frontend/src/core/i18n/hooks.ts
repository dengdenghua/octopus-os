import { useEffect, useState } from "react";

import { useI18nContext } from "./context";
import { getLocaleFromCookie, setLocaleInCookie } from "./cookies";

import { detectLocale, normalizeLocale, type Locale } from "./index";

export function useI18n() {
  const { locale, setLocale, t } = useI18nContext();
  const [isClient, setIsClient] = useState(false);

  const changeLocale = (newLocale: Locale) => {
    void setLocale(newLocale);
    setLocaleInCookie(newLocale);
    document.documentElement.lang = newLocale.split("-")[0] ?? newLocale;
  };

  // Initialize locale on mount
  useEffect(() => {
    setIsClient(true);
    const saved = getLocaleFromCookie();
    if (saved) {
      const normalizedSaved = normalizeLocale(saved);
      void setLocale(normalizedSaved);
      if (saved !== normalizedSaved) {
        setLocaleInCookie(normalizedSaved);
      }
      return;
    }

    const detected = detectLocale();
    void setLocale(detected);
    setLocaleInCookie(detected);
  }, [setLocale]);

  return {
    locale,
    t,
    changeLocale,
    isClient,
  };
}
