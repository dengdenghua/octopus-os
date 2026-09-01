/**
 * Pet authoring sources that historically lived in `public/` as well as in
 * the canonical Godot project. The web UI does not reference these files, so
 * Vite may omit only the generated public copies after proving byte identity.
 */
// Echo OS intentionally does not carry pre-Echo mascot authoring
// binaries. Add a record here only when an Echo-owned canonical pet asset is
// introduced and a generated public copy must be proven byte-identical.
export const WEB_BUILD_DEDUPLICATED_PET_ASSETS = [] as const;
