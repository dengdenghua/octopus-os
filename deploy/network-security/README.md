# Echo OS host firewall

The general desktop image uses Debian firewalld 2 with its nftables backend and
the KDE Plasma firewall KCM. A fresh machine starts in the vendor
`echo-public` zone: established traffic and loopback continue to work, DHCPv6
client traffic is allowed, and no SSH, Agent, application, arbitrary port,
masquerade or intra-zone forwarding rule is opened by Echo OS. Approved native
SMB/Time Machine shares add only Samba and WSD rules scoped to RFC1918/ULA
sources; approved NFS exports add only NFS, mountd and rpc-bind for their exact
validated private CIDRs. Approved DLNA publication adds only UDP 1900 and TCP
8200 for RFC1918 IPv4 sources; the service itself sees only explicitly selected
read-only bind mounts. Removing the last dependent share removes those rules.

`StrictForwardPorts=yes` is intentional. A Docker or Podman published port does
not silently bypass the host firewall; an administrator must also authorize
that service/port through firewalld. The Electron renderer receives no direct
firewall IPC. KDE talks to the system firewalld D-Bus service, whose existing
PolicyKit boundary provides the interactive administrator authorization.

The delivered `firewalld.conf` is stored in the encrypted persistent `/etc`
overlay. The default zone may therefore be changed through the authorized KDE
KCM and remains across an A/B root replacement. Backend, table ownership,
strict forwarded-port handling, reload drop policy, reverse-path filtering and
fail-safe `CleanupOnExit=no` are boot invariants. A changed default zone is
accepted only when firewalld confirms that exact zone exists; a fresh signed
image and the raw cold-boot gates still require `echo-public`.

`echo-firewall-health.service` is required by NetworkManager, SDDM, the direct
desktop and `boot-complete.target`. It waits for firewalld, validates the
bounded root-owned policy, checks the system D-Bus owner and the nftables
`inet firewalld` table, and confirms runtime/default-zone agreement. For the
vendor default it additionally proves that only `dhcpv6-client` is allowed and
that ports, protocols, source ports, masquerading and forwarding are empty/off.
Rich rules must exactly match the root-only native firewall state in both the
permanent and runtime firewalld views; an absent state requires an empty rule
set. Failure prevents networking and boot blessing instead of silently starting
an unfiltered desktop.

Raw Hub containers use the same transaction manager. The unprivileged Docker
control endpoint synchronously requests one argument-free root reconciliation
after success and rollback. Rules are re-derived from the immutable catalog,
complete ownership labels, exact Docker port bindings and the running public
container's current network mode/address. Bridge ports use RFC1918-source rich
forwarding and host-network ports use RFC1918-source input rules; any ambiguity
removes all managed Hub exposure while `StrictForwardPorts=yes` keeps Docker's
implicit DNAT blocked.

Portable policy and coordinator tests do not prove kernel packet filtering.
The Linux raw gate must observe `ECHO_FIREWALL_READY`; the physical acceptance
matrix must additionally scan from another machine, exercise IPv4/IPv6,
Wi-Fi/Ethernet/VPN zone changes, explicitly authorized sharing, sleep/resume,
container port publication and update/rollback persistence.
