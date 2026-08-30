import { Github as GitHubLogoIcon } from "lucide-react";
import { useMemo } from "react";

import { GITHUB_URL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { EchoMark } from "@/components/brand/echo-mark";

export function Footer() {
  const { t } = useI18n();
  const year = useMemo(() => new Date().getFullYear(), []);

  // Implementation note.
  // registered and the page was superseded by ``/workspace/workflows``
  // Implementation note.
  // a mkdocs path the SPA doesn't ship · catches 404 in the router
  // catch-all, so it now points at the About page we actually ship.
  const links = useMemo(
    () => [
      {
        title: t.landingFooter.productTitle,
        items: [
          { label: t.landingFooter.workspaceLink, href: "/workspace" },
          { label: t.landingFooter.aboutLink, href: "/about" },
        ],
      },
      {
        title: t.landingFooter.resourcesTitle,
        items: [
          { label: "GitHub", href: GITHUB_URL },
          { label: t.landingFooter.skillMarketLink, href: "/workspace" },
        ],
      },
      {
        title: t.landingFooter.communityTitle,
        items: [
          { label: "Discord", href: "#" },
          { label: t.landingFooter.wechat, href: "#" },
        ],
      },
    ],
    [t],
  );

  return (
    <footer className="container-md mx-auto mt-32">
      <hr className="from-border/0 to-border/0 m-0 h-px w-full border-none bg-linear-to-r via-white/20" />

      <div className="grid grid-cols-2 gap-8 py-12 md:grid-cols-4">
        <div className="col-span-2 md:col-span-1">
          <div className="flex items-center gap-2 mb-3">
            <div className="flex size-6 items-center justify-center rounded-lg border border-white/12 bg-white/[0.04] text-white/80">
              <EchoMark tone="light" className="size-3.5" />
            </div>
            <span className="text-lg font-bold text-white/80">Echo</span>
          </div>
          <p className="text-xs leading-relaxed text-white/40">
            {t.landingFooter.tagline}
          </p>
        </div>

        {links.map((group) => (
          <div key={group.title}>
            <h3 className="mb-3 text-sm font-semibold text-white/70">
              {group.title}
            </h3>
            <ul className="space-y-2">
              {group.items.map((item) => (
                <li key={item.label}>
                  <a
                    href={item.href}
                    className="text-sm text-white/40 transition-colors hover:text-white/70"
                  >
                    {item.label}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      <div className="flex flex-col items-center justify-center gap-3 border-t border-white/[0.06] py-6">
        <div className="flex items-center gap-3">
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="text-white/30 transition-colors hover:text-white/60"
          >
            <GitHubLogoIcon className="size-4" />
          </a>
        </div>
        <div className="flex items-center gap-1.5 text-xs text-white/30">
          <span>&copy; {year}</span>
          <span className="font-semibold text-white/45">Echo</span>
          <span>· MIT License</span>
        </div>
      </div>
    </footer>
  );
}
