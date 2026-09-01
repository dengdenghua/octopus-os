# Echo OS A/B update contract

`echo-os-update` accepts only a directory containing one version-matched
dm-verity root/hash/signature triplet, its UKI, and a signed manifest. The UUIDs
are not cosmetic: the root and hash UUIDs are derived from the signed root hash
and are propagated into the destination GPT by `systemd-sysupdate`'s `@u`:

```text
echo-os_0.3.0.root.ROOT_UUID.raw.zst
echo-os_0.3.0.root-verity.HASH_UUID.raw.zst
echo-os_0.3.0.root-verity-sig.SIGNATURE_UUID.raw.zst
echo-os_0.3.0.efi
OS-SOURCE-IDENTITY.json
SHA256SUMS
SHA256SUMS.gpg
```

The command first runs a bounded structural preflight, verifies
`SHA256SUMS.gpg` with a root-owned public-only keyring, validates the exact file
set and every payload hash, and tests all three authenticated zstd streams. It
then validates the signature-partition JSON, release-certificate fingerprint,
PKCS#7 signer, root/hash UUID derivation and the UKI's single matching
`roothash=`; any mutable `root=` selection is forbidden. Only then does it call
`systemd-sysupdate`. `OS-SOURCE-IDENTITY.json` is part of the signed hash set and
must contain one clean full OS commit/tree, credential-free origin and consistent
commit timestamp; the preflight and post-signature passes must return the same
source identity. The manifest is capped at 64 KiB, its signature at 1 MiB,
the compressed root at 16 GiB, the hash tree at 1 GiB, the compressed signature
partition at 8 MiB and the UKI at 256 MiB. Its decompressed signature partition
must fit systemd's 4 MiB embedded-signature limit. Symlinked bundles,
unsigned extra files, non-root-owned state and files writable by group/other
are rejected before update data reaches sysupdate.
Both `verify` and `apply` take an exclusive root-owned lock below
`/run/echo-os-update`; a second process fails before authentication or writes can
overlap. A successful apply emits one `ECHO_UPDATE_APPLIED` record binding the
installed version, OS commit/tree and source-manifest digest. This marker is
printed only after `systemd-sysupdate` returns success. Because sysupdate also
returns success when asked to install an already-installed version, apply now
first runs `check-new`, requires its single candidate to equal the authenticated
bundle version, emits `ECHO_UPDATE_CANDIDATE_READY`, and then invokes
`update VERSION` explicitly. A replayed/current bundle, an older bundle, a
candidate mismatch, or a failed check cannot reach the update command or
produce an applied marker.
Immediately before an `apply`, a provisioned device also captures its current
local-account hash, display name and hostname into private persistent `/var`
state. Locale, console/X11 keymap and timezone are independently validated and
captured into their own persistent `/var` state at the same boundary. The
update aborts if either capture fails; a replacement root must never silently
relock the owner's account or revert the device's regional settings.
`10-root.transfer`, `20-root-verity.transfer` and
`30-root-verity-sig.transfer` write the inactive backing partitions in order.
`90-uki.transfer` installs the UKI last with three boot attempts, so an
interrupted update never publishes a boot entry before its complete verified
root set is present.

No development signing key is included. A production image build requires
`ECHO_UPDATE_KEYRING` and injects that bounded binary OpenPGP public export at
`/usr/lib/echo-os/update-keyring.gpg`; the finished root parses it with the same
strict public-packet verifier used by Recovery, and the build reads it back from
the raw byte-for-byte. An administrator/OEM can install a root-owned override
at `/etc/echo-os/update-keyring.gpg`. A missing, symlinked, writable, oversized,
malformed or secret-bearing keyring fails closed.

```bash
sudo echo-os-update verify /path/to/bundle
sudo echo-os-update apply /path/to/bundle
```

## Signed HTTPS channel

Installed systems also ship `echo-os-update-channel`. Its immutable default
source is read from `/usr/lib/echo-os/update-channel`; an administrator may
replace it with a root-owned, non-symlink `/etc/echo-os/update-channel` and may
select a matching public-only keyring at
`/etc/echo-os/update-keyring.gpg`. A channel value must be one
credential-free HTTPS directory URL: HTTP, embedded credentials, query strings,
fragments, escapes, traversal and redirects are rejected. The repository
serves the same seven files shown above directly below that URL.

The client downloads only bounded `SHA256SUMS` and `SHA256SUMS.gpg` first,
requires `gpgv` to authenticate the manifest, and only then derives and streams
the five exact payload names. TLS uses the Debian CA store with TLS 1.2 or
newer; transformed responses, oversized/empty bodies, digest mismatches,
symlinks and same-version replacement are rejected. Files are written under a
root-only staging name in `/var/cache/echo-os/updates`, fsynced, made immutable
to ordinary users and atomically renamed to their authenticated version. The
cache retains the selected version and at most one previous version; explicit
channel apply keeps the fetch lock until the production updater exits so the
selected directory cannot be vacuumed during a write.

```bash
# Download and authenticate only. This never writes an A/B slot or reboots.
sudo echo-os-update-channel fetch

# Re-fetch/authenticate, then enter the production check-new + apply path.
sudo echo-os-update-channel apply
```

`echo-os-update-fetch.timer` runs the fetch-only command after boot and roughly
every six hours, with randomized delay, only on AC power. It never invokes
`apply`, `systemd-sysupdate` or reboot. A successful poll emits
`ECHO_UPDATE_CHANNEL_FETCHED`; installation still requires the explicit apply
command above, which reuses all bundle, dm-verity, UKI, state-capture and replay
checks described in this document.

The coordinator also publishes a deliberately small, root-owned
`/var/lib/echo-os-update/status.json` for the desktop. It contains only a schema
number, coarse phase/state, authenticated version and manifest digest, timestamp
and (on failure) a numeric exit code. It never exposes the private bundle path,
channel URL, stderr or key material. Writes use a same-directory fsync + atomic
replace; readers reject symlinks, unexpected fields, unsafe ownership/modes and
records over 4 KiB. A check that loses the fetch lock does not overwrite the
status of the operation holding it.

The About Echo OS panel reads that fixed file through the Electron main process.
Installing is a separate fixed action: Electron may invoke only
`pkexec --disable-internal-agent /bin/bash
/usr/lib/echo-os/echo-os-update-apply`, and the PolicyKit policy binds
authorization to that exact interpreter + first-argument pair with
`auth_admin_keep` for the active local session. The helper then calls the same
`echo-os-update-channel apply` entrypoint above. Renderer code cannot supply a
command, bundle path, version or additional argv. The UI reports checking,
authenticated, inactive-slot installation, reboot-required and failed states;
it does not reboot automatically.

The source tree currently names
`https://updates.echo-age.com/echo-os/stable/x86-64` as the production channel.
That is an image configuration contract, not evidence that the endpoint is
deployed or contains a signed release; availability and publication remain a
release-operations acceptance gate.

## Signing-key rotation and retirement

Every release root embeds a canonical `update-trust-policy.json` containing a
positive generation, the exact public-keyring SHA-256, the sorted primary
fingerprints currently trusted and the cumulative fingerprints retired. The
release build derives the trusted set from the actual binary keyring with GPG,
rejects revoked primary keys, requires an explicit
`ECHO_UPDATE_TRUST_GENERATION`, seals policy and keyring into dm-verity root and
reads both back from the finished artifact.

`echo-update-trust-promote.service` is required before boot blessing and after
restore, crash, Agent and the applicable desktop/login health gates. Only such
a healthy root may promote its system policy into the encrypted persistent
`/var/lib/echo-os/update-trust`. Promotion advances exactly one generation:
previously retired fingerprints remain retired, every removed trusted
fingerprint must move to the retired set, and an identity never previously
trusted cannot be invented as retired. Keyring and policy use a fsynced
pending-to-current transaction; the next healthy boot finishes either possible
interruption boundary. `echo-os-update` and the HTTPS channel prefer this
managed keyring over an older root copy, so A/B rollback cannot resurrect a
retired signing key. A root-owned `/etc/echo-os/update-keyring.gpg` remains an
intentional administrator override and has highest priority.

A normal no-key-change release keeps the same generation and exact policy. A
rotation is deliberately a two-release bridge:

1. Generation N+1 is signed by the old key and embeds a keyring containing old
   and new keys. After the healthy boot, both are persistent.
2. Generation N+2 is signed by the new key, embeds only the new key and lists
   the old full fingerprint as retired. After promotion, old-root rollback still
   selects the new-only managed keyring.

Skipping the bridge, changing a keyring inside one generation, silently
dropping a key, unretiring a key or jumping generations fails closed. The
portable transaction/rollback tests cover these rules and both power-loss
publication boundaries. A production rotation is not complete until the two
real signed releases are served in sequence and the Linux A/B lifecycle proves
bridge boot, final boot, old-signature rejection and rollback behavior.

The release side requires the full signing-key fingerprint, the exact public
keyring embedded in the target image, the dm-verity/Secure Boot release
certificate and the authorized PCR-policy public key. Before publication it
fully verifies the dm-verity tree, the UKI roothash, PE signer and signed-PCR11
policy. It then builds on the destination file system, verifies the detached
signature with the selected update keyring, checks the strict bundle and all
zstd payloads, and atomically renames the complete directory. It refuses
symlinks and non-empty destinations:

```bash
os_commit="$(git rev-parse HEAD)"
python3 packaging/image/os_source_identity.py capture \
  --repo "$PWD" \
  --expected-commit "$os_commit" \
  --output /run/release/echo-os-source-identity.json

ECHO_OS_SOURCE_MANIFEST=/run/release/echo-os-source-identity.json \
ECHO_UPDATE_KEYRING=/run/secrets/echo-os-update-release.gpg \
ECHO_UPDATE_SIGNING_KEY=FULL_FINGERPRINT \
ECHO_SECURE_BOOT_CERTIFICATE=/run/secrets/echo-os-db.crt \
ECHO_TPM2_PCR_PUBLIC_KEY=/run/secrets/echo-os-pcr-policy-public.pem \
  ./deploy/update/create-update-bundle.sh /secure/release/echo-os-0.3.0
```

The private key is intentionally not accepted as a repository file or image
input. Keep it in the protected release environment; distribute only its
public keyring with the OS.

## Atomic stable-repository publication

`create-update-bundle.sh` deliberately stops at one authenticated release
directory. `publish_update_repository.py` is the separate, public-key-only
promotion boundary that makes such a directory visible at the production
channel. The web document root has this layout:

```text
/srv/updates.echo-age.com/echo-os/
├── releases/x86-64/00000000000000000001-0.3.0/
│   ├── SHA256SUMS
│   ├── SHA256SUMS.gpg
│   └── five authenticated payload files
└── stable/x86-64 -> ../releases/x86-64/00000000000000000001-0.3.0
```

The publisher runs under the sole owner of a non-group/world-writable document
root. It copies only the strict signed file set into a private directory on the
same filesystem, snapshots and structurally validates the public-only keyring,
runs `gpgv` and the complete runtime bundle verifier against the copied bytes,
fsyncs every file and directory, and renames the immutable release into place.
Only then does it atomically replace the relative `stable/x86-64` symlink and
fsync that directory. The first publication has sequence 1 and every later
publication advances exactly one; a gap, rollback, sequence reuse with another
version, same-version replacement, unsafe symlink or concurrent publisher fails
closed. A retry can finish the precise interruption where the immutable release
was renamed but the stable pointer was not yet switched.

```bash
./deploy/update/publish_update_repository.py publish \
  --bundle /secure/release/echo-os-0.3.0 \
  --keyring /run/secrets/echo-os-update-release.gpg \
  --repository-root /srv/updates.echo-age.com/echo-os \
  --sequence 1

./deploy/update/publish_update_repository.py verify-current \
  --keyring /run/secrets/echo-os-update-release.gpg \
  --repository-root /srv/updates.echo-age.com/echo-os
```

The HTTPS server must map that document root directly to `/echo-os`, follow the
internal relative channel symlink without issuing an HTTP redirect, disable
directory listings and content transformation, serve exact `Content-Length`,
and deny dotfiles such as `.publish.lock`. `SHA256SUMS` and its signature should
use `Cache-Control: no-store`; version-named payloads may be cached. TLS
certificate provisioning, host deployment, external availability monitoring
and production key custody remain release-operations gates. This source tool
does not make the configured public URL live by itself. Portable unit tests
cover nine state, immutability and interruption cases;
`smoke-update-repository-publication.sh` additionally creates an ephemeral real
GPG identity on Linux, publishes two consecutive signed releases through the
CLI, verifies the served channel with `gpgv`, and requires rollback rejection.

`systemd-bless-boot.service` removes the attempt counter after a successful
Echo Desktop health gate. If a new entry exhausts its attempts, systemd-boot
stops selecting it and falls back to the previous UKI/root version.
`ab-update-smoke.yml` now starts from a base raw written by the production
whole-disk installer and completed by the production OEM service. The OEM VM
boot persists its machine ID, local-account marker/hash and regional state into
a lifecycle raw, removes the test-only SDDM autologin file, and only then hands
that device to `smoke-ab-update.sh`. The update gate first runs a complete
`veritysetup verify` against the new triplet and UKI, compares the original
values after root replacement and rollback, and boots the updated root through
the production local-account restoration path. It then corrupts the new root,
requires an explicit dm-verity rejection before boot, consumes three failed
boot attempts, and boots the rolled-back root through SDDM again.

Before the healthy update, the raw gate also performs one real interrupted
apply against the same lifecycle disk. A PATH shim does not fake sysupdate: it
starts the host's real `systemd-sysupdate` in its own process group, watches a
bounded sample at the inactive root's exact GPT offset, and sends `SIGKILL` as
soon as the first changed bytes are visible. The gate then requires a changed
inactive-root sample, uncommitted A/B labels, no new UKI, no false
`ECHO_UPDATE_APPLIED`, no leaked loop device, and a healthy cold boot through
the old UKI. A normal production-entrypoint apply is retried on that same
partially written disk and must flush/rewrite it successfully before the rest
of the update and rollback lifecycle proceeds. This follows systemd v257's
documented incomplete-transfer recovery and UKI-last ordering semantics; it is
still runtime evidence only after the privileged Linux workflow actually runs.

The same partially written disk then enters a target-capacity failure before
the successful retry. The harness fills the ESP with copies of the exact
authenticated update UKI until FAT refuses another equal-sized file, invokes
the production updater, and requires an `ENOSPC`/disk-full failure. The A/B
labels and new UKI must remain unpublished, the applied marker must remain
absent, the old entry must cold-boot, and no loop device may leak. Only after
the bounded filler namespace is removed may the normal update succeed on that
same disk. This covers an exhausted boot partition without mutating or
deleting unrelated ESP entries.

The destructive raw lifecycle no longer invokes `systemd-sysupdate` directly.
It calls the packaged `echo-os-update apply` entrypoint with an explicit
source-runtime sentinel and a disposable offline image target, so the real
bounded preflight, root-only keyring policy, detached-signature verification,
full payload hashes, verity/UKI binding, exclusive lock and UKI-last apply
marker are all exercised before the updated disk is booted. This test-only
offline mode does not synthesize device identity: the raw must already contain
the OEM, account, machine, network and regional state produced by earlier
production boots. Normal installed invocations receive no path arguments or
sentinel environment and always capture current live account/region state
immediately before apply; enabling the source harness already requires root,
which has equivalent direct disk authority.
The uploaded log directory retains the interrupted and successful production
apply records, the healthy old-root boot after interruption, the explicit
dm-verity corruption rejection, every healthy/failed/rolled-back boot serial
log, and a final `ECHO_AB_UPDATE_RAW_OK` record that binds the tested source
identity and persistent-state matrix. The final record is no longer available
only in ephemeral job-console output.
After the lifecycle closes, `verify-ab-update-evidence.py` requires fourteen
exact log roles: interrupted authenticated apply and healthy old-root boot,
ESP-full authenticated apply and healthy old-root boot, successful production
apply, final completion, corruption rejection, healthy updated boot/login,
three non-healthy failed attempts, and healthy rollback boot/login. The two
failed apply logs must contain their exact failure and unpublished-label/UKI
markers plus successful same-disk recovery, and neither may contain an applied
marker. The binder hashes those logs against the
clean OS and Agent sources, the
source-bound update manifest/signature, public update keyring and provisioned
base-disk hash without copying log text. CI then detached-signs the resulting
`echo-ab-update-evidence.json` with the same isolated update identity that
signed the tested bundle, immediately verifies it using the public-only update
keyring, and uploads the JSON, signature, public keyring and verification log.
This makes a completed A/B run reviewable as one authenticated evidence set;
it does not turn an unexecuted workflow into runtime proof.

This is the destructive VM contract for install → first use → healthy update →
failed update → rollback continuity. A production release still needs an
externally protected signing key, signed release pipeline, actual green CI
evidence and representative hardware execution before this contract can be
called shipped.
