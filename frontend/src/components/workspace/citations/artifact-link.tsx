import type { AnchorHTMLAttributes } from "react";

import { RoutedWebLink } from "@/components/ui/routed-web-link";
import {
  artifactRefFromMarkdownHref,
  dispatchOpenArtifact,
} from "@/core/artifacts/open-artifact";
import { cn } from "@/lib/utils";

import { CitationLink } from "./citation-link";

/** Link renderer for artifact markdown: citation: prefix → CitationLink, otherwise underlined text. */
export function ArtifactLink(props: AnchorHTMLAttributes<HTMLAnchorElement>) {
  if (typeof props.children === "string") {
    const match = /^citation:(.+)$/.exec(props.children);
    if (match) {
      const [, text] = match;
      return <CitationLink {...props}>{text}</CitationLink>;
    }
  }
  const { className, onClick, ...rest } = props;
  return (
    <RoutedWebLink
      {...rest}
      openTargetSource="artifact-markdown"
      onClick={(event) => {
        onClick?.(event);
        if (event.defaultPrevented || !props.href) return;
        const artifactRef = artifactRefFromMarkdownHref(props.href);
        if (artifactRef && dispatchOpenArtifact(artifactRef)) {
          event.preventDefault();
          event.stopPropagation();
        }
      }}
      className={cn(
        "text-primary decoration-primary/30 hover:decoration-primary/60 underline underline-offset-2 transition-colors",
        className,
      )}
    />
  );
}
