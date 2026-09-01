import {
  chmodSync,
  constants,
  copyFileSync,
  existsSync,
  mkdirSync,
} from "node:fs";
import { dirname } from "node:path";

/**
 * Seed the isolated appliance development data directory with the Agent model
 * configuration once. Existing appliance configuration always wins so models
 * edited through the UI are never reset by a later development restart.
 */
export function seedDevCustomModels({ sourcePath, targetPath }) {
  if (!existsSync(sourcePath) || existsSync(targetPath)) return false;

  mkdirSync(dirname(targetPath), { recursive: true, mode: 0o700 });
  try {
    copyFileSync(sourcePath, targetPath, constants.COPYFILE_EXCL);
  } catch (error) {
    // A concurrent launcher may have completed the same one-time seed first.
    if (error?.code === "EEXIST") return false;
    throw error;
  }
  chmodSync(targetPath, 0o600);
  return true;
}
