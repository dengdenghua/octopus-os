# Echo OS removable storage

Echo OS uses the distribution UDisks2 system daemon as the single removable
block-device authority. Dolphin and KDE Solid request mount, unmount, unlock,
format and power-off operations over the system D-Bus API; UDisks2 applies its
upstream PolicyKit actions. The Echo renderer receives no raw block-device or
mount IPC and cannot choose a privileged command.

The image explicitly carries read/write tooling for FAT/VFAT, exFAT, NTFS,
ext2/3/4, Btrfs and XFS, plus KDE's MTP/AFC KIO integration for phones and
portable media devices. The system does not silently mount media during boot.
Mounting is on demand from Dolphin or another portal-aware application, so an
inserted untrusted volume is not traversed before the user opens it.

`echo-removable-storage-health.service` requires the real UDisks2 service before
SDDM, the direct desktop and boot blessing. It verifies the D-Bus owner and
status API, the immutable UDisks2/udev/D-Bus/PolicyKit files, Dolphin, KDE MTP
integration and every declared filesystem helper. The bounded success marker is:

```text
ECHO_REMOVABLE_STORAGE_READY provider=udisks2 policy=polkit mount=on-demand filesystems=vfat,exfat,ntfs,ext4,btrfs,xfs portable=mtp
```

Portable tests validate fail-closed behavior without mounting host media. The
Linux raw acceptance gate must still attach disposable FAT, exFAT, NTFS, ext4,
Btrfs and XFS images plus an emulated hot-plug disk, then prove Dolphin-visible
mount, byte write/read, unmount, power-off and unplug behavior. Physical USB,
SD-card readers, encrypted removable media and MTP phones remain a real-device
matrix rather than a source-level claim.
