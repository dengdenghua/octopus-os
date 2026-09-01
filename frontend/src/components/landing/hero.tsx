import { ChevronRightIcon } from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { FlickeringGrid } from "@/components/ui/flickering-grid";
import { WordRotate } from "@/components/ui/word-rotate";
import { GITHUB_URL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

const echoMaskUrl = `${import.meta.env.BASE_URL}images/echo.svg`;

export function Hero({ className }: { className?: string }) {
  const { t } = useI18n();
  return (
    <div
      className={cn(
        "flex size-full flex-col items-center justify-center",
        className,
      )}
    >
      <div className="absolute inset-0 z-0 bg-black/40">
        <div className="css-starfield size-full" />
      </div>
      <FlickeringGrid
        className="absolute inset-0 z-0 translate-y-8 mask-size-[100vw] mask-center mask-no-repeat md:mask-size-[72vh]"
        style={{
          maskImage: `url(${echoMaskUrl})`,
          WebkitMaskImage: `url(${echoMaskUrl})`,
        }}
        squareSize={4}
        gridGap={4}
        color={"white"}
        maxOpacity={0.3}
        flickerChance={0.25}
      />
      <div className="absolute inset-0 z-0 bg-gradient-to-t from-black/80 via-transparent to-black/40" />
      <div className="container-md relative z-10 mx-auto flex h-screen flex-col items-center justify-center">
        <div className="mb-6 flex items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-4 py-1.5 text-xs font-medium text-white/60 backdrop-blur-sm">
          <span className="size-1.5 rounded-lg bg-emerald-400 animate-pulse-soft" />
          {t.hero.releaseBadge}
        </div>
        <h1 className="flex items-center gap-2 text-4xl font-bold md:text-6xl">
          <WordRotate
            className="text-white"
            words={[
              "Deep Research",
              "Collect Data",
              "Analyze Data",
              "Generate Webpages",
              "Vibe Coding",
              "Generate Slides",
              "Generate Images",
              "Generate Podcasts",
              "Generate Videos",
              "Generate Songs",
              "Organize Emails",
              "Do Anything",
              "Learn Anything",
            ]}
          />{" "}
          <div className="text-white/90">{t.hero.withEcho}</div>
        </h1>
        <p className="mt-8 max-w-2xl text-center text-lg leading-relaxed text-[rgb(184,184,192)] md:text-xl">
          {t.hero.heroDescription}
        </p>
        <div className="mt-8 flex items-center gap-4">
          <Link to="/workspace">
            <Button
              className="size-lg bg-white text-[#08080c] shadow-lg shadow-black/25 transition-all hover:bg-white/90"
              size="lg"
            >
              <span className="text-md">Get Started</span>
              <ChevronRightIcon className="size-4" />
            </Button>
          </Link>
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-5 py-2.5 text-sm font-medium text-white/70 backdrop-blur-sm transition-all hover:border-white/20 hover:bg-white/10 hover:text-white"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z" />
            </svg>
            Star on GitHub
          </a>
        </div>
      </div>
    </div>
  );
}
