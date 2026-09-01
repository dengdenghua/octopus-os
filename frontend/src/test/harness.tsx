/**
 * Shared test harness · wraps a component tree with the providers
 * that pages / hooks assume at runtime.
 *
 * Providers included (in order):
 *   1. QueryClientProvider · for any `useQuery` / `useMutation` hook.
 *   2. I18nProvider · for `useI18n()` · primed with en-US so tests
 *      see stable copy regardless of system locale.
 *   3. MemoryRouter · for `<Link>` / `useNavigate()` usage · initial
 *      route configurable per test.
 *
 * We intentionally do NOT wrap AuthProvider here · every test that
 * cares about auth either mocks `@/providers/AuthProvider` directly
 * (see `src/app/login/page.test.tsx`) or uses an auth-free page.
 * Wrapping a real AuthProvider would make tests hit the live `/api/
 * auth/status` endpoint, which is flaky in CI.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import { type ReactElement, type ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import { I18nProvider } from "@/core/i18n/context";
import { enUS, jaJP, koKR, zhCN, type Translations } from "@/core/i18n/locales";
import type { Locale } from "@/core/i18n";

function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false, // don't retry in tests · keep errors deterministic
        gcTime: Infinity, // never GC during a single test render
      },
      mutations: {
        retry: false,
      },
    },
  });
}

export interface AllProvidersOptions {
  initialRoute?: string;
  queryClient?: QueryClient;
  locale?: Locale;
}

export function AllProviders({
  children,
  initialRoute = "/",
  queryClient,
  locale = "en-US",
}: {
  children: ReactNode;
} & AllProvidersOptions) {
  const client = queryClient ?? makeQueryClient();
  const translations = TRANSLATIONS_BY_LOCALE[locale];
  // Seed the locale cookie so ``useI18n()``'s first useEffect
  // Implementation note.
  // mount) doesn't stomp the initial locale back to the jsdom
  // default. Without this the trigger label stays zh-CN (from
  // initialTranslations) but async-loaded child views silently
  // race back to en-US, giving confusing mixed-language DOM.
  if (typeof document !== "undefined") {
    // Clear any stale locale cookie from previous tests before seeding the
    // expected locale, so ``getLocaleFromCookie()`` always reads the value
    // we intend for this render.
    document.cookie = "locale=; path=/; max-age=0; SameSite=Lax";
    document.cookie = `locale=${encodeURIComponent(locale)}; path=/`;
  }
  return (
    <QueryClientProvider client={client}>
      <I18nProvider initialLocale={locale} initialTranslations={translations}>
        <MemoryRouter initialEntries={[initialRoute]}>{children}</MemoryRouter>
      </I18nProvider>
    </QueryClientProvider>
  );
}

const TRANSLATIONS_BY_LOCALE: Record<Locale, Translations> = {
  "en-US": enUS,
  "zh-CN": zhCN,
  "ja-JP": jaJP,
  "ko-KR": koKR,
};

/** Convenience render that wraps with AllProviders in one call. */
export function renderWithProviders(
  ui: ReactElement,
  opts: AllProvidersOptions & Omit<RenderOptions, "wrapper"> = {},
) {
  const { initialRoute, queryClient, locale, ...renderOpts } = opts;
  return render(ui, {
    wrapper: ({ children }) => (
      <AllProviders
        initialRoute={initialRoute}
        queryClient={queryClient}
        locale={locale}
      >
        {children}
      </AllProviders>
    ),
    ...renderOpts,
  });
}
