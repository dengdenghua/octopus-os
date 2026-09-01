# Echo OS persistent application layer

Echo OS replaces its root filesystem atomically during A/B updates, so
applications installed into `/usr` with `apt` are not a durable user-facing
installation model. System components are supplied only by signed Echo OS
images. Desktop applications use Flatpak instead:

- system installations and their exports live under `/var/lib/flatpak`;
- per-user installations live below `~/.local/share/flatpak`;
- both `/var` and `/home` are persistent partitions outside the A/B roots;
- applications are launched from exported freedesktop desktop files and use
  `xdg-desktop-portal-kde` for mediated host access.

`echo-app-catalog.service` runs once per device and adds Flathub from the
repository definition shipped in the signed root. The definition includes the
upstream public signing key and its entire file is pinned by SHA-256 in the
provisioning script. It does not download application metadata during boot.
The current definition was fetched from the official
`https://dl.flathub.org/repo/flathub.flatpakrepo` endpoint on 2026-08-26 and
has SHA-256
`3371dd250e61d9e1633630073fefda153cd4426f72f4afa0c3373ae2e8fea03a`.
An existing remote with the same name but a different URL is never replaced
silently. The completion marker is stored in `/var/lib/echo-os`, so an A/B root
switch neither re-adds a deliberately removed source nor overwrites an
administrator's later source policy.

The visible `echo-app-store.desktop` starts KDE Discover with its supported
`--backends flatpak` whitelist. A higher-precedence hidden desktop entry masks
Discover's generic PackageKit launcher, because deb packages written to the
current root would disappear when the other root is activated. The PackageKit
binary remains a Debian dependency of Discover, but it is not part of the Echo
application-store route.

Flathub is a third-party catalog, not an Echo OS trust root. Flatpak validates
repository commits with the public key in the definition; users must still
review each application's publisher and requested sandbox permissions. Disk
encryption is still required before application data or the system Flatpak
repository can be considered protected at rest.
