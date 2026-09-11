# Echo OS DLNA

Echo uses Debian ReadyMedia (`minidlna`) as an optional, read-only DLNA/UPnP-AV
server. The service is disabled until an administrator previews and approves a
registered shared folder. It advertises only TCP 8200 and UDP 1900 to private
IPv4 networks through the managed firewall.

`echo-dlna.service` hides all native NAS mount roots. The generated service
drop-in reintroduces only approved folders below `/run/echo-dlna/media`, using
UUID-derived destinations and `BindReadOnlyPaths`. The daemon runs as Debian's
dedicated `minidlna` user, receives only the `users` group needed to traverse
the selected source, has no capabilities, cannot see home directories or
devices, and can write only its private database directory.

Before every service start, a fixed root preflight revalidates each share UUID,
mounted volume UUID, real directory and the exact managed firewall state. A
missing/replaced volume, stale registry, modified config or firewall drift keeps
the unprivileged daemon stopped.

The preflight imports the image-pinned appliance package from
`/opt/echo-agent/site-packages`; it never falls back to a mutable source tree.

The generated configuration and drop-in are canonical and tamper-evident.
Removing the final share stops and disables the service, closes both firewall
ports and preserves all media data.
