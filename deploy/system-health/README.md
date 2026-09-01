# Echo OS crash collection

Echo OS uses Debian's native `systemd-coredump` socket and stores compressed
process cores locally under `/var/lib/systemd/coredump`. The directory is on the
device's separately encrypted `echo-var` LUKS2 mapping, not on either immutable
A/B root slot.

The production drop-in fixes these limits:

- at most 512 MiB is processed and retained for any one crashing process;
- external storage is capped at 1 GiB and preserves at least 2 GiB of free disk;
- namespace entry is disabled; and
- no uploader, telemetry endpoint, credential, or automatic off-device transfer
  is installed by this subsystem.

Core files can contain passwords, tokens, document fragments, and other process
memory. They are diagnostic evidence, not ordinary user documents. Access stays
root-controlled. Export for support must be an explicit administrator action
with the affected user's consent; a future support UI must redact metadata and
must never silently upload a raw core.

`echo-crash-health.service` is required by `boot-complete.target`. It refuses to
publish readiness unless the source and effective systemd configuration match,
the storage directory is root-owned, `/var` is mounted from
`/dev/mapper/echo-var`, and `systemd-coredump.socket` is active. Raw-image cold
boot is the runtime proof; the portable policy test intentionally does not
pretend that macOS can exercise Linux coredump activation.
