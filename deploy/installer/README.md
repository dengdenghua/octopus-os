# Echo OS whole-disk installer

This directory owns the first x86-64 whole-disk installation path for Echo OS.
It does not repartition the operator's current system in place. A release raw
image is authenticated, written byte-for-byte to one explicitly selected whole
disk, and then only the final `echo-home` partition is expanded to consume the
remaining capacity.

The authenticated source image has exactly ten GPT partitions in this order:
ESP; active root, root-verity hash tree and root-verity signature; three matching
inactive `_empty` slots; encrypted `echo-var`, `echo-swap` and trailing
`echo-home`. Root/hash/signature use the x86-64 Discoverable Partition
Specification types and are read-only; the mutable partitions are the only
factory-reset candidates.

## Release bundle

`create-install-bundle.sh` runs only on Linux and accepts a finished
`echo-os_VERSION.raw`. Before signing, it validates the exact ten-entry GPT
table, partition order, Discoverable Partition Specification type UUIDs, labels,
partition extents, seven immutable entries and three LUKS2 mutable entries. The
release tool opens the raw read-only, extracts its single version-matched main
UKI from the ESP, and itself performs full dm-verity root/hash/signature,
release-certificate, derived-UUID and UKI-roothash verification before signing.
It also verifies that systemd-boot, the desktop UKI and Recovery UKI all carry
that Secure Boot signer, and that both UKIs carry the authorized signed-PCR11
policy. The output directory contains exactly four authenticated files:

```text
INSTALL-MANIFEST.json
INSTALL-MANIFEST.json.gpg
FACTORY-DATA-KEY
echo-os_VERSION.raw.zst
```

`FACTORY-DATA-KEY` is deliberately public installation material, not a durable
device secret: it unlocks the identical factory-created mutable volumes long
enough for the installer to enroll the human-held recovery key and the target
TPM. Installation succeeds only after the data-protection runtime verifies both
new unlock paths and removes this factory key from all three installed LUKS2
volumes. Possession of an old install bundle must therefore not unlock a
completed installation.

The schema-3 manifest binds the product, architecture, version, GPT layout,
compressed payload SHA-256, uncompressed image SHA-256 and byte count. It also
binds the clean Echo OS repository, full Git commit/tree and the SHA-256 of the
separately retained source-identity manifest. The detached signature is made by
an externally provisioned full GPG fingerprint:

```bash
os_commit="$(git rev-parse HEAD)"
python3 packaging/image/os_source_identity.py capture \
  --repo "$PWD" \
  --expected-commit "$os_commit" \
  --output /run/release/echo-os-source-identity.json

ECHO_INSTALL_KEYRING=/run/secrets/echo-os-installer-release.gpg \
ECHO_INSTALL_SIGNING_KEY=FULL_RELEASE_FINGERPRINT \
ECHO_OS_SOURCE_MANIFEST=/run/release/echo-os-source-identity.json \
ECHO_FACTORY_DATA_KEY=/run/secrets/echo-os-factory-data.key \
ECHO_SECURE_BOOT_CERTIFICATE=/run/secrets/echo-os-db.crt \
ECHO_TPM2_PCR_PUBLIC_KEY=/run/secrets/echo-os-pcr-policy-public.pem \
  ./deploy/installer/create-install-bundle.sh \
  packaging/image/mkosi.output/install-bundle
```

The repository and install bundle contain no private release key. The matching
public keyring is embedded in the independently signed Recovery UKI at image
build time through `ECHO_INSTALL_KEYRING`. A production image build fails if no
installer trust root is selected. Compression, signing and verification occur
in a same-filesystem staging directory; the final bundle directory is published
with one atomic rename only after every check succeeds.

Bundle publication verifies its new signature with that same selected public
keyring, not merely with a public key exported from the signing process. A
release therefore fails before publication if its Recovery UKI and installer
bundle were configured with different signing identities.

`ECHO_INSTALL_KEYRING` must be a bounded binary OpenPGP export produced with
`gpg --export`, not a GnuPG home directory, keybox or secret-key export. A strict
packet parser allows only public key/subkey, user identity/attribute, signature
and trust packets; secret-key, secret-subkey, compressed/opaque, malformed and
oversized inputs are rejected both while building Recovery and before runtime
signature verification.

## Recovery workflow

Boot the Echo Recovery entry, attach or mount the signed bundle, and first run
the read-only plan:

```bash
echo-os-installer plan /path/to/install-bundle /dev/nvme1n1
```

The target must resolve to a writable whole-disk device. The installer rejects:

- a partition path instead of a whole disk;
- the disk backing the running Recovery root;
- the disk containing the installer payload;
- a target or child partition that is mounted;
- a target or child partition backing an active dm-crypt, LVM or RAID holder;
- a target containing active swap;
- a read-only or undersized target;
- a missing, untrusted, malformed, symlinked or hash-mismatched bundle.

`plan` prints the authenticated release, target model/serial/capacity and a
confirmation bound to that exact device and image digest. The plan token also
binds the kernel major:minor identity and WWN when available. It does not open
the target for writing. Installation requires copying the complete printed
token:

```bash
echo-os-installer install /path/to/install-bundle /dev/nvme1n1 \
  "INSTALL-ECHO-OS:nvme1n1:0123456789abcdef"
```

The `install` action is destructive. It re-runs all safety checks after taking
an exclusive disk lock, re-reads the confirmed identity and aborts if the device
behind the path changed. It rejects a decompressed stream shorter or longer than
the signed byte count, writes the authenticated raw image, flushes the block
device and verifies the exact uncompressed bytes through direct block-device
readback, relocates the backup GPT, lets
`systemd-repart` grow only the trailing home partition, checks ext4, expands the
home filesystem and verifies the expected filesystem types. Both plan and
install emit the SHA-256 of the exact GPG-authenticated manifest and its signed
uncompressed raw identity. Success is reported only through
`ECHO_INSTALL_COMPLETE` after the final sync, and that marker repeats the same
source raw SHA-256 so downstream evidence cannot substitute another build with
the same version string.

## Verification boundary

Local/macOS-safe checks are:

```bash
python3 deploy/installer/test_verify_install_bundle.py
python3 deploy/installer/test_verify_install_stream.py
python3 deploy/installer/test_verify_public_keyring.py
./packaging/image/verify-image.sh --static
```

The image workflow now creates a real signed bundle and invokes
`smoke-installer-install.sh` against a new sparse NBD whole disk. The shared disk
harness first requires the per-disk plan marker and proves that planning changed
neither target allocation nor either disk boundary. It then passes the exact
bound confirmation to the production `install` action, requires its completion
marker, parses the finished GPT and proves that `echo-home` grew into the added
capacity. Only then is the installed raw published to later jobs.

The same installed raw is the input to the Secure-Boot QEMU Recovery,
credential-backed first-use OEM, production SDDM/X11, selectable SDDM/Wayland
and direct-desktop cold-boot gates. OEM validation uses the real provisioning
code and a VM-only random password, while modifying only a disposable copy with
test SDDM autologin. This is a source-defined destructive release test, not a
current result: the uncommitted workflow has not run on a Linux runner from this
workspace. A release still requires that workflow to be green, followed by
human-interactive OEM/password testing, A/B dm-verity update/tamper/rollback,
LUKS2/TPM lifecycle and representative hardware installation gates. The source
now defines those Linux gates, but this workspace has not executed them on a
Linux runner. Until they are green, the bundle is a production-candidate
artifact rather than a generally installable Echo OS release.
