// Custom ts-prune replacement.
// Static-only, no codegen. Resolves every import path to a real
// project file (handling relative, @/ alias, and index.* fallbacks),
// then reports files whose exports are never imported from anywhere
// in the source tree (excluding tests + entrypoints).

import { readdir, readFile, stat } from "node:fs/promises";
import { extname, isAbsolute, join, relative, resolve, sep } from "node:path";

const ROOT = resolve(process.cwd());
const SRC = join(ROOT, "src");

const ENTRY = new Set(
  [
    "src/main.tsx",
    "src/router.tsx",
    "src/types/electron.d.ts",
    "src/vite-env.d.ts",
  ].map((p) => p.split(sep).join("/")),
);

const TS_EXT = [".ts", ".tsx", ".jsx", ".js"];

async function* walk(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  for (const e of entries) {
    if (e.name === "node_modules" || e.name === "dist" || e.name === "coverage")
      continue;
    const full = join(dir, e.name);
    if (e.isDirectory()) yield* walk(full);
    else if (e.isFile()) yield full;
  }
}

function relPosix(p) {
  return relative(ROOT, p).split(sep).join("/");
}

function stripExt(p) {
  for (const ext of TS_EXT) {
    if (p.endsWith(ext)) return p.slice(0, -ext.length);
  }
  return p;
}

// All source files
const allFiles = [];
for await (const f of walk(SRC)) {
  const ext = extname(f);
  if (!TS_EXT.includes(ext)) continue;
  if (f.endsWith(".d.ts")) continue;
  allFiles.push(f);
}
const allRelNoExt = new Set(allFiles.map((f) => stripExt(relPosix(f))));

// Resolve a specifier from a given importer back to a project file.
// Returns relative POSIX path (no extension) or null if not found.
function resolveImport(importerAbs, spec) {
  if (spec.startsWith("@/")) {
    const target = stripExt(join(SRC, spec.slice(2)));
    return resolveWithExtensions(target);
  }
  if (spec.startsWith(".") || isAbsolute(spec)) {
    const fromDir = importerAbs.replace(/[\\/][^\\/]+$/, "");
    const target = stripExt(resolve(fromDir, spec));
    return resolveWithExtensions(target);
  }
  return null; // bare specifier (package)
}

function resolveWithExtensions(noExt) {
  // Exact match
  if (allRelNoExt.has(relPosix(noExt))) return relPosix(noExt);
  // /index match
  const idx = noExt + "/index";
  if (allRelNoExt.has(relPosix(idx))) return relPosix(idx);
  return null;
}

// Walk the source tree and collect: { file -> exports }, { name -> Set<importers> }
const exportsByFile = new Map(); // relNoExt -> Map<name, kind>
const usageByName = new Map(); // name -> Set<importerRelNoExt>

for (const f of allFiles) {
  const src = await readFile(f, "utf8");
  const m = new Map();
  // export const|function|class|interface|type|enum|let|var|namespace X
  const re1 =
    /export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|let|var|interface|type|enum|namespace)\s+([A-Za-z_$][A-Za-z0-9_$]*)/g;
  let mm;
  while ((mm = re1.exec(src)) !== null) {
    m.set(mm[1], "named");
  }
  // export { a, b as c } from "x"  OR  export { a, b }
  const re2 = /export\s*\{\s*([^}]+)\s*\}(?:\s+from\s+["']([^"']+)["'])?/g;
  while ((mm = re2.exec(src)) !== null) {
    const names = mm[1].split(",").map((s) =>
      s
        .trim()
        .split(/\s+as\s+/)[0]
        .trim(),
    );
    for (const n of names)
      if (n && /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(n)) m.set(n, "re-export");
  }
  if (/export\s+default\s+/.test(src)) m.set("default", "default");
  exportsByFile.set(stripExt(relPosix(f)), m);
}

for (const f of allFiles) {
  const src = await readFile(f, "utf8");
  const meRel = stripExt(relPosix(f));
  // import { a, b } from "x"
  const re1 = /import\s*\{\s*([^}]+)\s*\}\s*from\s*["']([^"']+)["']/g;
  let mm;
  while ((mm = re1.exec(src)) !== null) {
    const names = mm[1].split(",").map((s) =>
      s
        .trim()
        .split(/\s+as\s+/)[0]
        .trim(),
    );
    for (const n of names) {
      if (n) {
        let s = usageByName.get(n);
        if (!s) {
          s = new Set();
          usageByName.set(n, s);
        }
        s.add(meRel);
      }
    }
  }
  // import X from "x"  (default)
  const re2 = /import\s+([A-Za-z_$][A-Za-z0-9_$]*)\s+from\s*["']([^"']+)["']/g;
  while ((mm = re2.exec(src)) !== null) {
    let s = usageByName.get(mm[1]);
    if (!s) {
      s = new Set();
      usageByName.set(mm[1], s);
    }
    s.add(meRel);
  }
  // import X, { a, b } from "x"  (default + named, same regex catches default)
  // import * as X from "x"
  const re3 =
    /import\s*\*\s*as\s+([A-Za-z_$][A-Za-z0-9_$]*)\s+from\s*["']([^"']+)["']/g;
  while ((mm = re3.exec(src)) !== null) {
    let s = usageByName.get(mm[1]);
    if (!s) {
      s = new Set();
      usageByName.set(mm[1], s);
    }
    s.add(meRel);
  }
  // import "x" (side-effect, no names, but file is reached)
  // dynamic import() — for now skip
}

// Reachability: which files are imported by any other file?
const reachedFiles = new Set();
for (const f of allFiles) {
  const src = await readFile(f, "utf8");
  // All import specifiers
  const re =
    /(?:import\s+(?:[^"';]+\s+from\s+)?|import\s*\(\s*|export\s*\*\s*from\s*|export\s*\{[^}]+\}\s*from\s*)["']([^"']+)["']/g;
  let mm;
  while ((mm = re.exec(src)) !== null) {
    const resolved = resolveImport(f, mm[1]);
    if (resolved) reachedFiles.add(resolved);
  }
}

// Now build report.
const orphanFiles = [];
const unusedExports = []; // {file, name, kind} for files that ARE reached
for (const [file, m] of exportsByFile) {
  if (ENTRY.has(file + ".tsx") || ENTRY.has(file + ".ts")) continue;
  const isReached = reachedFiles.has(file);
  if (!isReached) {
    orphanFiles.push(file);
    continue;
  }
  for (const [name, kind] of m) {
    const users = usageByName.get(name);
    const isUsed = users && [...users].some((u) => u !== file);
    if (!isUsed) unusedExports.push({ file, name, kind });
  }
}

const totalExports = [...exportsByFile.values()].reduce(
  (a, m) => a + m.size,
  0,
);
const summary = {
  totalFiles: allFiles.length,
  totalExports,
  orphanFileCount: orphanFiles.length,
  reachedUnusedExportCount: unusedExports.length,
};
// dedupe orphan files list
const orphanDedup = [...new Set(orphanFiles)].sort();

process.stdout.write(
  JSON.stringify(
    {
      summary,
      orphanFiles: orphanDedup,
      unusedExports: unusedExports.slice(0, 200),
    },
    null,
    2,
  ) + "\n",
);
