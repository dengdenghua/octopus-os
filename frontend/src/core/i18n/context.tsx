import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import type { Locale } from "@/core/i18n";
import { setLocaleInCookie } from "@/core/i18n/cookies";
import type { Translations } from "@/core/i18n/locales";
import { loadTranslations, primeTranslations } from "@/core/i18n/translations";

export interface I18nContextType {
  locale: Locale;
  t: Translations;
  setLocale: (locale: Locale) => Promise<void>;
}

export const I18nContext = createContext<I18nContextType | null>(null);

export function I18nProvider({
  children,
  initialLocale,
  initialTranslations,
}: {
  children: ReactNode;
  initialLocale: Locale;
  initialTranslations: Translations;
}) {
  const [locale, setLocale] = useState<Locale>(initialLocale);
  const [t, setTranslations] = useState<Translations>(initialTranslations);
  const requestSeqRef = useRef(0);

  primeTranslations(initialLocale, initialTranslations);

  const handleSetLocale = useCallback(async (newLocale: Locale) => {
    const requestId = ++requestSeqRef.current;
    setLocale(newLocale);
    setLocaleInCookie(newLocale);
    const nextTranslations = await loadTranslations(newLocale);
    if (requestId !== requestSeqRef.current) {
      return;
    }
    setTranslations(nextTranslations);
  }, []);

  const value = useMemo<I18nContextType>(
    () => ({ locale, t, setLocale: handleSetLocale }),
    [locale, t, handleSetLocale],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18nContext() {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error("useI18n must be used within I18nProvider");
  }
  return context;
}
