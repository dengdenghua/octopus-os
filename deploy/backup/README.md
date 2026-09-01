# Echo OS encrypted user backup and migration staging

Echo OS uses Debian's `restic` package as its encrypted, deduplicating backup
engine. It does not invent a second archive or cryptographic format. The first
release deliberately supports only a locally attached POSIX filesystem mounted
at `/mnt/echo-backup`; it rejects network filesystems, the internal Echo root,
var, swap and home volumes, a group/world-writable mount root, nested mounts,
symlinks and a repository not owned privately by UID 1000.

The source allowlist is exactly `/home/echo` and `/var/lib/echo-agent`.
Machine ID, `/etc/shadow`, persistent NetworkManager profiles, TPM/LUKS tokens,
recovery keys, systemd credentials and other `/var/lib/echo-os` device state are
not migration data and are never passed to restic. Backup subprocesses run as
the unprivileged `echo` user, receive the password through an anonymous
`memfd`, inherit no cloud/backend environment variables and use no command
shell. The native Agent is stopped only if it was active, and is restarted plus
health-checked on both success and failure.

Backups are intentionally offline in this baseline: `echo` must have no
local, remote or closing user session. The tool stops SDDM, stops the native
Agent, checks the user session state again and refuses any remaining process
whose real, effective, saved or filesystem UID is 1000 before reading or
restoring data. This closes both a new graphical-login race and a lingering
same-user process race. It also guarantees that a partial service-stop failure
still enters the recovery path before the command returns. This avoids claiming
application-consistent Firefox, SQLite or desktop state on the current ext4
layout without a filesystem snapshot. A successful command accepts the exact
snapshot ID emitted by the backup operation, performs a full
`restic check --read-data`, confirms that ID in the authenticated index, then
records only the repository/snapshot IDs, exact staging-directory identity and
timestamp in the root-only
`/var/lib/echo-os/user-backup-state.json` marker.

## External disk and first repository

Format a dedicated external partition as ext4, XFS, Btrfs or F2FS and mount it
at the fixed path. The exact device command is intentionally not automated by
the backup tool because formatting the wrong disk is destructive.

```console
sudo install -d -m 0755 /mnt/echo-backup
sudo mount /dev/disk/by-uuid/YOUR-BACKUP-UUID /mnt/echo-backup
sudo echo-os-backup init
```

The password is prompted without echo and confirmed. It is never stored by the
interactive command. Losing it makes the encrypted repository unrecoverable.

Run an offline backup after logging out of the graphical `echo` session:

```console
sudo echo-os-backup backup
sudo echo-os-backup snapshots
sudo echo-os-backup check
```

For a manually scheduled system service, provision the password as an encrypted
systemd credential rather than an environment variable or command-line value:

```console
sudo install -d -m 0700 /etc/credstore.encrypted
read -sr ECHO_BACKUP_PASSWORD
printf %s "$ECHO_BACKUP_PASSWORD" | sudo systemd-creds encrypt \
  --name=echo-backup-password - \
  /etc/credstore.encrypted/echo-backup-password
unset ECHO_BACKUP_PASSWORD
sudo systemctl start echo-user-backup.service
```

The unit is not enabled by default: an external repository, its encrypted
credential and an intentional offline window have to exist first.

## Restore and migration boundary

Restore never overwrites live home or Agent state. It first performs a full
repository data check, resolves `latest` only among snapshots tagged
`echo-os-user-v1`, and restores into a newly created private directory below
`/home/echo/.echo-restore-staging`:

```console
sudo echo-os-backup restore latest
sudo echo-os-backup restore FULL_OR_UNIQUE_SNAPSHOT_PREFIX
```

The staged tree must contain only `home/echo` and `var/lib/echo-agent`, have
UID 1000 ownership, contain no device/FIFO/socket nodes, and contain no absolute
or escaping symlinks. Promotion is a separate Recovery transaction and never
runs automatically. From the offline Recovery console, first select the exact
whole installed disk and obtain its content-bound transaction token:

```console
echo-recovery restore-plan /dev/nvme0n1
echo-recovery restore-promote /dev/nvme0n1 "$PROMOTE_TOKEN_PRINTED_BY_PLAN"
```

Promotion uses same-filesystem renames for Home and Agent data plus a private,
root-owned journal on `echo-var`. The former trees remain inside mode `0700`
root-only containers; any intermediate phase blocks Agent, SDDM, the direct
desktop and boot blessing until Recovery resumes it. A complete promotion is a
trial: normal boot may start, but old data remains recoverable. After validating
the normal desktop, return to Recovery and choose exactly one path printed by
`restore-status`:

```console
echo-recovery restore-status /dev/nvme0n1
echo-recovery restore-rollback /dev/nvme0n1 "$ROLLBACK_TOKEN_PRINTED_BY_STATUS"
echo-recovery restore-commit /dev/nvme0n1 "$COMMIT_TOKEN_PRINTED_BY_STATUS"
```

Rollback makes the old Home and Agent state active and retains rejected trial
changes under private staging. Commit is the only operation that deletes the
retained old trees. Each command reopens only the closed `echo-var` and
`echo-home` LUKS2 partitions with the device recovery key; the transaction
never touches either dm-verity root slot, the ESP, swap, LUKS tokens or the
external restic repository.

This source contract has 13 backup-policy tests and 7 crash/restart transaction
tests, but it is not a completed backup claim. The Linux/raw gate is defined to
initialize a repository on a disposable external block device, back up mixed
files/xattrs/ACLs/sparse data, reject wrong credentials, disk exhaustion and a
corrupted pack, then copy the staged whole-disk raw into independent rollback
and commit branches. The commit branch must cold-boot the production SDDM
session while old data is retained before an explicit commit. That gate still
has to run successfully on a Linux runner and its logs must be reviewed; real
external-disk disconnects, lost-password handling and physical-device migration
remain separate acceptance work.
