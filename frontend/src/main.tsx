import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AppRouter } from "./router";
import { ThemeProvider } from "./components/theme-provider";
import { I18nProvider } from "./core/i18n/context";
import { getLocaleFromCookie } from "./core/i18n/cookies";
import { detectLocale, normalizeLocale } from "./core/i18n/locale";
import { loadTranslations } from "./core/i18n/translations";
import { AuthProvider } from "./providers/AuthProvider";
import { AppearanceBootstrap } from "./hooks/use-appearance";
import { installPageAgentBridge } from "./core/page-agent-bridge";
import { installHashRouterShellUrlNormalizer } from "./core/router/hash-shell-url";
import { normalizeLoopbackOrigin } from "./core/router/loopback-origin";
import { migrateLegacyEchoStorage } from "./core/storage/echo-storage";

import "./styles/globals.css";

const queryClient = new QueryClient();

async function bootstrap() {
  if (normalizeLoopbackOrigin()) return;

  migrateLegacyEchoStorage();
  installHashRouterShellUrlNormalizer();
  installPageAgentBridge();

  const savedLocale = getLocaleFromCookie();
  const initialLocale = savedLocale
    ? normalizeLocale(savedLocale)
    : detectLocale();
  const initialTranslations = await loadTranslations(initialLocale);

  createRoot(document.getElementById("root")!).render(
    <HashRouter>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider>
          <I18nProvider
            initialLocale={initialLocale}
            initialTranslations={initialTranslations}
          >
            <AuthProvider>
              <AppearanceBootstrap />
              <AppRouter />
            </AuthProvider>
          </I18nProvider>
        </ThemeProvider>
      </QueryClientProvider>
    </HashRouter>,
  );
}

void bootstrap();
