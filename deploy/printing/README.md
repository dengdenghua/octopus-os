# Echo OS private local printing

Echo OS ships a real CUPS scheduler and KDE Print Manager instead of exposing
printing as an Electron command bridge. Applications use the normal libcups/Qt
print interfaces. Printer administration is handled by the distribution's
`cups-pk-helper` system D-Bus service and PolicyKit policy, with the existing
per-session KDE PolicyKit authentication agent presenting authorization UI.

The signed-root workstation policy is intentionally local-only:

- CUPS listens on `localhost:631` and `/run/cups/cups.sock`; wildcard `Port`,
  `SSLListen`, `ServerAlias`, includes, browse peers, remote sharing and the web
  interface are rejected.
- New printers are not shared by default. Automatic LAN printer discovery is
  not enabled and the Echo firewall opens no mDNS or CUPS port. An owner may
  still add a known remote IPP/IPPS URI, which is an outbound client action.
- Driverless USB printers use Debian `ipp-usb` with its proxy fixed to loopback.
  Avahi is present for that local DNS-SD registration, while ipp-usb's
  loopback-only interface policy and the closed host firewall keep it from
  becoming an Echo OS network print service.
- Page logging, completed-job history and submitted job-file retention are
  disabled. Logs are bounded; verbose IPP/HTTP/USB payload tracing is disabled.
- `/var/spool/cups` must resolve to `/dev/mapper/echo-var`, so transient print
  data stays on the per-device encrypted persistent volume rather than the
  verified root or an unencrypted scratch filesystem.

`echo_printing_policy.py` parses both shipped configurations, rejects duplicate
overrides and checks root ownership/modes. `echo-printing-health` additionally
requires the real scheduler/socket, `lpstat` API, IPP backends, PDF filters,
KDE KCM, PolicyKit mechanism and driverless USB runtime before SDDM, the direct
desktop or boot blessing can complete. The health path is read-only and runs
with `PrivateDevices=yes`; it cannot add a printer or submit/cancel a job.

This source and portable fault testing are not hardware evidence. Linux raw and
physical acceptance must still cover Qt/GTK/Electron printing, correct/denied
administrator authorization, USB IPP hot-plug, manual IPP/IPPS network printers,
paper/ink errors, cancel/retry, suspend/resume, malformed jobs, disk pressure and
confirmation that job payloads disappear after completion and reboot.

Primary interfaces:

- CUPS scheduler policy: <https://openprinting.github.io/cups/doc/man-cupsd.conf.html>
- CUPS security model: <https://openprinting.github.io/cups/doc/security.html>
- Debian CUPS daemon: <https://packages.debian.org/trixie/cups-daemon>
- Debian KDE Print Manager: <https://packages.debian.org/trixie/print-manager>
- Debian CUPS PolicyKit helper: <https://packages.debian.org/trixie/cups-pk-helper>
- Debian driverless USB proxy: <https://packages.debian.org/trixie/ipp-usb>
