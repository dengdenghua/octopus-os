/**
 * Shared desktop wallpaper URLs.
 *
 * The upstream artwork is 6400x3552 / 16 MB. Renderers only ever need display
 * resolution, so they use the WebP variants (2560 wide, 3840 for high-DPI
 * panels) through a shared srcset. The resampled JPEG at the original filename
 * stays in place for the native liquid-glass addon, which loads the path
 * directly and decodes it outside the renderer.
 *
 * See scripts/optimize-desktop-assets.py for the regeneration step.
 */

const WALLPAPER_BASE = "/third-party/appletechie-macos/wallpaper-day2";

/** Primary source: 2560 wide, matches the common full-screen desktop case. */
export const DESKTOP_WALLPAPER_URL = `${WALLPAPER_BASE}-2560.webp`;

/** Candidate set for <img srcSet>; browsers pick by viewport width. */
export const DESKTOP_WALLPAPER_SRCSET = [
  `${WALLPAPER_BASE}-2560.webp 2560w`,
  `${WALLPAPER_BASE}-3840.webp 3840w`,
].join(", ");

/** The wallpaper always fills the window. */
export const DESKTOP_WALLPAPER_SIZES = "100vw";
