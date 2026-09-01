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
