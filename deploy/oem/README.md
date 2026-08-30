# Echo OS local identity and first boot

The Linux local account and the Echo/Agent cloud account are deliberately
separate identities. The local account owns files, sessions and `sudo`;
logging into the Agent workbench never grants an OS login or administrator
privilege.

On an unprovisioned production image, `echo-oem-setup.service` owns tty1 before
SDDM starts. It validates a display name, one-label device hostname and local
password, then invokes the separate regional-state tool to select an installed
locale, console/X11 keymap and IANA timezone. It configures the fixed
first-release account `echo` (UID 1000) as a password-protected member of
`sudo`. Completion is recorded atomically in
the private `/var/lib/echo-os/oem-complete.json` marker. No password or hash is
written to that marker. The password hash is stored separately as the root-only
`/var/lib/echo-os/local-account.shadow`; plaintext is never persisted.
The device name is deliberately limited to 15 characters even though DNS labels
can be longer. This keeps Linux, Samba, Windows discovery and the Echo UI on one
untruncated device identity; first boot and automated OEM credentials reject a
longer name before changing the host.

For factory automation and destructive VM validation, the same service can
optionally inherit one system credential named `echo.os.oem`. It is a bounded,
exact-schema JSON object containing display name, hostname, local password,
locale, keymap and timezone. The service accepts it only from systemd's private
credential directory, refuses symlinks, oversized input, duplicate/extra fields
and group/other-readable files, and passes the plaintext password only to
`chpasswd` standard input. Regional values still have to match the catalogs
installed in that exact image. No normal disk contains this credential; when it
is absent, the tty1 interaction above remains the only first-use path.

This separation is required by A/B updates: a newly installed root contains a
locked vendor `/etc/shadow`. Before PID 1 starts, initramfs mounts the selected
root's vendor `/etc` as the read-only lower layer of an overlay whose writable
upper and work directories live on encrypted `echo-var`. Thus `chpasswd`, PAM
and ordinary account tools retain normal rename semantics, while no password
hash is ever copied into either unencrypted vendor root. Before SDDM starts,
`echo-local-account.service` verifies or restores the persistent hash through
encrypted `chpasswd` input, never a shell or command-line argument.
`echo-account-capture.path` watches local password, GECOS and hostname changes
and refreshes the private `/var` state, so a later root replacement does not
revert a password the owner changed normally.
The captured state also records its root image version. A locked account is
restored only when the running version changed; a deliberate lock on the same
root fails closed and is never silently undone.

Regional settings have their own root-only persistent JSON and capture path;
they are deliberately not mixed with the account password hash. The restore
unit initializes or applies them before OEM setup and SDDM, and an update forces
both account and regional capture before writing an inactive root.

SDDM then presents the real PAM-backed Linux login and launches the default
`Echo OS` X session. It also discovers an explicitly labelled
`Echo OS (Wayland Candidate)` session backed by KWin DRM, XWayland and
KScreenLocker; it remains manually selectable until Linux raw and hardware
gates pass. There is no production autologin setting. The system boot is only
blessed after SDDM has established its seat0 greeter.

The production X11 greeter exposes the conventional `Super+Alt+S` screen-reader
toggle without starting speech on every boot. `GreeterEnvironment` enables the
Qt accessibility bridge, while the root-owned Xsetup wrapper first preserves
Debian's vendor display setup and then starts one transient helper as the
unprivileged `sddm` user. The helper accepts only a local X display and an
SDDM-owned, non-writable Xauthority file; it waits for a local, non-remote
`seat0` logind session whose class is exactly `greeter`, grabs only that fixed
shortcut, and can execute only `/usr/bin/orca --replace --no-setup --disable
splash-window` without a command shell. Orca configuration and cache are kept
under the greeter's volatile `/run/user/<uid>` tree. The matching Xstop wrapper
terminates the transient helper and preserves Debian's vendor cleanup.

The image workflow now creates a provisioned disk, removes its test autologin,
cold-boots back to the production greeter and sends `Super+Alt+S` through
QEMU's virtual keyboard. It passes only after observing both
`ECHO_SDDM_ACCESSIBILITY_READY` and the fixed Orca start marker. That executable
gate is not runnable in the current macOS workspace and does not prove audible
speech or keyboard focus behavior; physical audio, braille, focus order and
disabled-user testing remain Linux/raw and hardware acceptance work.

VM cold-boot tests use two separate credential scopes. `echo.os.ci-session`
skips OEM/SDDM and conditionally enables the direct KWin session. The first-use
gate instead creates a private, randomly passworded `echo.os.oem` host file,
passes it through mkosi/systemd, and boots a temporary copy with only a test
SDDM autologin drop-in. It requires production OEM completion, persisted region
state, SDDM and the packaged desktop in one cold boot. Neither credential nor
the test autologin policy is present in the delivered disk image.

For the A/B lifecycle gate, that OEM boot runs non-ephemerally against a new
same-filesystem raw copy. After the VM stops, the harness opens encrypted
`echo-var` only with the private CI recovery key, removes and verifies the
absence of its test autologin, validates the exact OEM marker and private
password-hash modes, then atomically publishes the provisioned raw. Subsequent
update and rollback checks reuse those states rather than synthesizing a second
identity.
