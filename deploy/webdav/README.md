# Echo OS WebDAV

After an administrator approves WebDAV publication, Echo exposes registered
NAS shares at `https://<device>/webdav/`. The gateway and its jail/refresh
units are disabled by default. nginx is the only network-facing TLS endpoint;
rclone listens on `127.0.0.1:5005` and uses `appliance.native_webdav` as its
Basic-auth proxy.

Each accepted Echo login maps to a dedicated OpenSSH chroot containing only
root-managed bind mounts for shares readable by that POSIX identity. A
per-device gateway key reaches a second sshd bound only to
`127.0.0.1:22022`; that sshd disables passwords, shells, forwarding, TTYs and
all non-SFTP commands. File operations therefore execute as the mapped user,
so POSIX ACLs remain the authorization source of truth.

The refresh path restarts rclone whenever the account/share view changes. ACL
revocation is enforced immediately by the kernel even before the next refresh;
the 30-second timer only reconciles directory visibility and newly granted
shares. Both jail reconciliation oneshots intentionally run in the host mount
namespace: filesystem `Protect*=` options would make bind mount additions and
removals disappear when the oneshot exits. Their remaining sandbox permits only
`AF_UNIX` and the bounded capabilities needed for ownership, identity switching
and mount operations.

Windows' built-in WebDAV client requires HTTPS for Basic authentication. The
per-device certificate is generated at first boot and must be trusted on the
client before mapping the network location.

## Runtime validation

The repository's source and delivery tests cover fail-closed authentication,
account mapping, service boundaries, TLS proxying, mountpoint exit contracts and
active-bind idempotency. A Debian 13 QEMU run additionally exercises the real
nginx/rclone/OpenSSH/ext4 ACL stack with PROPFIND, PUT, GET, MKCOL, MOVE, DELETE,
LOCK and UNLOCK. It also proves that WebDAV remains unpublished until the
approval-bound controller enables it, that disabling a member returns 401 and
removes that account's host-visible bind mounts, and that globally disabling
publication stops/disables every gateway and refresh unit and removes all bind
mounts. The retained current-source v4 result is
`C:\飞牛os\_vmtest\webdav-linux-runtime-v4.json`, SHA-256
`96997562d8a966d88ea9e1cac80d4e1d366ea53daa4d259963324552b961c239`.

This is service-side VM evidence, not acceptance of a newly built raw image or
Windows/macOS clients. Those clients still need certificate-trust, mapping,
locking and large-file tests against a frozen release candidate.
