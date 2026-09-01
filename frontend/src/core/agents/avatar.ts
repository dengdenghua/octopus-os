// Avatar style options:
// - "pixel-halfbody-v3": Pixel art style (retro 8-bit aesthetic)
// - "original": Original uploaded avatar (anime/realistic/custom art)
// - null/undefined: Use original avatar without transformation
const AGENT_AVATAR_ASSET_VERSION: string | null = null; // Changed from "pixel-halfbody-v3" to null

export function withAgentAvatarVersion(src: string): string {
  if (!src.includes("/api/agents/") || !src.includes("/avatar")) {
    return src;
  }
  if (src.includes("avatar_style=")) {
    return src;
  }
  // If no avatar style is configured, return the original URL unchanged
  if (!AGENT_AVATAR_ASSET_VERSION) {
    return src;
  }
  const separator = src.includes("?") ? "&" : "?";
  return `${src}${separator}avatar_style=${AGENT_AVATAR_ASSET_VERSION}`;
}
