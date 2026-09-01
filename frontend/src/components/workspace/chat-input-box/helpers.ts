import type { ResearchMaterial, ResearchSourceKind } from "@/core/research/api";

export interface ComposerResearchMaterial {
  id: string;
  enabled: boolean;
  material: Partial<ResearchMaterial>;
}

export interface ComposerImageInjectionDetail {
  threadId?: string | null;
  images?: File[] | null;
  sourceLabel?: string | null;
  text?: string | null;
}

export interface WorkspaceFileInjectionDetail {
  threadId?: string | null;
  path?: string | null;
  workDir?: string | null;
  sourceLabel?: string | null;
}

export interface PendingContextFile {
  id: string;
  name: string;
  path: string;
  workDir?: string | null;
  sourceLabel?: string | null;
  file?: File;
}

export function imageFileKey(file: File): string {
  return `${file.name}|${file.size}`;
}

export function fileBasename(path: string): string {
  const parts = path.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1]?.trim() || path;
}

export function pendingFileKey(path: string, workDir?: string | null): string {
  return `${workDir ?? ""}|${path}`.replace(/\\/g, "/").toLowerCase();
}

export function uploadFileKey(file: File): string {
  return `upload|${file.name}|${file.size}|${file.lastModified}`;
}

export function referencedFilesBlock(files: PendingContextFile[]): string {
  if (files.length === 0) return "";
  const lines = files.map((file) => {
    const prefix = file.file ? "upload" : "path";
    const location = file.file ? file.name : file.path;
    const workspace = file.workDir ? ` workspace=${file.workDir}` : "";
    return `- ${prefix}=${location}${workspace}`;
  });
  return `<referenced_files>\n${lines.join("\n")}\n</referenced_files>`;
}

export function appendReferencedFiles(
  text: string,
  files: PendingContextFile[],
): string {
  const block = referencedFilesBlock(files);
  if (!block) return text;
  const body = text.trim();
  return body ? `${body}\n\n${block}` : block;
}

export async function dataUrlToFile(
  dataUrl: string,
  filename: string,
): Promise<File | null> {
  try {
    const response = await fetch(dataUrl);
    const blob = await response.blob();
    return new File([blob], filename, {
      type: blob.type || "image/png",
    });
  } catch {
    return null;
  }
}

export const RESEARCH_SOURCE_OPTIONS: Array<{
  kind: ResearchSourceKind;
  label: string;
}> = [
  { kind: "web", label: "Web" },
  { kind: "news", label: "News" },
  { kind: "academic", label: "Academic" },
  { kind: "company_site", label: "Official" },
  { kind: "ecommerce", label: "Shop" },
  { kind: "social", label: "Social" },
  { kind: "forum", label: "Forum" },
  { kind: "provided_url", label: "URLs" },
  { kind: "uploaded_file", label: "Files" },
];

export const DEFAULT_RESEARCH_SOURCES: ResearchSourceKind[] = [
  "web",
  "news",
  "academic",
  "company_site",
  "ecommerce",
  "social",
  "forum",
  "provided_url",
  "uploaded_file",
];

export function parseComposerUrls(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(/[\s,，]+/)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  );
}
