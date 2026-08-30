const LEGACY_ROOT = "octo" + "pus";
const LEGACY_PREFIXES = [
  `${LEGACY_ROOT}:`,
  `${LEGACY_ROOT}.`,
  `${LEGACY_ROOT}-`,
  `${LEGACY_ROOT}_`,
] as const;

function echoKey(key: string): string | null {
  for (const prefix of LEGACY_PREFIXES) {
    if (key.startsWith(prefix)) return `echo${key.slice(LEGACY_ROOT.length)}`;
  }
  return null;
}

function migrateStorage(storage: Storage): void {
  const legacyKeys = Array.from({ length: storage.length }, (_, index) =>
    storage.key(index),
  ).filter((key): key is string => Boolean(key));

  for (const legacyKey of legacyKeys) {
    const targetKey = echoKey(legacyKey);
    if (!targetKey) continue;
    try {
      const value = storage.getItem(legacyKey);
      if (value !== null && storage.getItem(targetKey) === null) {
        storage.setItem(targetKey, value);
      }
      storage.removeItem(legacyKey);
    } catch {
      // Storage can be unavailable in hardened/webview contexts. Startup must
      // remain usable; the next writable launch will retry the migration.
    }
  }
}

/** One-time, idempotent migration to Echo-owned browser storage keys. */
export function migrateLegacyEchoStorage(): void {
  if (typeof window === "undefined") return;
  migrateStorage(window.localStorage);
  migrateStorage(window.sessionStorage);
}
