// Audit direct localStorage / sessionStorage access in production code.
// Goal: surface sites that bypass the React-aware `useLocalStorage` hook
// or that don't guard for SSR (where `localStorage` is undefined).
import { readFile, readdir } from "node:fs/promises";
import { join, relative, sep } from "node:path";

const SRC = process.argv[2] ?? "src";
const SKIP_TEST = /\.(test|spec)\.(ts|tsx|mjs|js)$/;
const SKIP_DTS = /\.d\.ts$/;
const STORAGE_RE = /\b(localStorage|sessionStorage)\b/;

function relPosix(p) {
  return relative(process.cwd(), p).split(sep).join("/");
}

async function* walk(dir) {
  for (const e of await readdir(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) {
      if (e.name === "node_modules" || e.name === ".next" || e.name === "dist")
        continue;
      yield* walk(p);
    } else if (/\.(ts|tsx|mjs|js)$/.test(e.name)) {
      yield p;
    }
  }
}

const hits = [];
for await (const f of walk(SRC)) {
  if (SKIP_TEST.test(f) || SKIP_DTS.test(f)) continue;
  const src = await readFile(f, "utf8");
  const lines = src.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    if (STORAGE_RE.test(lines[i])) {
      hits.push({ file: relPosix(f), line: i + 1, text: lines[i].trim() });
    }
  }
}

// Group by file
const byFile = new Map();
for (const h of hits) {
  if (!byFile.has(h.file)) byFile.set(h.file, []);
  byFile.get(h.file).push(h);
}

console.log(
  `--- localStorage / sessionStorage direct access — ${hits.length} hits in ${byFile.size} files ---`,
);
for (const [file, list] of [...byFile.entries()].sort(
  (a, b) => b[1].length - a[1].length,
)) {
  console.log(`\n  ${file}  (${list.length})`);
  for (const h of list) {
    console.log(`    L${h.line}  ${h.text}`);
  }
}
