import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { useEffect, useRef, type ComponentType } from "react";
import { Toaster } from "sonner";

import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@/styles/globals.css";

import { ThemeProvider } from "@/components/theme-provider";
import { WorkbenchSurfaceProvider } from "@/core/workbench/workbench-surface";
import { installAuthFetchInterceptor } from "@/core/auth/fetch-interceptor";
import { getLocaleFromCookie } from "@/core/i18n/cookies";
import { I18nProvider } from "@/core/i18n/context";
import { detectLocale, normalizeLocale } from "@/core/i18n/locale";
import type { Translations } from "@/core/i18n/locales";
import { loadTranslations } from "@/core/i18n/translations";
import { AppearanceBootstrap } from "@/hooks/use-appearance";
import { AuthProvider } from "@/providers/AuthProvider";

interface HostContextMessage {
  type?: unknown;
  route?: unknown;
  colorScheme?: unknown;
  locale?: unknown;
}

function safeWorkspaceRoute(value: string | null, fallback: string): string {
  if (!value) return fallback;
  try {
    const parsed = new URL(value, window.location.origin);
    if (
      parsed.origin === window.location.origin &&
      parsed.pathname.startsWith("/workspace/")
    ) {
      return `${parsed.pathname}${parsed.search}${parsed.hash}`;
    }
  } catch {
    // The fallback is a trusted manifest constant.
  }
  return fallback;
}

function trustedHostOrigin(): string {
  const requested = new URLSearchParams(window.location.search).get(
    "echo_host_origin",
  );
  if (requested) {
    try {
      const parsed = new URL(requested);
      if (parsed.protocol === "http:" || parsed.protocol === "https:") {
        return parsed.origin;
      }
    } catch {
      // Fall through to the embedding document/referrer origin.
    }
  }
  try {
    return document.referrer
      ? new URL(document.referrer).origin
      : window.location.origin;
  } catch {
    return window.location.origin;
  }
}

function HostBridge() {
  const location = useLocation();
  const navigate = useNavigate();
  const firstLocation = useRef(true);
  const hostOrigin = useRef(trustedHostOrigin());

  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (
        event.source !== window.parent ||
        event.origin !== hostOrigin.current
      ) {
        return;
      }
      const payload = event.data as HostContextMessage | null;
      if (payload?.type !== "echo.host.context") return;
      if (payload.colorScheme === "dark") {
        document.documentElement.classList.add("dark");
      } else if (payload.colorScheme === "light") {
        document.documentElement.classList.remove("dark");
      }
      if (typeof payload.locale === "string" && payload.locale) {
        document.documentElement.lang = payload.locale;
      }
      if (typeof payload.route === "string") {
        const route = safeWorkspaceRoute(payload.route, "");
        const current = `${location.pathname}${location.search}${location.hash}`;
        if (route && route !== current) navigate(route, { replace: true });
      }
    };
    window.addEventListener("message", receive);
    window.parent.postMessage(
      { type: "echo.workbench.ready" },
      hostOrigin.current,
    );
    return () => window.removeEventListener("message", receive);
  }, [location.hash, location.pathname, location.search, navigate]);

  useEffect(() => {
    if (firstLocation.current) {
      firstLocation.current = false;
      return;
    }
    window.parent.postMessage(
      {
        type: "echo.workbench.navigate",
        href: `${location.pathname}${location.search}${location.hash}`,
      },
      hostOrigin.current,
    );
  }, [location.hash, location.pathname, location.search]);

  return null;
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export async function mountStandaloneWorkbench(
  Page: ComponentType,
  fallbackRoute: string,
): Promise<void> {
  installAuthFetchInterceptor();
  const params = new URLSearchParams(window.location.search);
  const initialRoute = safeWorkspaceRoute(
    params.get("echo_host_path"),
    fallbackRoute,
  );
  const initialLocale = normalizeLocale(
    getLocaleFromCookie() || document.documentElement.lang || detectLocale(),
  );
  document.documentElement.lang = initialLocale;

  let translations: Translations;
  try {
    translations = await loadTranslations(initialLocale);
  } catch {
    translations = await loadTranslations("en-US");
  }

  const root = document.getElementById("root");
  if (!root) throw new Error("workbench root element is missing");
  createRoot(root).render(
    <MemoryRouter initialEntries={[initialRoute]}>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider defaultTheme="system" storageKey="echo-theme">
          <I18nProvider
            initialLocale={initialLocale}
            initialTranslations={translations}
          >
            <AuthProvider>
              <AppearanceBootstrap />
              <WorkbenchSurfaceProvider surface="browser">
                <div className="h-screen min-h-0 overflow-hidden bg-background text-foreground">
                  <HostBridge />
                  <Page />
                  <Toaster position="top-center" />
                </div>
              </WorkbenchSurfaceProvider>
            </AuthProvider>
          </I18nProvider>
        </ThemeProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}
