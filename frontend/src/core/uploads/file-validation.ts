const MACOS_APP_BUNDLE_CONTENT_TYPES = new Set([
  "",
  "application/octet-stream",
]);

export const MACOS_APP_BUNDLE_UPLOAD_MESSAGE =
  "macOS .app bundles can't be uploaded directly from the browser. Compress the app as a .zip or upload the .dmg instead.";

const MAX_UPLOAD_FILE_SIZE = 100 * 1024 * 1024;

const BLOCKED_EXTENSIONS = new Set([
  ".exe",
  ".bat",
  ".cmd",
  ".com",
  ".scr",
  ".pif",
  ".sh",
  ".bash",
  ".zsh",
  ".msi",
  ".msp",
  ".mst",
  ".cpl",
  ".gadget",
  ".ws",
  ".wsf",
  ".vbs",
  ".vbe",
  ".wsh",
  ".ps1",
  ".psm1",
  ".psd1",
  ".app",
]);

const BLOCKED_MIME_TYPES = new Set([
  "application/x-msdownload",
  "application/x-msdos-program",
  "application/x-executable",
  "application/x-sh",
  "application/x-bat",
]);

export function isLikelyMacOSAppBundle(file: Pick<File, "name" | "type">) {
  return (
    file.name.toLowerCase().endsWith(".app") &&
    MACOS_APP_BUNDLE_CONTENT_TYPES.has(file.type)
  );
}

function hasBlockedExtension(filename: string): boolean {
  const ext = filename.toLowerCase().replace(/^.*(\.[^.]+)$/, "$1");
  return BLOCKED_EXTENSIONS.has(ext);
}

function hasBlockedMimeType(mimeType: string): boolean {
  return BLOCKED_MIME_TYPES.has(mimeType);
}

function hasDangerousFilename(filename: string): boolean {
  return /[<>:"|?*\\\x00-\x1f]/.test(filename) || filename.includes("..");
}

export function splitUnsupportedUploadFiles(fileList: File[] | FileList) {
  const incoming = Array.from(fileList);
  const accepted: File[] = [];
  const rejected: File[] = [];
  const reasons: Map<File, string> = new Map();

  for (const file of incoming) {
    if (isLikelyMacOSAppBundle(file)) {
      rejected.push(file);
      reasons.set(file, MACOS_APP_BUNDLE_UPLOAD_MESSAGE);
      continue;
    }
    if (file.size > MAX_UPLOAD_FILE_SIZE) {
      rejected.push(file);
      reasons.set(
        file,
        `File too large (max ${MAX_UPLOAD_FILE_SIZE / 1024 / 1024}MB)`,
      );
      continue;
    }
    if (hasDangerousFilename(file.name)) {
      rejected.push(file);
      reasons.set(file, "Filename contains invalid characters");
      continue;
    }
    if (hasBlockedExtension(file.name)) {
      rejected.push(file);
      reasons.set(file, "Executable files are not allowed");
      continue;
    }
    if (hasBlockedMimeType(file.type)) {
      rejected.push(file);
      reasons.set(file, "This file type is not allowed");
      continue;
    }
    accepted.push(file);
  }

  const messages = rejected.map((f) => reasons.get(f)).filter(Boolean);
  return {
    accepted,
    rejected,
    message: rejected.length > 0 ? messages.join("; ") : undefined,
  };
}
