# Echo OS native-storage broker

The native Echo OS Agent runs as the unprivileged `echo` account. Storage write
plans and mutations are delegated over `/run/echo-storage-broker/broker.sock`
to this root-owned, Unix-only service. The Agent authenticates the caller,
requests a root-side plan, completes password approval and durable attempted
audit recording, then sends the matching desired state and plan ID for apply.

The broker accepts only 38 source-owned `plan`/`apply` pairs plus the two
fixed root-state projections needed for sharing overview and one share's ACL.
It has no TCP listener, shell endpoint, dynamic import endpoint, arbitrary
path API, or browser-facing credentials.
The socket directory is root-owned and not writable by the `echo` group; Linux
peer credentials additionally restrict requests to root and the configured
`echo` account.

The service deliberately does not use systemd options that create a private
mount namespace (`ProtectSystem`, `ProtectHome`, `PrivateTmp`, or
`ReadWritePaths`). Native pool and volume operations must update the host's
mount namespace, `/etc`, and selected block-device sysfs controls. Its narrow
operation protocol, root-side plan revalidation, plan IDs, socket ownership,
peer UID check, lack of an IP socket, and the Agent's approval/audit envelope
are the privilege boundary.
