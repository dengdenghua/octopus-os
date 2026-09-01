/**
 * Diff computation utilities.
 *
 * Provides Myers-based line diff, word-level diff for changed lines,
 * hunk parsing from unified diff format, and syntax highlighting helpers.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type DiffLineType = "add" | "remove" | "context" | "header";

export interface DiffLine {
  type: DiffLineType;
  content: string;
  oldLineNumber: number | null;
  newLineNumber: number | null;
}

export interface WordDiffSegment {
  text: string;
  type: "equal" | "add" | "remove";
}

export interface DiffHunk {
  id: string;
  header: string;
  oldStart: number;
  oldCount: number;
  newStart: number;
  newCount: number;
  lines: DiffLine[];
  accepted: boolean | null; // null = pending
}

export interface FileDiff {
  id: string;
  filePath: string;
  status: "added" | "modified" | "deleted";
  additions: number;
  deletions: number;
  hunks: DiffHunk[];
  originalContent: string | null;
  newContent: string | null;
  accepted: boolean | null;
  timestamp: number;
}

export type SortMode = "name" | "status" | "changes";
export type ViewMode = "side-by-side" | "unified";

// ---------------------------------------------------------------------------
// Line-level diff (Myers algorithm, simplified)
// ---------------------------------------------------------------------------

interface EditOp {
  type: "equal" | "insert" | "delete";
  oldIdx: number;
  newIdx: number;
  length: number;
}

/**
 * Compute the longest common subsequence length table and return edit
 * operations. This is a simple O(NM) approach suitable for files with
 * reasonable line counts.
 */
function computeEditOps(oldLines: string[], newLines: string[]): EditOp[] {
  const m = oldLines.length;
  const n = newLines.length;

  // Build LCS table
  const dp: number[][] = Array.from({ length: m + 1 }, () =>
    new Array<number>(n + 1).fill(0),
  );

  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (oldLines[i - 1] === newLines[j - 1]) {
        dp[i]![j] = dp[i - 1]![j - 1]! + 1;
      } else {
        dp[i]![j] = Math.max(dp[i - 1]![j]!, dp[i]![j - 1]!);
      }
    }
  }

  // Backtrack to get edit operations
  const ops: EditOp[] = [];
  let i = m;
  let j = n;

  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
      ops.unshift({ type: "equal", oldIdx: i - 1, newIdx: j - 1, length: 1 });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || dp[i]![j - 1]! >= dp[i - 1]![j]!)) {
      ops.unshift({ type: "insert", oldIdx: i, newIdx: j - 1, length: 1 });
      j--;
    } else {
      ops.unshift({ type: "delete", oldIdx: i - 1, newIdx: j, length: 1 });
      i--;
    }
  }

  return ops;
}

/**
 * Generate DiffLine arrays from two text contents.
 */
export function computeLineDiff(
  oldText: string,
  newText: string,
  contextLines = 3,
): DiffHunk[] {
  const oldLines = oldText.split("\n");
  const newLines = newText.split("\n");
  const ops = computeEditOps(oldLines, newLines);

  // Convert ops to a flat list of raw diff lines
  const allLines: Array<{
    type: DiffLineType;
    content: string;
    oldLine: number | null;
    newLine: number | null;
  }> = [];

  for (const op of ops) {
    if (op.type === "equal") {
      allLines.push({
        type: "context",
        content: oldLines[op.oldIdx]!,
        oldLine: op.oldIdx + 1,
        newLine: op.newIdx + 1,
      });
    } else if (op.type === "delete") {
      allLines.push({
        type: "remove",
        content: oldLines[op.oldIdx]!,
        oldLine: op.oldIdx + 1,
        newLine: null,
      });
    } else {
      allLines.push({
        type: "add",
        content: newLines[op.newIdx]!,
        oldLine: null,
        newLine: op.newIdx + 1,
      });
    }
  }

  // Group into hunks with context
  return groupIntoHunks(allLines, contextLines);
}

function groupIntoHunks(
  allLines: Array<{
    type: DiffLineType;
    content: string;
    oldLine: number | null;
    newLine: number | null;
  }>,
  contextLines: number,
): DiffHunk[] {
  // Find ranges of changed lines
  const changeIndices: number[] = [];
  for (let i = 0; i < allLines.length; i++) {
    if (allLines[i]!.type !== "context") {
      changeIndices.push(i);
    }
  }

  if (changeIndices.length === 0) return [];

  // Group change indices into hunk ranges (merge if close)
  const ranges: Array<[number, number]> = [];
  let rangeStart = changeIndices[0]!;
  let rangeEnd = changeIndices[0]!;

  for (let i = 1; i < changeIndices.length; i++) {
    if (changeIndices[i]! - rangeEnd <= contextLines * 2 + 1) {
      rangeEnd = changeIndices[i]!;
    } else {
      ranges.push([rangeStart, rangeEnd]);
      rangeStart = changeIndices[i]!;
      rangeEnd = changeIndices[i]!;
    }
  }
  ranges.push([rangeStart, rangeEnd]);

  // Create hunks
  const hunks: DiffHunk[] = [];

  for (const [start, end] of ranges) {
    const hunkStart = Math.max(0, start - contextLines);
    const hunkEnd = Math.min(allLines.length - 1, end + contextLines);

    const lines: DiffLine[] = [];
    let oldStart = 0;
    let newStart = 0;
    let oldCount = 0;
    let newCount = 0;

    for (let i = hunkStart; i <= hunkEnd; i++) {
      const line = allLines[i]!;
      lines.push({
        type: line.type,
        content: line.content,
        oldLineNumber: line.oldLine,
        newLineNumber: line.newLine,
      });

      if (i === hunkStart) {
        oldStart = line.oldLine ?? 1;
        newStart = line.newLine ?? 1;
      }

      if (line.type === "context") {
        oldCount++;
        newCount++;
      } else if (line.type === "remove") {
        oldCount++;
      } else if (line.type === "add") {
        newCount++;
      }
    }

    const header = `@@ -${oldStart},${oldCount} +${newStart},${newCount} @@`;
    hunks.push({
      id: hashHunkId(`${oldStart}:${oldCount}:${newStart}:${newCount}`),
      header,
      oldStart,
      oldCount,
      newStart,
      newCount,
      lines,
      accepted: null,
    });
  }

  return hunks;
}

// ---------------------------------------------------------------------------
// Word-level diff (for highlighting changed characters)
// ---------------------------------------------------------------------------

/**
 * Compute word-level diff between two lines for fine-grained highlighting.
 */
export function computeWordDiff(
  oldLine: string,
  newLine: string,
): { oldSegments: WordDiffSegment[]; newSegments: WordDiffSegment[] } {
  const oldTokens = tokenize(oldLine);
  const newTokens = tokenize(newLine);

  const ops = computeEditOps(oldTokens, newTokens);
  const oldSegments: WordDiffSegment[] = [];
  const newSegments: WordDiffSegment[] = [];

  for (const op of ops) {
    if (op.type === "equal") {
      oldSegments.push({ text: oldTokens[op.oldIdx]!, type: "equal" });
      newSegments.push({ text: newTokens[op.newIdx]!, type: "equal" });
    } else if (op.type === "delete") {
      oldSegments.push({ text: oldTokens[op.oldIdx]!, type: "remove" });
    } else {
      newSegments.push({ text: newTokens[op.newIdx]!, type: "add" });
    }
  }

  return { oldSegments, newSegments };
}

/**
 * Tokenize a line into words and whitespace for word-level diff.
 */
function tokenize(line: string): string[] {
  const tokens: string[] = [];
  let current = "";
  let inWord = false;

  for (const ch of line) {
    const isWhitespace = /\s/.test(ch);
    const isSymbol = /[^\w\s]/.test(ch);

    if (isWhitespace || isSymbol) {
      if (current) {
        tokens.push(current);
        current = "";
      }
      tokens.push(ch);
      inWord = false;
    } else {
      if (!inWord && current) {
        tokens.push(current);
        current = "";
      }
      current += ch;
      inWord = true;
    }
  }

  if (current) tokens.push(current);
  return tokens;
}

// ---------------------------------------------------------------------------
// Unified diff parsing (for server-provided diffs)
// ---------------------------------------------------------------------------

/**
 * Parse a unified diff string into DiffHunk objects.
 */
export function parseUnifiedDiff(diffText: string): DiffHunk[] {
  if (!diffText) return [];

  const lines = diffText.split("\n");
  const hunks: DiffHunk[] = [];
  let currentHunk: DiffHunk | null = null;
  let oldLine = 0;
  let newLine = 0;

  for (const line of lines) {
    // Hunk header
    if (line.startsWith("@@")) {
      if (currentHunk) hunks.push(currentHunk);

      const match = line.match(
        /@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)/,
      );
      if (match) {
        const oldStart = parseInt(match[1]!, 10);
        const oldCount = match[2] ? parseInt(match[2], 10) : 1;
        const newStart = parseInt(match[3]!, 10);
        const newCount = match[4] ? parseInt(match[4], 10) : 1;

        oldLine = oldStart;
        newLine = newStart;

        currentHunk = {
          id: hashHunkId(`${oldStart}:${oldCount}:${newStart}:${newCount}`),
          header: line,
          oldStart,
          oldCount,
          newStart,
          newCount,
          lines: [],
          accepted: null,
        };
      }
      continue;
    }

    // Skip file headers
    if (line.startsWith("---") || line.startsWith("+++")) continue;

    if (!currentHunk) continue;

    if (line.startsWith("+")) {
      currentHunk.lines.push({
        type: "add",
        content: line.slice(1),
        oldLineNumber: null,
        newLineNumber: newLine++,
      });
    } else if (line.startsWith("-")) {
      currentHunk.lines.push({
        type: "remove",
        content: line.slice(1),
        oldLineNumber: oldLine++,
        newLineNumber: null,
      });
    } else if (line.startsWith(" ") || line === "") {
      currentHunk.lines.push({
        type: "context",
        content: line.startsWith(" ") ? line.slice(1) : line,
        oldLineNumber: oldLine++,
        newLineNumber: newLine++,
      });
    }
  }

  if (currentHunk) hunks.push(currentHunk);
  return hunks;
}

// ---------------------------------------------------------------------------
// Side-by-side alignment
// ---------------------------------------------------------------------------

export interface SideBySideLine {
  left: DiffLine | null;
  right: DiffLine | null;
}

/**
 * Align diff lines for side-by-side view.
 * Context lines appear on both sides, removals on left, additions on right.
 */
export function alignSideBySide(lines: DiffLine[]): SideBySideLine[] {
  const result: SideBySideLine[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i]!;

    if (line.type === "context") {
      result.push({ left: line, right: line });
      i++;
    } else if (line.type === "remove") {
      // Collect consecutive removals
      const removals: DiffLine[] = [];
      while (i < lines.length && lines[i]!.type === "remove") {
        removals.push(lines[i]!);
        i++;
      }

      // Collect consecutive additions
      const additions: DiffLine[] = [];
      while (i < lines.length && lines[i]!.type === "add") {
        additions.push(lines[i]!);
        i++;
      }

      // Pair them up
      const maxLen = Math.max(removals.length, additions.length);
      for (let j = 0; j < maxLen; j++) {
        result.push({
          left: j < removals.length ? removals[j]! : null,
          right: j < additions.length ? additions[j]! : null,
        });
      }
    } else if (line.type === "add") {
      result.push({ left: null, right: line });
      i++;
    } else {
      i++;
    }
  }

  return result;
}

// ---------------------------------------------------------------------------
// File path helpers
// ---------------------------------------------------------------------------

/**
 * Extract the file name from a full path.
 */
export function getFileName(filePath: string): string {
  const parts = filePath.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || filePath;
}

/**
 * Extract the directory from a full path.
 */
export function getDirectory(filePath: string): string {
  const normalized = filePath.replace(/\\/g, "/");
  const lastSlash = normalized.lastIndexOf("/");
  return lastSlash >= 0 ? normalized.slice(0, lastSlash) : "";
}

/**
 * Get the file extension.
 */
export function getFileExtension(filePath: string): string {
  const name = getFileName(filePath);
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
}

/**
 * Get a language identifier from file extension for syntax highlighting.
 */
export function getLanguageFromPath(filePath: string): string {
  const ext = getFileExtension(filePath);
  const map: Record<string, string> = {
    ts: "typescript",
    tsx: "typescriptreact",
    js: "javascript",
    jsx: "javascriptreact",
    py: "python",
    rs: "rust",
    go: "go",
    java: "java",
    rb: "ruby",
    c: "c",
    cpp: "cpp",
    h: "c",
    hpp: "cpp",
    cs: "csharp",
    css: "css",
    scss: "scss",
    html: "html",
    json: "json",
    yaml: "yaml",
    yml: "yaml",
    toml: "toml",
    md: "markdown",
    sql: "sql",
    sh: "shell",
    bash: "shell",
    zsh: "shell",
    xml: "xml",
    vue: "vue",
    svelte: "svelte",
  };
  return map[ext] ?? "plaintext";
}

// ---------------------------------------------------------------------------
// Grouping files by directory
// ---------------------------------------------------------------------------

export interface DirectoryGroup {
  directory: string;
  files: FileDiff[];
}

export function groupFilesByDirectory(files: FileDiff[]): DirectoryGroup[] {
  const groups = new Map<string, FileDiff[]>();

  for (const file of files) {
    const dir = getDirectory(file.filePath) || "(root)";
    const existing = groups.get(dir);
    if (existing) {
      existing.push(file);
    } else {
      groups.set(dir, [file]);
    }
  }

  return Array.from(groups.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([directory, dirFiles]) => ({ directory, files: dirFiles }));
}

// ---------------------------------------------------------------------------
// Sorting
// ---------------------------------------------------------------------------

const STATUS_ORDER: Record<string, number> = {
  added: 0,
  modified: 1,
  deleted: 2,
};

export function sortFiles(files: FileDiff[], mode: SortMode): FileDiff[] {
  const sorted = [...files];
  switch (mode) {
    case "name":
      return sorted.sort((a, b) =>
        getFileName(a.filePath).localeCompare(getFileName(b.filePath)),
      );
    case "status":
      return sorted.sort(
        (a, b) =>
          (STATUS_ORDER[a.status] ?? 99) - (STATUS_ORDER[b.status] ?? 99),
      );
    case "changes":
      return sorted.sort(
        (a, b) => b.additions + b.deletions - (a.additions + a.deletions),
      );
    default:
      return sorted;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function hashHunkId(input: string): string {
  let hash = 0;
  for (let i = 0; i < input.length; i++) {
    const chr = input.charCodeAt(i);
    hash = ((hash << 5) - hash + chr) | 0;
  }
  return Math.abs(hash).toString(36);
}
