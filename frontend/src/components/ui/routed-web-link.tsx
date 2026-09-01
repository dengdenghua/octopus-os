import type { ComponentProps, MouseEvent } from "react";

import {
  isWebTarget,
  openTarget,
  shouldRouteAnchorClick,
} from "@/core/navigation/open-target";
import type { LinkOpenTarget } from "@/core/settings/automation-preferences";

export interface RoutedWebLinkProps extends ComponentProps<"a"> {
  openTargetSource?: string;
  openTargetPreference?: LinkOpenTarget;
}

/** `example.com/page` → `https://example.com/page`; anything else untouched. */
const BARE_HOST = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+(?=$|[/?#:])/i;

function promoteBareHost(href: string): string {
  // Leave in-app paths, fragments, queries and anything already carrying a
  // scheme or protocol-relative prefix alone — only a bare host qualifies.
  if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith("//")) return href;
  if (href.startsWith("/") || href.startsWith("#") || href.startsWith("?")) {
    return href;
  }
  return BARE_HOST.test(href) ? `https://${href}` : href;
}

/**
 * A normal content link that follows the user's app-vs-external preference.
 * Modified clicks keep native browser behaviour; explicit external actions
 * should continue to use a plain anchor or ``openTarget(..., external)``.
 */
export function RoutedWebLink({
  href,
  onClick,
  openTargetSource = "content",
  openTargetPreference,
  target,
  rel,
  download,
  ...props
}: RoutedWebLinkProps) {
  // Callers feed this hrefs harvested from tool output — web-search results,
  // deep-research sources (`result.url`, `source.url`, `item.url`). Those can
  // arrive schemeless ("example.com/page"), which `isWebTarget` rejects, so the
  // link neither routes nor gets `rel`, and the browser silently resolves it
  // against the current SPA route into a dead in-app URL. Promote a bare
  // host-looking href to https so it behaves like the external link it is.
  // (`javascript:`/`data:` need no handling here: React blocks the former
  // outright and `isWebTarget` keeps both off the routing path.)
  const resolvedHref = href && !isWebTarget(href) ? promoteBareHost(href) : href;
  const webTarget = !!resolvedHref && isWebTarget(resolvedHref);
  const resolvedTarget = target ?? (webTarget ? "_blank" : undefined);
  const resolvedRel =
    rel ?? (resolvedTarget === "_blank" ? "noopener noreferrer" : undefined);

  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (
      event.defaultPrevented ||
      download !== undefined ||
      !resolvedHref ||
      !webTarget ||
      !shouldRouteAnchorClick(event.nativeEvent)
    ) {
      return;
    }
    event.preventDefault();
    void openTarget(resolvedHref, {
      source: openTargetSource,
      target: openTargetPreference,
    });
  };

  return (
    <a
      href={resolvedHref}
      target={resolvedTarget}
      rel={resolvedRel}
      download={download}
      onClick={handleClick}
      {...props}
    />
  );
}
