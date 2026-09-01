import { Star as StarFilledIcon, Github as GitHubLogoIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { EchoMark } from "@/components/brand/echo-mark";
import { NumberTicker } from "@/components/ui/number-ticker";
import { swallow } from "@/core/utils/log";
import { GITHUB_URL } from "@/core/config";
import type { Locale } from "@/core/i18n/locale";
import { useI18n } from "@/core/i18n/hooks";
import { env } from "@/env";
import { cn } from "@/lib/utils";

export type HeaderProps = {
  className?: string;
  homeURL?: string;
  /** Kept for API compat · no longer consumed · locale resolves via
   *  the surrounding ``I18nProvider``. */
  locale?: Locale;
};

// NOTE · 2026-04-24 · was ``async function Header({...})`` using the
// Next.js RSC ``await getI18n()`` pattern. React (SPA) doesn't allow
// async client components · every /about page load emitted:
//   "<Header> is an async Client Component. Only Server Components
//    can be async at the moment."
// Switched to the ``useI18n()`` hook which the surrounding
// ``I18nProvider`` (src/main.tsx) already guarantees. ``locale`` prop
// is retained for caller compat · ignored internally.
export function Header({ className, homeURL }: HeaderProps) {
  const isExternalHome = !homeURL;
  const { t } = useI18n();
  return (
    <header
      className={cn(
        "container-md fixed top-0 right-0 left-0 z-20 mx-auto flex h-16 items-center justify-between backdrop-blur-xs",
        className,
      )}
    >
      <div className="flex items-center gap-6">
        <a
          href={homeURL ?? GITHUB_URL}
          target={isExternalHome ? "_blank" : "_self"}
          rel={isExternalHome ? "noopener noreferrer" : undefined}
          className="group/logo flex items-center gap-2 transition-opacity hover:opacity-80"
        >
          <div className="flex size-7 items-center justify-center rounded-lg border border-white/12 bg-white/[0.04] text-white/80 shadow-sm">
            <EchoMark tone="light" className="size-4" />
          </div>
          <h1 className="text-xl font-bold text-white/90">Echo</h1>
        </a>
      </div>
      <nav className="mr-8 ml-auto flex items-center gap-8 text-sm font-medium">
        {/* Pre-fix ``/:lang/docs`` (resolving to "/en/docs") pointed
            at the mkdocs site we haven't deployed · router catch-all
            404'd every click from the landing header. Swap to the
            in-app ``/about`` until proper docs hosting lands. */}
        <Link
          to="/about"
          className="relative text-white/60 transition-colors after:absolute after:-bottom-0.5 after:left-0 after:h-0.5 after:w-0 after:bg-white/40 after:transition-all after:duration-300 hover:text-white/90 hover:after:w-full"
        >
          {t.home.docs}
        </Link>
      </nav>
      <div className="relative">
        <Button
          variant="outline"
          size="sm"
          asChild
          className="group relative z-10"
        >
          <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
            <GitHubLogoIcon className="size-4" />
            Star on GitHub
            {env.STATIC_WEBSITE_ONLY && env.GITHUB_OAUTH_TOKEN && (
              <StarCounter />
            )}
          </a>
        </Button>
      </div>
      <hr className="from-border/0 via-border/70 to-border/0 absolute top-16 right-0 left-0 z-10 m-0 h-px w-full border-none bg-linear-to-r" />
    </header>
  );
}

// NOTE · same async-in-SPA trap as Header above · rewritten to
// useEffect + useState so React can render it as a regular client
// component.
function StarCounter() {
  const [stars, setStars] = useState(10000);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(
          "https://api.github.com/repos/dengdenghua/echo-os",
          {
            headers: env.GITHUB_OAUTH_TOKEN
              ? {
                  Authorization: `Bearer ${env.GITHUB_OAUTH_TOKEN}`,
                  "Content-Type": "application/json",
                }
              : {},
          },
        );
        if (!response.ok || cancelled) return;
        const data = await response.json();
        if (!cancelled && typeof data?.stargazers_count === "number") {
          setStars(data.stargazers_count);
        }
      } catch (e) {
        swallow(e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  return (
    <>
      <StarFilledIcon
        className="size-4 transition-colors duration-300 group-hover:text-yellow-500"
        fill="currentColor"
      />
      {stars > 0 && (
        <NumberTicker className="font-mono tabular-nums" value={stars} />
      )}
    </>
  );
}
