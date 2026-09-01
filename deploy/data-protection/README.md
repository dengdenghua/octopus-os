# Echo OS data protection

Echo OS keeps the replaceable A/B vendor roots updateable and protects mutable
device data with three per-device LUKS2 volumes:

- `echo-var` protects device identity, account state, NetworkManager profiles,
  Flatpak system state and system logs;
- `echo-home` protects all local user files and per-user application state;
- `echo-swap` prevents plaintext memory pages from surviving power-off.

The ESP and replaceable vendor roots are not confidentiality boundaries. They
must remain free of reusable device secrets and are protected by signed UKIs,
authenticated update bundles and, later, read-only integrity enforcement. This
is intentionally described as **encrypted device data**, not as already proven
whole-disk encryption.

## Enrollment contract

The generic install image will create the three LUKS2 volumes with a random
factory key. That key is release material and therefore does not protect an
installed device. Before the first installed boot, Recovery must:

1. generate one 256-bit, typeable recovery key without a trailing newline;
2. add that recovery key to all three LUKS2 volumes;
3. enroll the device TPM2 in all three volumes with no direct PCR binding and
   with a signed PCR 11 policy whose public key is embedded in the image;
4. prove both unlock paths work on every volume;
5. only then remove the factory key from each volume;
6. present the recovery key on the physical console and require the operator to
   retain it outside the device.

`echo_data_protection.py` implements the fixed-volume enrollment and validation
transaction. It never accepts arbitrary device paths, never passes secret bytes
on a command line, never prints either key, and leaves every factory slot intact
if any recovery/TPM enrollment fails before the removal phase. It adds and
verifies the recovery key on all volumes before attempting any TPM enrollment,
so an ephemeral installer key is never the only surviving path after a partial
transaction.

For image CI, `enroll --tpm2-device-key=TPM2B_PUBLIC` uses systemd's offline
TPM enrollment against the public SRK of the exact swtpm state that will be
attached to QEMU. That phase proves only that a `systemd-tpm2` token exists;
the following cold boot must prove the same virtual TPM can actually unseal it.
Every production UKI, including Recovery and every A/B update, carries an
expected-PCR signature made by the separate PCR policy key. This lets an
authorized new UKI unlock existing data without silently binding the disk to
one kernel build.

```bash
sudo /usr/lib/echo-os/echo-data-protection \
  check-tpm2-public-key /usr/lib/systemd/tpm2-pcr-public-key.pem
sudo /usr/lib/echo-os/echo-data-protection \
  generate-recovery-key /run/echo-os/recovery.key
sudo /usr/lib/echo-os/echo-data-protection \
  enroll /dev/nvme0n1 /run/echo-os/factory.key /run/echo-os/recovery.key
sudo /usr/lib/echo-os/echo-data-protection \
  verify /dev/nvme0n1 /run/echo-os/recovery.key
```

The Recovery console owns two later lifecycle operations; it collects secrets
through `/dev/tty`, never places them in process arguments, and requires an exact
write confirmation:

```bash
echo-recovery rotate-recovery-key /dev/nvme0n1 ROTATE-ECHO-RECOVERY-KEY
echo-recovery rebind-tpm2 /dev/nvme0n1 REBIND-ECHO-TPM2
```

Recovery-key rotation first establishes and verifies the new key on all three
volumes. Only then does it revoke the old key from any volume. An interruption
during the add phase leaves the old key everywhere; an interruption during the
revoke phase leaves the new key everywhere and can be retried with the same two
keys. Rotation requires exactly one existing TPM2 token per volume and preserves
that token byte-for-byte.

TPM2 rebind is the recovery path after TPM clear, firmware replacement or board
replacement. The operator unlocks with the independent recovery key, stale TPM2
slots are explicitly removed before enrolling the current device TPM and the
release-authorized signed PCR 11 policy. The separate wipe is required because
systemd de-duplicates same-policy enrollments and would otherwise retain the old
TPM's sealed object. Recovery access is verified on every volume before that
phase, and the live command verifies an actual unseal before reporting completion.
CI uses the replacement TPM's public SRK for offline enrollment and must then
cold-boot with that exact replacement TPM state; token presence alone is not
runtime proof.

Factory reset does not reintroduce a release factory key. Recovery recreates
all three volumes directly with a newly acknowledged recovery key, then uses
`enroll-recovery` to add the signed-PCR11 TPM2 path. A TPM failure therefore
still leaves every freshly reset volume recoverable with the key already held
by the operator.

The current tool is the enrollment core. Enabling encrypted repart definitions
also requires all of the following in the same release gate: a signed factory
key in the installer bundle, encrypted-`/var` initramfs unlock before machine-id
binding, TPM-backed QEMU smoke, encrypted home growth, encrypted factory reset,
and removal of host-side test mutations that assume `/var` is plaintext.

## Threat boundary

The target is confidentiality after shutdown against loss or theft of the
storage device. TPM auto-unlock is accepted only together with enforcing Secure
Boot and a vendor-signed PCR 11 policy. The recovery key remains the independent
path after TPM, firmware or motherboard failure. This does not protect a
running, already-unlocked device from root compromise, malicious DMA,
compromised firmware, or a user who stores the recovery key on the same device.
