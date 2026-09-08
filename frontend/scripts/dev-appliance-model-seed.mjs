import {
  closeSync,
  existsSync,
  fsyncSync,
  linkSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readFileSync,
  rmdirSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";

import { protectDevStagingDirectory } from "./dev-private-directory.mjs";

/**
 * Seed the isolated appliance development data directory with the Agent model
 * configuration once. Existing appliance configuration always wins so models
 * edited through the UI are never reset by a later development restart.
 */
export function seedDevCustomModels({ sourcePath, targetPath }) {
  if (!existsSync(sourcePath) || existsSync(targetPath)) return false;

  mkdirSync(dirname(targetPath), { recursive: true, mode: 0o700 });
  const staging = mkdtempSync(join(dirname(targetPath), ".echo-model-seed-"));
  const stagedPath = join(staging, "models.json");
  try {
    protectDevStagingDirectory(staging);
    // Create a new file so Windows inherits the private staging ACL rather
    // than importing potentially broader source permissions via CopyFile.
    const descriptor = openSync(stagedPath, "wx", 0o600);
    try {
      writeFileSync(descriptor, readFileSync(sourcePath));
      fsyncSync(descriptor);
    } finally {
      closeSync(descriptor);
    }
    try {
      // Publish the complete inode without replacing an existing destination.
      // Unsupported filesystems fail closed instead of falling back to a copy
      // that could expose partially written credentials.
      linkSync(stagedPath, targetPath);
    } catch (error) {
      if (error?.code === "EEXIST") return false;
      throw error;
    }
    return true;
  } finally {
    if (existsSync(stagedPath)) unlinkSync(stagedPath);
    rmdirSync(staging);
  }
}
