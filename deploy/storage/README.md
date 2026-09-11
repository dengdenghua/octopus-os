# OpenZFS image contract

The raw Echo OS image installs Debian OpenZFS from `contrib` together with
the exact kernel header metapackage and DKMS. Image assembly must produce a
module for every installed kernel before it can finish.

Release builds sign every OpenZFS DKMS module with the same private identity
used for the UKI and enrolled as the appliance UEFI `db` key. The key is
copied into an isolated, mode-0700 mkosi skeleton before package installation,
using DKMS's conventional `mok.key`/`mok.pub` paths. DKMS therefore signs each
new module once, before compression; post-install verification then erases the
transient key and certificate. The artifact verifier rejects unsigned module
payloads and any residual DKMS signing identity.

At boot, Debian's `zfs-load-module.service` loads the module. The
image adds a narrow ordering drop-in because upstream `zfs-zed.service` has a
module-presence condition but no ordering dependency on that loader. The
`echo-zfs-health.service` gate then binds `modinfo` to `uname -r`, requires a
PKCS#7 signature, confirms the already-loaded kernel interface and both `zfs`/`zpool` APIs,
and blocks the privileged storage broker and `boot-complete.target` if any
part is unavailable. The verifier deliberately retains host `/dev/zfs` access
so this is a real kernel API probe, while systemd still denies network access,
filesystem mutation and kernel-module mutation. Pool import remains limited to the normal OpenZFS cache;
the image does not scan and import arbitrary foreign pools automatically.
