// @ts-check

/**
 * Convert a package name into a stable, readable Rollup chunk suffix.
 *
 * @param {string} value
 */
export function safeChunkName(value) {
  return value
    .replace(/[^a-zA-Z0-9_-]/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

/**
 * Return the package name for a dependency path under node_modules.
 *
 * @param {string} id
 * @returns {string | null}
 */
export function packageNameFromNodeModule(id) {
  const normalized = id.replace(/\\/g, "/");
  const marker = "/node_modules/";
  const markerIndex = normalized.lastIndexOf(marker);
  if (markerIndex < 0) return null;
  const parts = normalized.slice(markerIndex + marker.length).split("/");
  if (parts[0]?.startsWith("@")) {
    return parts.length >= 2 ? `${parts[0]}/${parts[1]}` : null;
  }
  return parts[0] || null;
}

/**
 * Keep editor and diagram dependencies in bounded, independently cached
 * chunks. Returning undefined deliberately leaves Mermaid itself to Rollup:
 * Mermaid already uses dynamic imports for its diagram engines.
 *
 * @param {string | null} pkg
 * @returns {string | undefined}
 */
export function heavyDependencyChunk(pkg) {
  if (pkg === "@uiw/react-codemirror") return "codemirror-react";
  if (pkg?.startsWith("@uiw/codemirror-theme-")) {
    return `codemirror-${safeChunkName(pkg)}`;
  }
  if (pkg?.startsWith("@codemirror/")) {
    return `codemirror-${safeChunkName(pkg)}`;
  }
  if (pkg === "codemirror") return "codemirror-core";
  if (pkg?.startsWith("@lezer/")) {
    return `lezer-${safeChunkName(pkg)}`;
  }
  if (pkg === "mermaid") return undefined;
  if (
    pkg === "cytoscape" ||
    pkg === "dagre-d3-es" ||
    pkg === "elkjs" ||
    pkg === "khroma"
  ) {
    return `diagram-${safeChunkName(pkg)}`;
  }
  return undefined;
}

/**
 * Shared dependency split policy for the main app and standalone workbenches.
 * Mermaid intentionally remains on its native dynamic-import boundaries.
 *
 * @param {string} id
 * @returns {string | undefined}
 */
export function manualChunks(id) {
  const normalized = id.replace(/\\/g, "/");
  const pkg = packageNameFromNodeModule(id);
  if (
    normalized.includes("node_modules/react-dom") ||
    normalized.includes("node_modules/react/") ||
    normalized.includes("node_modules/react-router-dom")
  ) {
    return "react-vendor";
  }
  if (normalized.includes("node_modules/@radix-ui/")) return "ui-radix";
  if (pkg?.startsWith("@tanstack/")) return "query-virtual";
  if (pkg === "streamdown") return "markdown-streamdown";
  if (
    pkg?.startsWith("rehype-") ||
    pkg?.startsWith("remark-") ||
    pkg === "unified" ||
    pkg === "hast" ||
    pkg === "unist-util-visit"
  ) {
    return "markdown-plugins";
  }
  if (pkg === "katex") return "katex";
  if (pkg === "lodash-es") return "lodash-es";
  if (
    pkg === "@uiw/react-codemirror" ||
    pkg?.startsWith("@uiw/codemirror-theme-") ||
    pkg?.startsWith("@codemirror/") ||
    pkg === "codemirror" ||
    pkg?.startsWith("@lezer/")
  ) {
    return heavyDependencyChunk(pkg);
  }
  if (pkg === "mermaid") return undefined;
  if (
    pkg === "cytoscape" ||
    pkg === "dagre-d3-es" ||
    pkg === "elkjs" ||
    pkg === "khroma"
  ) {
    return heavyDependencyChunk(pkg);
  }
  return undefined;
}
