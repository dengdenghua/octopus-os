# Android device-sync v1 reference

`DeviceSyncClient.kt` is the protocol adapter. `EchoDeviceSyncWorker.kt` is the
bounded WorkManager integration for MediaStore photos and user-selected SAF
files. `EchoPairingBootstrap.kt` is the strict, all-or-nothing parser for the
pairing deep link. All three compile against the current `echo-mobile`
Android project and use only dependencies already present there.

Integration boundary:

- reuse the paired `tentacle_id` as `deviceId`;
- read `baseUrl` from the pairing deep link's `sync` query parameter;
- reuse the per-device pairing credential from `KVUtils` encrypted storage;
- never use an account token, browser Cookie, or Agent shared token;
- replace the permissive parsing in `RuntimeConfigActivity.maybeApplyConnectString`
  with `EchoPairingBootstrap.apply(connectString)`; it accepts only the exact
  `echo://join` shape, validates Runtime and sync transports, rejects
  duplicates/unknown fields, and persists nothing until the whole invitation is
  valid;
- older pairing links without `sync` remain valid; applying one clears any stale
  sync base rather than silently backing up to the previous NAS;
- add `READ_MEDIA_IMAGES` for Android 13+ and request it only when the user turns
  on photo backup; keep the existing pre-13 `READ_EXTERNAL_STORAGE` path;
- use `ACTION_OPEN_DOCUMENT` with persistable read permission, then pass the
  selected URIs to `EchoDeviceSyncWorker.setSelectedFiles`;
- set `KEY_PHOTOS_ENABLED` / `KEY_FILES_ENABLED` after the corresponding Echo
  administrator grant succeeds, then call `schedule`;
- schedule with the existing WorkManager dependency; Wi-Fi-only and
  charging-only constraints are explicit inputs;
- enumerate photos through MediaStore in bounded batches and user-selected
  files through SAF without requesting a second broad storage database; use
  structured `QUERY_ARG_LIMIT`/sort arguments because Android 36 rejects a
  SQL `LIMIT` suffix in MediaStore sort order;
- derive stable MediaStore/SAF `assetId` values and persist only the last
  successfully uploaded MediaStore row; the server remains the upload-offset
  and idempotency authority;
- retry `IOException` and transient HTTP failures; stop and notify on 401, 403, or 426.

The worker stores only its scan cursor, selected SAF URI list and optional SHA
cache in the existing local KV layer. The per-device credential remains in the
already encrypted Echo auth-token key. `KEY_CERT_PIN` is intentionally
separate from the Runtime WebSocket pin because a Tailnet HTTPS sync endpoint
can use a different hostname and certificate.

The canonical machine-readable contract is
[`../device-sync-contract.json`](../device-sync-contract.json). These files are
kept as the OS-side protocol reference; the same three implementations are now
integrated under `echo-mobile/app/src/main/java/com/apk/claw/android/sync/`.
The Mobile Runtime page handles the browsable `echo://join` link with an
explicit confirmation, photo permission, SAF picker, Wi-Fi/charging controls,
manual run and WorkManager result feedback. Parser unit tests live in
`../android-reference-test/EchoPairingBootstrapTest.kt`; client behavior tests
live beside them. The eleven LAN, Tailnet, backward-compatibility,
credential-sink, request-header, device-binding, origin, skip, resumed-offset
and version cases are also part of the real Mobile test source set.
