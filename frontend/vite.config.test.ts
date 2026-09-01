import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { WEB_BUILD_DEDUPLICATED_PET_ASSETS } from "./config/public-asset-dedup";

const frontendRoot = path.dirname(fileURLToPath(import.meta.url));

function sha256(filename: string): string {
  return createHash("sha256").update(fs.readFileSync(filename)).digest("hex");
}

describe("web build pet-asset deduplication", () => {
  it("does not retain legacy mascot authoring binaries", () => {
    expect(WEB_BUILD_DEDUPLICATED_PET_ASSETS).toEqual([]);
  });

  for (const asset of WEB_BUILD_DEDUPLICATED_PET_ASSETS) {
    it(`keeps a byte-identical canonical source for ${asset.publicPath}`, () => {
      const publicSource = path.resolve(
        frontendRoot,
        "public",
        asset.publicPath,
      );
      const canonicalSource = path.resolve(frontendRoot, asset.canonicalPath);

      expect(fs.existsSync(publicSource)).toBe(true);
      expect(fs.existsSync(canonicalSource)).toBe(true);
      expect(fs.statSync(publicSource).size).toBe(
        fs.statSync(canonicalSource).size,
      );
      expect(sha256(publicSource)).toBe(sha256(canonicalSource));
    });
  }
});
