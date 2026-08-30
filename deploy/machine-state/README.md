# Echo OS persistent machine identity

Every distributable root image contains an empty `/etc/machine-id`, as required
for a generic image that is cloned to multiple devices. Keeping a generated ID
inside root A or root B would make one physical device change identity whenever
an update switches slots. Baking an ID into the image would be worse: every
device cloned from that artifact would share it.

The custom mkosi systemd initrd runs `echo-machine-state-initrd.service` after
the selected root has been opened through dm-verity and mounted read-only at
`/sysroot`, but before `initrd-root-fs.target` and switch-root. It unlocks the
LUKS2 `echo-var` partition through the signed-PCR11 TPM2 token or the recovery
key and keeps it mounted at the selected root's `/var`. It then mounts the
selected root's vendor `/etc` read-only as an overlay lower layer and keeps the
writable upper/work layers under `/var/lib/echo-os/etc-overlay`. Finally it
creates or reuses the strict 32-hex ID in `/var/lib/echo-os/machine-id` and
bind-mounts that file read-only over the overlaid `/etc/machine-id`.

The var, swap and home GPT entries carry `NoAuto=yes`. They are mounted only by
the explicit encrypted `crypttab`/`fstab` policy, which prevents GPT automatic
mounting from trying to bind `/var` to the build-time partition UUID before the
per-device machine ID exists. The initrd deliberately keeps `echo-var` mounted
through switch-root because both the active overlay and machine-ID bind depend
on files stored there.

The overlay keeps password hashes, host configuration and other mutable `/etc`
state off both replaceable, dm-verity-protected vendor roots while still allowing a new
A/B root to provide new lower-layer defaults. Factory reset deletes the
encrypted upper layer together with the rest of device data.

The state file is created from the kernel random UUID source with an atomic
rename, is never regenerated when valid state exists, and fails closed on
corrupt state or a missing persistent partition. A valid legacy ID in the
active root can seed the persistent file during migration. Factory-resetting
`/var` deliberately creates a new device identity on the next normal boot.

`echo-machine-identity-health.service` compares the active and persistent IDs
before either SDDM or the credential-gated CI desktop can start. Logs contain
only an application-specific ID produced by `systemd-id128`; the raw machine ID
is treated as confidential and is never printed. The A/B smoke injects one
temporary identity, verifies it survives root replacement, and compares the
non-reversible derived identity before and after rollback.

The source contract now requires encrypted var/home/swap plus TPM2 and recovery
unlock paths. Confidentiality is considered proven only after the Linux QEMU
gate demonstrates signed-PCR unseal with no recovery key available to the VM.

## NetworkManager profiles

NetworkManager's supported `[keyfile] path=` setting redirects all mutable
system connection profiles to
`/var/lib/NetworkManager/system-connections`. That includes Wi-Fi credentials,
802.1X material and VPN profiles, so the directory is root-only and its files
remain `0600`. The default location under `/etc` belongs to the replaceable
root and is not used for new writes.

Before NetworkManager starts, `echo-network-state-prepare.service` creates the
persistent directory and performs a one-time legacy migration. It imports only
regular root-owned files with mode `0600` or `0400`, never follows symlinks,
never overwrites a persistent filename, and records completion under
`/var/lib/echo-os`. Insecure legacy profiles are ignored just as NetworkManager
itself ignores files accessible to non-root users. The A/B smoke writes a valid,
non-autoconnecting test profile into this directory and compares it byte for
byte after root replacement and again after automatic rollback.

These profiles can contain passwords and private keys in plaintext inside the
unlocked system. Their permissions prevent access by ordinary users, and LUKS2
on `echo-var` protects them from offline disk access after shutdown.

## Locale, keyboard and timezone

Locale, console/X11 keymap and timezone configuration normally lives under
`/etc`, so it would revert to vendor defaults whenever an A/B update replaces
the root. `echo-region-state-restore.service` runs before OEM setup, SDDM or the
credential-gated desktop. On the first boot it validates the image defaults and
creates `/var/lib/echo-os/region-state.json`; later roots validate and apply the
persistent state through `localectl` and `timedatectl`. A corrupt, unsupported,
non-root-owned or non-`0600` state file fails closed.

The OEM flow invokes `echo-region-state --configure` and accepts only exact
values returned by the installed system locale, console-keymap and timezone
catalogs. The image compiles a curated first-release locale set and explicitly
ships Debian console keymap and IANA timezone data. Commands are fixed argument
arrays and never pass settings through a shell.

Credential-backed factory/VM first use invokes the separate fixed-arity
`--configure-values LOCALE KEYMAP TIMEZONE` entrypoint. It validates all three
values against those same installed catalogs, applies and reads them back,
persists the same root-only state schema and emits an `oem-credential` marker.
The entrypoint does not weaken or bypass any regional validation.

`echo-region-state-capture.path` watches `/etc/locale.conf`,
`/etc/vconsole.conf` and `/etc/localtime`, while `echo-os-update apply` forces a
fresh capture immediately before replacing the inactive root. The A/B smoke
injects `zh_CN.UTF-8`, the `us` keymap and `Asia/Shanghai`, then verifies both
the updated and automatically rolled-back roots activate and preserve those
exact values. Actual D-Bus activation of systemd-localed/systemd-timedated and
the console/X11 effect still require the Linux raw boot test.
