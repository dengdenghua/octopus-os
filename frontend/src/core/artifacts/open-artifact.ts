import { workspaceOutputRef } from "./utils";

export const OPEN_ARTIFACT_EVENT = "echo:open-artifact";

export type OpenArtifactDetail = { path: string };

const ARTIFACT_EXTENSION =
  /\.(?:pdf|docx?|xlsx?|pptx?|csv|tsv|html?|md|markdown|txt|json|svg|png|jpe?g|webp)(?:[?#].*)?$/i;

export function artifactRefFromMarkdownHref(href: string): string | null {
  const value = href.trim();
  if (!value || !ARTIFACT_EXTENSION.test(value)) return null;
  if (/^(?:mailto|tel|data|javascript):/i.test(value)) return null;

  if (value.startsWith("workspace-output:")) return value;

  if (/^https?:\/\//i.test(value)) {
    try {
      const url = new URL(value);
      if (url.origin !== window.location.origin) return null;
      return artifactRefFromOutputApiUrl(url);
    } catch {
      return null;
    }
  }

  if (value.startsWith("/api/threads/")) {
    try {
      return artifactRefFromOutputApiUrl(
        new URL(value, window.location.origin),
      );
    } catch {
      return null;
    }
  }

  const clean = safeDecodeURIComponent(value.split(/[?#]/, 1)[0] ?? value)
    .replace(/^\.\//, "")
    .replace(/^\/+/, "");
  if (clean.startsWith("output/final/")) {
    return workspaceOutputRef({
      area: "final",
      relativePath: clean.slice("output/final/".length),
    });
  }
  if (clean.startsWith("output/stages/")) {
    return workspaceOutputRef({
      area: "stages",
      relativePath: clean.slice("output/stages/".length),
    });
  }
  if (clean.startsWith("out/") || clean.startsWith("final/")) {
    return workspaceOutputRef({ area: "final", relativePath: clean });
  }
  if (clean.startsWith("output/")) {
    return workspaceOutputRef({
      area: "output",
      relativePath: clean.slice("output/".length),
    });
  }

  // Absolute local paths are normalized against the current thread by the
  // realtime page. Relative filenames without an output marker remain native
  // links because guessing their workspace area would open the wrong file.
  return value.startsWith("/") && !value.startsWith("//") ? value : null;
}

function artifactRefFromOutputApiUrl(url: URL): string | null {
  const match = /^\/api\/threads\/[^/]+\/outputs\/(.+)$/.exec(url.pathname);
  if (!match?.[1]) return null;
  const area = url.searchParams.get("area") ?? "output";
  if (!["output", "stages", "final", "deploy", "upload"].includes(area)) {
    return null;
  }
  return workspaceOutputRef({
    area: area as "output" | "stages" | "final" | "deploy" | "upload",
    relativePath: safeDecodeURIComponent(match[1]),
  });
}

function safeDecodeURIComponent(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export function dispatchOpenArtifact(path: string): boolean {
  const event = new CustomEvent<OpenArtifactDetail>(OPEN_ARTIFACT_EVENT, {
    cancelable: true,
    detail: { path },
  });
  return !window.dispatchEvent(event);
}
