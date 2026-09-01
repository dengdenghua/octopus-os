# Echo Mobile device-sync emulator acceptance

Run date: 2026-08-28 (Asia/Shanghai)

This is a repeatable Android-system smoke test, not a substitute for the
physical Android + NAS + Tailnet gate in `P1_P2_REAL_DEVICE_CHECKLIST.md`.

Environment:

- AVD: `sdk_gphone64_arm64`, Android API 36, `arm64-v8a`;
- app: `com.echo.mobile`, versionName 1.0.0, versionCode 15;
- APK: real `assembleDebug` arm64 output installed with ADB;
- app data was cleared before the run; only synthetic credentials and a
  21-byte synthetic SAF file were used.
- success-path server: `scripts/android_device_sync_lab.py`, bound to host
  loopback and backed by a new mode-0700 temporary directory. It composes the
  production `DeviceLinkService`, `DeviceSyncService`, `FileManager`, audit and
  FastAPI router; the per-device credential was written only to a mode-0600
  temporary file and was not printed.

Observed results:

1. A valid LAN `echo://join` intent resolved to `RuntimeConfigActivity` and
   showed Runtime plus sync origin in a confirmation dialog. Cancel left the
   previous Runtime unchanged; confirm persisted the new Runtime and sync base.
2. A public cleartext Runtime plus unrelated HTTPS sync receiver produced no
   confirmation and left the previously paired Runtime unchanged.
3. The credential field rendered as a system password field (`password=true`)
   with bullets in the accessibility hierarchy. The synthetic credential was
   absent from MMKV and absent in plaintext from encrypted preferences.
4. Enabling photo backup opened the Android 36 media permission controller.
   “Allow all” granted `READ_MEDIA_IMAGES`, checked the photo switch and showed
   the pending Echo-admin-grant state.
5. `ACTION_OPEN_DOCUMENT` selected a synthetic Downloads file, returned to the
   Runtime page, checked file backup and retained its content URI in the local
   selected-URI list.
6. Wi-Fi-only scheduling appeared in JobScheduler with `NOT_METERED`,
   `INTERNET`, `TRUSTED` and `VALIDATED` requirements and without a charging
   requirement. The one-time run used ordinary connected-network constraints.
7. “立即同步” executed the real WorkManager worker against the reachable local
   development endpoint. Its 401 response was surfaced as “设备凭据已撤销，请重新配对”,
   proving the worker-to-UI failure mapping rather than only task enqueueing.
8. The same UI was then paired to the managed acceptance endpoint and exercised
   a real authenticated upload. This exposed an Android 36 MediaStore failure:
   the provider rejects `LIMIT` appended to SQL sort order. The worker was
   changed to `QUERY_ARG_SORT_COLUMNS`, `QUERY_ARG_SORT_DIRECTION` and
   `QUERY_ARG_LIMIT`; the rebuilt APK was installed over the same app data.
9. After the fix, WorkManager reported “本轮备份完成”. The selected 21-byte file
   appeared under `Mobile Uploads/<device>/Files/Selected/`; source and stored
   SHA-256 were both
   `17cfbc6106c1c91cbcb59ae2afbbc3bb8f18c75e5841d4537cf2de3ba44d5559`.
10. A second unchanged run also completed while file count, event count and
    target modification time all remained unchanged. This proves the real
    Android-to-Echo path uses the server-side `skip` decision rather than
    creating duplicates.
11. Changing the same SAF document to 24 bytes produced one keep-both conflict
    file with digest prefix `ad5e624e`; the original 21-byte file and digest
    remained unchanged. The ledger contained one `created` event, one
    `conflict` event and revision 2.
12. A separate 512 MiB SAF run was force-stopped immediately after the server
    durably recorded exactly 8 MiB. Reopening the app left that authoritative
    offset unchanged; the next manual run resumed and atomically committed all
    536,870,912 bytes. Source and target SHA-256 were both
    `9acca8e8c22201155389f65abbf6bc9723edc7384ead80503839f49dcc56d767`,
    and no `.part` file remained.
13. That forced stop also exercised a real ASGI disconnect. The router now
    converts the expected `ClientDisconnect` into a bounded 499 response rather
    than emitting an exception traceback; a regression test invokes this path
    directly.

Automated guards now also cover the acceptance lab's real managed-device auth,
both scope grants, private credential-file mode, exclusive creation, empty
state-directory requirement and symlink rejection.

Not proven here:

- device reboot and Echo-service restart resume against authoritative
  `uploadedBytes` (app-process kill is proven above);
- lock-screen, Doze, OEM battery manager and large MediaStore behavior;
- Tailscale HTTPS/certificate behavior, low-capacity 507 and physical network
  transitions.

Those remain mandatory physical acceptance items.
