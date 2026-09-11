# Native Docker control boundary

The raw Echo OS image runs Docker Engine locally but never gives the public
Agent process `/var/run/docker.sock`. `echo-docker-control.service` is the only
long-running Docker control endpoint. `systemd-sysusers` provisions a dedicated
`echo-docker-control` system account, and systemd starts the proxy with only the
`echo` broker primary group and Docker socket supplementary group, plus an empty
capability bounding set. It never has a root startup window or inherits the
interactive `echo` account's groups. The broker independently admits only root,
`echo`, and this dedicated account by peer UID. The service exposes only the catalog-verified Hub API on
`127.0.0.1:2375`. The existing root storage broker performs only an
argument-free, source-owned reconciliation that reads exact Hub ownership
labels and never accepts a port, address, or rule from the Agent.

`echo-docker-credential.service` generates a 256-bit token once per installed
device in `/var/lib/echo-os/docker-proxy-token`. Both services receive it via
systemd credentials; the token is not baked into the image, unit environment,
command line, logs, or source tree. Existing credentials are accepted only
when they remain root-owned regular files with mode `0600` and exact canonical
content.

The control service can contact only loopback and the Docker Unix socket. It
observes `/var/lib/docker` through a read-only bind and reports only bounded
capacity data after confirming that Docker itself declares that exact root.
Arbitrary Docker API proxying, raw inspect/log access, and caller-selected
images, ports, mounts, commands, or Compose payloads are not exposed.
Catalog-owned Hub containers are also hidden from and denied by the generic
launcher start/stop surface; only the plan-bound Hub lifecycle may control
them, so every state transition remains coupled to firewall reconciliation.

Every successful or rolled-back Hub lifecycle operation synchronously asks the
root broker to derive the complete firewall state again. Bridge containers get
private-source rich `forward-port` rules tied to their current RFC1918 address;
host-network services get private-source input rules. A stale catalog digest,
package digest, version, name, label, port binding, network mode, container IP,
or incomplete bundle closes all managed Hub ports. `StrictForwardPorts=yes`
continues to block Docker's own published-port bypass.
