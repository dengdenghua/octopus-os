// Audit `as` casts in production source, grouped by suspicion level.
// Heuristics (from safest to riskiest):
//   0. `as const`             — TS-narrowing idiom, totally safe
//   1. `as const [...]` array narrowing — same
//   2. `as string|number|...` simple union — usually safe
//   3. `as <known interface>` — context-dependent
//   4. `as unknown as <T>`    — DOUBLE CAST, bypasses safety, high risk
//   5. `as any`               — already audited separately (1 site)
//   6. `as Record<...>`       — common escape hatch, low risk
//
// Output top-N per category to make this actionable.
import { readFile, readdir } from "node:fs/promises";
import { join, relative, sep } from "node:path";

const SRC = process.argv[2] ?? "src";
const TOP = Number(process.argv[3] ?? 10);
const SKIP_TEST = /\.(test|spec)\.(ts|tsx|mjs|js)$/;
const SKIP_DTS = /\.d\.ts$/;
const AS_RE = /\bas\b/g;

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

const buckets = {
  "as const": [],
  "as <known-type>": [],
  "as Record<...>": [],
  "as unknown as <T> (DOUBLE CAST)": [],
  "as any": [],
  other: [],
};

for await (const f of walk(SRC)) {
  if (SKIP_TEST.test(f) || SKIP_DTS.test(f)) continue;
  const src = await readFile(f, "utf8");
  const lines = src.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    let m;
    AS_RE.lastIndex = 0;
    while ((m = AS_RE.exec(line)) !== null) {
      const after = line.slice(m.index + 2).trim();
      let bucket = "other";
      if (/^const\b/.test(after)) bucket = "as const";
      else if (/^any\b/.test(after)) bucket = "as any";
      else if (/^unknown\s+as\b/.test(after))
        bucket = "as unknown as <T> (DOUBLE CAST)";
      else if (/^Record\s*</.test(after)) bucket = "as Record<...>";
      else if (/^[A-Z][A-Za-z0-9_]*\b/.test(after)) bucket = "as <known-type>";
      const text = line.trim();
      if (text.length < 250) {
        buckets[bucket].push({ file: relPosix(f), line: i + 1, text });
      }
    }
  }
}

for (const [bucket, hits] of Object.entries(buckets)) {
  if (hits.length === 0) continue;
  console.log(`\n--- ${bucket} — ${hits.length} hits (showing top ${TOP}) ---`);
  for (const h of hits.slice(0, TOP)) {
    console.log(`  ${h.file}:${h.line}  →  ${h.text}`);
  }
  if (hits.length > TOP) {
    console.log(`  ...and ${hits.length - TOP} more`);
  }
}
