# Echo OS appliance operations bundle

This archive is the host-side companion to one immutable Echo OS container
release. It contains only reviewed Compose configuration, install/upgrade,
backup/recovery and audit scripts, TLS configuration, a transactional systemd installer and the
release's exact `echo-release.env` image reference.

## Verify and extract

Keep the downloaded archive, its `.sha256` and `.spdx.json` beside the published
`operations_bundle.py`, then run:

```bash
sha256sum -c echo-appliance-operations.tar.gz.sha256
python3 operations_bundle.py verify echo-appliance-operations.tar.gz
sudo python3 operations_bundle.py extract echo-appliance-operations.tar.gz \
  --destination /opt/echo-os --require-root-owner
cd /opt/echo-os/echo-appliance-operations-*
```

The verifier reads the archive without extracting it, rejects links, devices,
absolute/traversal paths, unexpected files, wrong modes and checksum/SBOM
mismatches. Extraction writes that verified fixed inventory plus its verified
`bundle-manifest.json` and `SHA256SUMS`, so candidate-bound lab executors remain
usable after safe extraction, and refuses to replace an existing release
directory.
The production directory is extracted as root because the systemd installer
requires the bundle and managed scripts to be root-owned and not writable by
other users. Archive verification itself remains an unprivileged operation.

## First installation

Review the immutable image in `echo-release.env`. Optionally copy
`appliance.env.example` to `appliance.env` and set the NAS storage path, port,
UID/GID and explicit embedded-application origins. Do not put `ECHO_OS_IMAGE`
in `appliance.env`.

```bash
sudo install -m 0600 appliance.env.example appliance.env
sudoedit appliance.env
sudo docker login ghcr.io
sudo ./install-appliance.sh
```

If no administrator password is supplied, the first-start password is generated
and printed once in `docker compose logs echo-os`. Plain HTTP is appropriate
only on a trusted LAN.

## Production TLS

Place an unencrypted PEM certificate chain at `tls/echo.crt` and its matching
private key at `tls/echo.key`, make the key mode `0600`, then run:

```bash
export ECHO_TLS_HOST=echo.home.example
./start-tls.sh
```

The launcher validates the certificate, key, SAN, expiry and exact trust
boundary before it changes the deployment. TLS mode binds the compatibility
port to host loopback and exposes only the pinned, zero-capability gateway.

## Lifecycle

- `backup-state.sh` creates and re-verifies an encrypted, offline device-state
  backup. It requires both `ECHO_BACKUP_DIR` and `ECHO_BACKUP_MOUNTPOINT`.
  Store it off-device; it intentionally excludes NAS user files.
- `nas_data_backup.py` is the separate NAS user-data disaster-recovery path.
  It accepts only a read-only mounted filesystem snapshot, stores it in a
  private off-device restic repository, transports the password through an
  anonymous memory file and runs `restic check --read-data` before accepting a
  generation. Restore performs another full read, requires the configured NAS
  root to be empty, binds an exact 64-character snapshot ID into the operator
  confirmation and promotes the complete staged tree with one Linux atomic
  directory exchange. It never overlays or merges live user files.
- `bare_metal_recovery_lab.py` is the destructive G6 end-to-end recovery
  harness. Run `plan` on the still-working candidate only after the encrypted
  appliance-state backup, native Agent/user snapshot and NAS restic snapshot
  have completed. The private plan binds those three backups, the candidate,
  this bundle, the target disk and 1 MiB state/Agent plus 1 GiB NAS recovery
  canaries. Seven confirmed `run` phases then cross candidate Recovery and
  normal Echo OS boots: authenticated whole-disk install, first cold boot,
  three-path restore, transactional Agent promotion, trial verification,
  Recovery commit and final cold-boot verification. `verify` accepts only the
  complete eight-log sequence (including the source-backup record). Convert it
  to `bare-metal-recovery-lifecycle.json` with
  `physical_acceptance_capture.py bare-metal-result`; handwritten check booleans
  cannot satisfy G6. Use only a sacrificial target with another verified copy
  of every backup. The candidate index, extracted operations bundle, installer
  bundle, recovery key, backup receipts, private plan directory and separate
  public evidence directory must all be strict descendants of the verified
  off-device repository mount, so the whole-disk install cannot erase the
  lifecycle or any later-phase input.
  Appliance authentication, session-revocation and audit-signing state are
  restored and compared; host `/etc/shadow`, machine identity and disk identity
  are deliberately not cloned, and the replacement local administrator is
  provisioned by the installer.
- `export-audit-evidence.sh` creates encrypted, independently verifiable audit
  evidence on a mount named by both `ECHO_AUDIT_EXPORT_DIR` and
  `ECHO_AUDIT_EXPORT_MOUNTPOINT`.
- `upgrade-appliance.sh <registry@sha256:...>` backs up first, rejects schema
  migrations and accepts only an immutable current and target image. Before it
  changes `echo-release.env`, it fsyncs a mode-`0600` transaction journal; the
  target selection, healthy-container check and commit are separate durable
  phases. A handled failure restores and verifies both previous containers
  before deleting the journal.
- `recover-appliance-upgrade.sh` is the idempotent recovery entrypoint after an
  abrupt host reset or power loss. It restores the journaled previous immutable
  image, runs the real Compose health wait, verifies both container image
  identities and only then removes the transaction. If recovery fails, the
  journal remains for another boot or operator attempt; do not delete or edit it.
- `restore-state.sh <external-verified.echo-backup>` first prints an exact
  digest-bound confirmation. A confirmed run restores into staging, validates
  it, atomically promotes it and rolls back the directory if health fails.
- `operations_systemd.py plan` validates both active external mounts, encrypted
  credentials and the extracted bundle, then writes a mode-`0400`, digest-bound
  installation plan. `apply` requires its exact printed confirmation, verifies
  all five generated units with systemd, replaces each unit atomically, enables
  the boot-time upgrade-recovery service and enables both timers. A handled
  failure restores all previous unit files and enable/active states.
- `systemd/*.example` are review references and must remain byte-identical to
  the installer's default rendering. Production installation uses the planner;
  do not hand-edit and copy these templates.
- `operations_systemd.py remove-plan` snapshots the exact managed unit hashes,
  modes, recovery-service state and timer states. A confirmed `remove` disables
  the recovery service and timers and removes only those five unit files.
  Encrypted credentials, device/NAS data, backups
  and audit evidence are explicitly preserved; a handled failure restores the
  unit files and their previous timer states.
- `operations_systemd_lab.py` is the destructive physical-acceptance harness
  for a dedicated Debian 13 + OMV 8 lab appliance. Its private `plan` requires
  the exact `echo-delivery-release-evidence-index.json` for the candidate and
  verifies that this extracted bundle's ID, immutable image and lab-tool bytes
  match that candidate before binding them into the plan. It also requires
  a clean managed-unit baseline, two independent external mounts, four explicit
  preservation files and a root-owned evidence directory. Eight separately
  confirmed `run` phases exercise installation rollback, real scheduled backup
  and audit timer triggers, both mount-loss paths, removal rollback and final
  removal. It writes only fixed mode-`0444` structured evidence logs. Do not run
  it on a production NAS or mounts containing the only copy of user data.
- `hub_lifecycle_lab.py` is the destructive real-Docker Hub acceptance harness
  for a fresh appliance whose nine fixed Hub application endpoints and auxiliary
  TCP/UDP ports are free and whose nine target applications are not installed.
  `plan` verifies the release-candidate
  index, this extracted operations bundle and its own executable bytes, logs in
  only to read the live catalog, then writes a mode-`0400` plan bound to those
  identities, its digest, architecture, immutable package contracts and an
  exact confirmation. A confirmed `run`
  installs Jellyfin, Navidrome, Syncthing, Nextcloud, Immich, Open WebUI,
  qBittorrent, Paperless-ngx and Home Assistant, verifies every container
  image, health state, port, mount, private network, capability/resource bound
  and retained data/secret volume, uninstalls them, reinstalls them against the
  same volume identities without re-revealing credentials, and finally removes
  the containers while retaining data. Its mode-`0444` result contains only
  secret names and hashed host paths, never administrator or generated secret
  values. `verify` independently rechecks the plan/result digests, semantic
  service evidence and the still-present candidate/bundle/tool bytes. Both
  device gates must place the pair under the fixed names
  `hub-lifecycle-plan.json` and `hub-lifecycle-result.json`; the capture and
  final acceptance tools reject a missing half, semantic tampering or evidence
  from another candidate. Use only a dedicated acceptance appliance; retained application
  volumes and the Immich NAS directory deliberately remain after the run.
  Pass `--private-paperless-secret-output` to `run` with a path under a separate
  root-owned mode-0700 directory. The harness writes the reveal-once Paperless
  administrator password there as a candidate- and plan-bound mode-0400 JSON,
  never in the public result or stdout. After the final retained-data uninstall,
  reinstall Paperless and use that file for the functional lab. Never add it to
  a gate manifest or signed evidence directory.
- `paperless_functional_lab.py` is the candidate-bound Paperless OCR and Office
  acceptance harness. Run it separately in both device gates while the catalog
  Paperless bundle is installed. Its root-owned private fixture directory holds
  two real scanned PDFs, DOCX, XLSX, PPTX and their private search terms; none of
  those bytes or terms enter public evidence. `plan` binds the candidate,
  operations bundle, five-service installation and fixture hashes. A confirmed
  `run --password-file <private-handoff.json>` authenticates through the official API,
  rejects a handoff from another candidate or one stored under public evidence,
  uploads every fixture, waits for
  consumption, searches the expected extracted text, downloads and hashes the
  original, then deletes the synthetic document. `verify` rechecks the read-only
  plan/result pair. Both device manifests require the fixed names
  `paperless-functional-plan.json` and `paperless-functional-result.json` and
  reject forged checks, cross-candidate evidence or a missing half.
- `lan_discovery_functional_lab.py` is the candidate-bound two-device LAN
  acceptance harness. It uses only the public Syncthing REST and Home Assistant
  WebSocket/REST APIs. The NAS and companion Syncthing probes each require the
  peer address configuration to remain exactly `dynamic`, a healthy local
  discovery method, a discovery-cache match, one private-address direct
  TCP/QUIC connection with `isLocal:true`, and observed traffic. Their salted
  device hashes must cross-match while their machine hashes differ. The Home
  Assistant probe requires loaded `zeroconf` and `ssdp` config entries, binds a
  real `switch` or `light` entity to one discovered entry, changes it once and
  restores the initial state. The `credentials` command reads secrets only from
  named environment variables and creates fixed-name mode-0400 files under a
  separate owner-only directory; it refuses overwrite, public-plan directories
  and role confusion, and never prints secret values. Every plan, credential,
  probe and verification command first proves that its running mode-0755 tool bytes
  match the candidate-bound SHA-256 and size. Schema 2 probes must be no more
  than one hour old at verification, no more than ten minutes apart, and no
  more than five minutes ahead of the verifier clock. This rejects damaged or
  edited companion copies, stale probes and evidence spliced across lab runs.
  Both device gates require the
  fixed plan/result plus `lan-syncthing-nas.json`,
  `lan-syncthing-companion.json` and `lan-home-assistant.json`; capture and final
  acceptance rehash all three probes and reject manual-address, same-machine,
  forged, cross-candidate or unbound evidence.
- `power_state_recovery_lab.py` is the destructive G5 power/update/state
  harness. On a freshly extracted candidate bundle, `seed` first verifies the
  candidate manifest and both currently running containers, prints an exact
  candidate/old-digest confirmation, and only after that confirmation fsyncs
  the mutable `echo-release.env` selection from the candidate seed to the
  actually running older digest. Its root-only mode-`0400` plan then binds the release-candidate index,
  this bundle and the systemd physical-lab plan to one older immutable running
  image, the candidate target digest, two running containers, the enabled
  boot-recovery service, one 1 MiB device-state canary and one 1 GiB NAS
  canary. Seven ordered phases establish the baseline, start the production
  upgrade through a controlled Docker proxy, durably stop it immediately after
  target selection, require a real physical power removal and a different
  kernel Boot ID, prove automatic rollback from the persistent unclean-shutdown
  journal, commit a normal digest upgrade, inject and recover a later Compose
  failure, uninstall/reinstall without removing volumes, and restore an
  externally verified encrypted backup after a read-only preflight. `verify`
  accepts only the seven fixed mode-`0444` semantic logs. Never use it on a
  production NAS; the arm phase deliberately waits for the operator to remove
  power.
- `device_endurance_lab.py` is the G1/G6 physical device harness shared by the
  x86_64 and ARM64 cold-boot gates. Its private mode-`0400` plan binds one
  authenticated installer transcript, the release candidate, this bundle and
  the running immutable container to one hashed device identity. Four ordered
  phases run the full 1 GiB appliance probe during the first cold boot, require
  24 hours on the same kernel Boot ID, durably arm a physical power cut, then
  reject a normal shutdown and re-run the appliance probe after power returns.
  The installer transcript stays outside signed evidence because it may contain
  disk identity details; fixed mode-`0444` phase logs contain only digests and
  redacted identities. The harness never removes power itself. Use a dedicated
  acceptance device with persistent journald and physically disconnect power
  only after the arm phase succeeds.
- `protocol_interoperability_lab.py` is the G3 physical client harness. Its
  mode-`0400` plan binds the candidate, this bundle's manifest and both lab
  executor digests, one reviewed server name and one dedicated-share UUID.
  Five client roles perform real 8 MiB write/read/rename/delete probes on
  Windows SMB, macOS SMB/NFS and Linux SMB/NFS while hashing native mount
  evidence. Three separately confirmed Linux policy phases prove allowed and
  denied ACL identities on both protocols, exhaust one dedicated quota through
  SMB and observe the same quota through NFS, and stream a full non-sparse
  1 GiB file from SMB to NFS with one digest. `verify` binds all eight fixed
  mode-`0444` JSON logs into `protocol-interoperability-lifecycle.json` only
  after revalidating the current candidate index, bundle manifest and executor
  bytes. Every mounted root must contain only its exact authorization marker
  before a probe starts. Never use production shares, user identities or the
  only copy of any data.
- `storage_recovery_lab.py` is the destructive G2 SMART/RAID1 harness for a
  dedicated Debian 13 + OMV 8 appliance with exactly two sacrificial member
  devices. Its mode-`0400` private plan binds the release candidate, this
  bundle's manifest and both executor byte digests, the exact block-device
  identities and a root-owned mode-`0444` disposable-volume authorization
  marker. Eight separately confirmed phases prove healthy SMART, a physical
  member disconnect, degraded reads, read-only handling, real ENOSPC handling,
  exact-member reconnect, completed rebuild, reboot persistence and an Echo API
  1 GiB recycle-bin restore. Every phase writes a fixed mode-`0444` JSON log.
  Never run it on a production array or on a volume containing user data.

The A/B source contract additionally renders these same units inside a Debian
13 container and passes them to that release's native `systemd-analyze verify`.
The signed A/B job repeats the gate, binds its exact OS source revision, and
embeds the strict report fields plus original report SHA-256 in the GPG-signed
A/B evidence. Candidate and offline replay validation reject a missing, false,
cross-source or non-Debian-13 claim. This is a parser-compatibility gate. The
bundled physical lab harness covers real timer execution, mount loss and
transactional systemd failure paths, but its outputs still need the
candidate-bound physical manifest and signer. The G5 power interruption is
performed by `power_state_recovery_lab.py` and must be captured separately as
`power-state-lifecycle.json`; the parser/systemd result cannot replace it.

The backup and evidence directories must already exist on the exact active
mountpoints supplied to the scripts. `external_storage.py` fails before Docker
is touched if a mount disappeared, a path contains a symlink, the filesystem is
volatile/system-owned, or the destination shares a filesystem with the release,
device state, or NAS data. A distinct mounted partition is not necessarily
off-device: use removable or remote storage for disaster recovery.

The `data/` directory is Echo device state. The NAS files mounted through
`NAS_STORAGE` use the separate `nas_data_backup.py` path and still require the
operator to create a read-only filesystem snapshot first; a live writable tree
is rejected. Application-container data outside both roots remains subject to
its own OMV snapshot/backup policy.
