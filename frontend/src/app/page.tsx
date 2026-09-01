import { useNavigate } from "react-router-dom";
import {
  ArrowRight,
  Github,
  Brain,
  Workflow,
  Plug,
  Shield,
  Layers,
  Globe,
} from "lucide-react";
import { GITHUB_URL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";

const featureMeta = [
  { icon: Brain, key: "deepResearch" as const, title: "Planner" },
  { icon: Workflow, key: "multiAgent" as const, title: "Workers" },
  { icon: Plug, key: "skillsTools" as const, title: "Skills & Tools" },
  { icon: Shield, key: "sandbox" as const, title: "Sandbox" },
  { icon: Layers, key: "memory" as const, title: "Memory & Journal" },
  { icon: Globe, key: "multiChannel" as const, title: "Surfaces" },
];

export default function HomePage() {
  const navigate = useNavigate();
  const { t } = useI18n();

  return (
    <div className="min-h-screen w-full bg-[#08080c] relative overflow-hidden">
      <div className="absolute inset-0 css-starfield" />

      <div
        className="relative z-10 flex flex-col items-center justify-center min-h-screen animate-fade-in cursor-pointer"
        onClick={() => navigate("/workspace")}
      >
        <div className="text-center">
          <div className="mb-4 inline-flex items-center gap-2 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-1 text-[11px] text-white/60 backdrop-blur-sm">
            <span className="size-1.5 rounded-lg bg-white/35" />
            Agent OS control surface
          </div>
          <div className="mb-8 animate-float">
            <div className="relative mx-auto size-[72px]">
              <div className="absolute inset-[-6px] rounded-xl border border-white/[0.06] bg-white/[0.025] blur-xl" />
              <div className="relative flex size-[72px] items-center justify-center rounded-xl border border-white/12 bg-white/[0.04] text-white/80 shadow-sm shadow-black/25">
                <svg width="36" height="36" viewBox="0 0 512 512" fill="none">
                  <path
                    d="M256 32C167.6 32 96 103.6 96 192c0 52.8 25.6 99.6 65.2 128.8C128 348 96 404 96 448c0 17.7 14.3 32 32 32s32-14.3 32-32c0-28 16-68 40-96 8 4 16.4 7.2 25.2 9.6-4 26.4-9.2 56-9.2 86.4 0 17.7 14.3 32 32 32s32-14.3 32-32c0-26.4 4-52 8-76 12-2.4 23.6-6 34.8-11.2C348 384 368 420 368 448c0 17.7 14.3 32 32 32s32-14.3 32-32c0-48-36-108-72-147.2C399.6 271.6 416 233.6 416 192c0-88.4-71.6-160-160-160zm0 64c53 0 96 43 96 96s-43 96-96 96-96-43-96-96 43-96 96-96z"
                    fill="currentColor"
                  />
                  <circle cx="224" cy="176" r="20" fill="currentColor" />
                  <circle cx="288" cy="176" r="20" fill="currentColor" />
                  <circle cx="228" cy="180" r="10" fill="#08080c" />
                  <circle cx="292" cy="180" r="10" fill="#08080c" />
                </svg>
              </div>
            </div>
          </div>

          <h1 className="mb-2 text-5xl font-bold tracking-tight md:text-7xl">
            <span className="inline-block text-white">Echo</span>
          </h1>

          <p className="mb-1.5 max-w-lg text-lg text-white/45 md:text-xl">
            {t.landing.tagline}
          </p>

          <p className="mb-8 max-w-sm text-sm leading-relaxed text-white/25 md:text-base">
            {t.landing.subtitle}
          </p>

          <div className="flex items-center justify-center gap-2.5">
            <button
              className="group flex items-center gap-2 rounded-lg bg-white px-5 py-2.5 text-sm font-medium text-[#08080c] shadow-lg shadow-black/20 ring-1 ring-white/10 transition-all duration-200 hover:bg-white/90 active:scale-[0.97]"
              onClick={(e) => {
                e.stopPropagation();
                navigate("/workspace");
              }}
            >
              {t.landing.getStarted}
              <ArrowRight className="size-3.5 transition-transform duration-200 group-hover:translate-x-0.5" />
            </button>

            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 rounded-lg border border-white/[0.06] bg-white/[0.025] px-5 py-2.5 text-sm font-medium text-white/50 backdrop-blur-sm transition-all duration-200 hover:border-white/[0.12] hover:bg-white/[0.05] hover:text-white/70 active:scale-[0.97]"
              onClick={(e) => e.stopPropagation()}
            >
              <Github className="size-3.5" />
              GitHub
            </a>
          </div>
        </div>

        <div className="absolute bottom-6 flex items-center gap-1.5 text-[11px] text-white/15 animate-pulse-soft">
          <span className="size-1 rounded-lg bg-white/20" />
          {t.landing.clickToEnter}
        </div>
      </div>

      <div className="relative z-10 mx-auto max-w-5xl px-6 pb-20">
        <div className="mb-5 flex items-center justify-between gap-4">
          <div>
            <div className="text-xs uppercase tracking-[0.18em] text-white/35">
              Agent OS Runtime
            </div>
            <div className="mt-1 text-sm text-white/55">
              {t.landing.capabilitiesPanel}
            </div>
          </div>
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {featureMeta.map((f) => (
            <div
              key={f.title}
              className="group relative overflow-hidden rounded-lg border border-white/[0.05] bg-white/[0.018] p-5 backdrop-blur-sm transition-all duration-300 hover:-translate-y-0.5 hover:border-white/[0.1] hover:bg-white/[0.03] hover:shadow-lg hover:shadow-black/20"
            >
              <div className="mb-3 flex size-8 items-center justify-center rounded-lg border border-white/[0.08] bg-white/[0.04] text-white/70 shadow-sm shadow-black/20">
                <f.icon className="size-3.5" />
              </div>
              <h3 className="mb-0.5 text-[13px] font-semibold text-white/80">
                {f.title}
              </h3>
              <p className="text-[11px] leading-relaxed text-white/30">
                {t.landing.features[f.key]}
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
