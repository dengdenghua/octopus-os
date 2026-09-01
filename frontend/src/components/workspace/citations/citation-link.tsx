import { swallow } from "@/core/utils/log";
import { ExternalLinkIcon } from "lucide-react";
import type { ComponentProps } from "react";

import { Badge } from "@/components/ui/badge";
import { RoutedWebLink } from "@/components/ui/routed-web-link";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { cn } from "@/lib/utils";

export function CitationLink({
  href,
  children,
  ...props
}: ComponentProps<"a">) {
  const domain = extractDomain(href ?? "");

  // Priority: children > domain
  const childrenText =
    typeof children === "string"
      ? children.replace(/^citation:\s*/i, "")
      : null;
  const isGenericText = childrenText === "Source" || childrenText === "来源";
  const displayText = (!isGenericText && childrenText) ?? domain;

  return (
    <HoverCard closeDelay={0} openDelay={0}>
      <HoverCardTrigger asChild>
        <RoutedWebLink
          href={href}
          openTargetSource="citation"
          className="inline-flex items-center"
          onClick={(e) => e.stopPropagation()}
          {...props}
        >
          <Badge
            variant="secondary"
            className="hover:bg-secondary/80 mx-0.5 cursor-pointer gap-1 rounded-lg px-2 py-0.5 text-xs font-normal"
          >
            {displayText}
            <ExternalLinkIcon className="size-3" />
          </Badge>
        </RoutedWebLink>
      </HoverCardTrigger>
      <HoverCardContent className={cn("relative w-80 p-0", props.className)}>
        <div className="p-3">
          <div className="space-y-1">
            {displayText && (
              <h4 className="truncate text-sm leading-tight font-medium">
                {displayText}
              </h4>
            )}
            {href && (
              <p className="text-muted-foreground truncate text-xs break-all">
                {href}
              </p>
            )}
          </div>
          <RoutedWebLink
            href={href}
            openTargetSource="citation-preview"
            className="text-primary mt-2 inline-flex items-center gap-1 text-xs hover:underline"
          >
            Visit source
            <ExternalLinkIcon className="size-3" />
          </RoutedWebLink>
        </div>
      </HoverCardContent>
    </HoverCard>
  );
}

function extractDomain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./i, "");
  } catch (e) {
    swallow(e);
    return url;
  }
}
