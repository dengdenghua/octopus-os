# Echo Recovery

Echo Recovery is a self-contained UKI: its kernel, initrd, tools and recovery
service live in one EFI file, so it does not need either installed root slot.
systemd-boot discovers it from `EFI/Linux/`, while `loader.conf` keeps
`echo-os_*` as the normal default.

When `ECHO_SECURE_BOOT_KEY` and `ECHO_SECURE_BOOT_CERTIFICATE` are supplied to
the image build, this UKI is signed with the same external identity as the
desktop UKI and systemd-boot. The artifact verifier checks all three PE
signatures; no private key is copied into the image.

The automatic startup path is read-only and emits `ECHO_RECOVERY_READY` after
enumerating disks, partitions and boot state. Its marker includes the verified
40-character OS commit read from the source identity sealed into the signed
Recovery UKI; a missing, redirected or malformed identity fails closed. The
desktop dm-verity root embeds the exact same build-captured identity. The
console then provides:

- `echo-recovery check-root DEVICE` — resolves the same-version hash tree and
  signature partition, authenticates the PKCS#7 root-hash signature and
  roothash-derived GPT UUIDs, then runs a full read-only `veritysetup verify`;
- `echo-recovery restore-plan DISK` / `restore-status DISK` — opens only the
  closed encrypted Home and var volumes read-only, validates the exact fully
  checked staging tree and prints transaction-bound promote/rollback/commit
  tokens without changing data;
- `echo-recovery restore-promote DISK TOKEN` — atomically promotes staged Home
  and Agent state across their two filesystems, retaining the previous trees in
  root-only containers for a normal-boot trial;
- `echo-recovery restore-rollback DISK TOKEN` — returns the retained old trees
  to service and preserves trial changes in private rejected staging;
- `echo-recovery restore-commit DISK TOKEN` — after a successful trial boot,
  explicitly deletes only the retained old Home and Agent trees;
- `echo-recovery rotate-recovery-key DISK ROTATE-ECHO-RECOVERY-KEY` — adds and
  verifies a new recovery key on every mutable LUKS2 volume before revoking the
  old key anywhere, preserving the existing TPM2 token and data;
- `echo-recovery rebind-tpm2 DISK REBIND-ECHO-TPM2` — uses the independent
  recovery key to replace stale TPM2 slots after TPM clear, firmware service or
  motherboard replacement, then verifies live automatic unlock;
- `echo-recovery factory-reset DISK ERASE-ECHO-DATA` — after validating a whole
  disk and unmounted `echo-var`/`echo-swap`/`echo-home`, recreates only those
  three `FactoryReset=yes` LUKS2 partitions, rotates the recovery key and
  enrolls the release-authorized signed-PCR11 TPM2 policy. The recreated GPT
  entries retain `NoAuto=yes`, so the normal explicit encrypted mount policy
  remains authoritative after reset.
- `echo-os-installer plan BUNDLE DISK` — authenticates a release bundle and
  prints a confirmation bound to one safe whole disk without writing it;
- `echo-os-installer install BUNDLE DISK CONFIRMATION` — after repeating all
  safety checks, irreversibly installs the authenticated whole-disk image and
  expands its trailing `echo-home` filesystem.

Recovery contains only the selected release public keyring. The installer's GPG
private key stays outside both the repository and UKI. A production image build
requires `ECHO_INSTALL_KEYRING`; Recovery reports the installer command but will
reject every bundle when no valid trust root has been provisioned. Detailed
bundle, target-safety and confirmation rules are in `deploy/installer/README.md`.
The keyring is parsed as a bounded binary OpenPGP public export; secret key
packets and opaque/compressed packet types are rejected before it reaches
`gpgv` or the Recovery UKI.

Because the OEM completion marker and A/B local-account state live in
`echo-var`, a successful data reset deliberately removes them. The next normal
boot blocks SDDM and returns to the one-time local administrator setup before
any old root-slot password can be used graphically.

Recovery deliberately has no in-place signed-root repair command. Mutating
ext4 metadata or data would invalidate the dm-verity tree and its release
signature; recovery must boot another verified slot or reinstall/redeploy an
authenticated release instead.

Restore promotion has a separate fail-closed boot contract. A root-owned
journal records every cross-filesystem rename boundary. Normal boot accepts no
transaction or a fully promoted trial only; any prepared, partially promoted,
rolling-back or committing phase blocks Agent, SDDM, direct desktop startup and
boot blessing until Recovery resumes the exact transaction. Old and prepared
Agent data are hidden in mode `0700` root containers, so the trial user cannot
alter the rollback source. Commit is the sole deletion authorization.

Recovery console access is a high-privilege physical-access path. The current
A/B roots are dm-verity protected but intentionally not encrypted, while
`echo-var`, `echo-swap` and `echo-home` are independent LUKS2 volumes. Production
hardware still needs enforcing Secure Boot and protected release/TPM identities.
The recovery UKI and root-hash metadata must be signed by the selected protected
release identity before those controls can be claimed.

The current CI source defines an authenticated install onto an ephemeral NBD
whole disk. Its shared harness proves the initial plan is read-only, executes the
production write path and checks the expanded final GPT. A separate factory-reset
gate then operates on a copy, requires the old recovery and factory credentials to
fail, inspects the new signed-PCR11/SRK tokens, proves all seven immutable
partitions (ESP plus both root/hash/signature triplets) were unchanged, and
cold-boots the reset copy with the same virtual TPM. Independent
key-lifecycle coverage rotates recovery access on another copy, proves decrypted
data and immutable partitions are byte-identical, binds a separately initialized
replacement TPM, and boots only with that new TPM state. Recovery,
production-login, Wayland-candidate and direct-desktop Secure-Boot gates exercise
the original installed disk. The backup gate additionally defines two independent
copies of one fully verified staged raw: one performs promote/rollback, while the
other performs promote, production SDDM trial boot and explicit commit. Its
evidence binder requires repository, snapshot and transaction identities to
agree across the whole flow. The uncommitted workflow has not run on a Linux
runner from this workspace, so these remain executable release-gate definitions
rather than installation, dm-verity boot/rejection, reset, key-recovery, restore
transaction or boot evidence.
