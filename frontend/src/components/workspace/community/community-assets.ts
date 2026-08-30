import { getPublicAssetURL } from "@/core/config";

/** Public community media honoring the configured Vite/desktop asset base. */
export function communityAssetURL(filename: string): string {
  return getPublicAssetURL(`community/${filename.replace(/^\/+/, "")}`);
}
