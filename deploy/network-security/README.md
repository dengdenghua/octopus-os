# Echo OS host firewall

The general desktop image uses Debian firewalld 2 with its nftables backend and
the KDE Plasma firewall KCM. A fresh machine starts in the vendor
`echo-public` zone: established traffic and loopback continue to work, DHCPv6
client traffic is allowed, and no SSH, Agent, application, arbitrary port,
masquerade or intra-zone forwarding rule is opened by Echo OS.

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
that ports, protocols, source ports, rich rules, masquerading and forwarding
are empty/off. Failure prevents networking and boot blessing instead of
silently starting an unfiltered desktop.

Portable policy and coordinator tests do not prove kernel packet filtering.
The Linux raw gate must observe `ECHO_FIREWALL_READY`; the physical acceptance
matrix must additionally scan from another machine, exercise IPv4/IPv6,
Wi-Fi/Ethernet/VPN zone changes, explicitly authorized sharing, sleep/resume,
container port publication and update/rollback persistence.
