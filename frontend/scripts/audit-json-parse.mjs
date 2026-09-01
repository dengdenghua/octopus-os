// Scan every JSON.parse(...) call in production source and verify
// it is wrapped in a try block. Tests are skipped.
//
// Outputs a list of "unguarded" parse sites that are real risks.

import { readFile, readdir } from "node:fs/promises";
import { join, relative, sep } from "node:path";

const ROOT = ".";
const SRC = "src";

async function* walk(dir) {
  for (const e of await readdir(dir, { withFileTypes: true })) {
    if (e.name === "node_modules" || e.name === "dist") continue;
    const full = join(dir, e.name);
    if (e.isDirectory()) yield* walk(full);
    else if (e.isFile()) yield full;
  }
}

const findings = [];
for await (const f of walk(SRC)) {
  if (!/\.(ts|tsx)$/.test(f)) continue;
  if (/\.(test|spec)\.(ts|tsx)$/.test(f)) continue;
  if (f.endsWith(".d.ts")) continue;
  const src = await readFile(f, "utf8");
  const lines = src.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/JSON\.parse\s*\(/);
    if (!m) continue;
    let inTry = false;
    for (let j = i; j >= 0; j--) {
      if (/\}\s*catch\s*[({]/.test(lines[j])) {
        inTry = false;
        break;
      }
      if (/\btry\s*\{/.test(lines[j])) {
        inTry = true;
        break;
      }
    }
    if (!inTry) {
      findings.push({
        file: relative(ROOT, f).split(sep).join("/"),
        line: i + 1,
        text: lines[i].trim(),
      });
    }
  }
}

if (findings.length === 0) {
  console.log(
    "All production JSON.parse() sites are protected by try/catch. ✅",
  );
  console.log("(Tests, .d.ts, and benchmark files are excluded.)");
} else {
  console.log(
    `Found ${findings.length} UNGUARDED JSON.parse() in production code:`,
  );
  for (const f of findings) {
    console.log(`  ${f.file}:${f.line}  →  ${f.text}`);
  }
}
