# Echo OS native document scanning

Echo OS ships Debian's SANE client/backends and KDE Skanpage as the native
document-scanning surface. Skanpage supports flatbed and automatic document
feeder devices, page ordering/rotation and saving to user-selected PDF or image
files. The Echo renderer receives no scanner device, output-path or command IPC.

The signed-root baseline supports three client-side paths:

- USB scanners use the distribution's libsane backends and udev rules. The
  first-release desktop user belongs to the `scanner` group selected by those
  rules; boot health uses `PrivateDevices=yes` and never opens a scanner.
- Driverless multifunction USB devices reuse the loopback-only `ipp-usb` proxy
  and are consumed through the `sane-airscan` eSCL backend.
- eSCL and WSD network discovery occurs only when a SANE application requests
  device enumeration. Boot health runs with `PrivateNetwork=yes` and never
  invokes `scanimage -L`, `sane-find-scanner` or `airscan-discover`.

The SANE network server is a different trust boundary. `saned.socket` is
disabled by the image preset, and `echo-scanning-health` refuses to bless login
while that scanner-sharing listener is enabled or active. The AirScan policy
keeps remote devices remote (`pretend-local = false`), disables debug logging
and payload hexdumps, and contains no fixed third-party scanner endpoint.
Scanned documents have no system spool or Echo history: Skanpage writes only to
the location explicitly selected by the signed-in user.

Portable tests validate the fixed policy and fail-closed health path without
enumerating any host device. Linux raw and physical acceptance must still cover
flatbed and ADF USB scanners, driverless USB eSCL, on-demand LAN eSCL/WSD,
multi-page PDF/image output, cancel and unplug behavior, denied device access,
suspend/resume, malformed scanner responses and confirmation that no scan
payload enters system logs or a global spool.

Primary interfaces:

- Debian SANE utilities: <https://packages.debian.org/trixie/sane-utils>
- Debian SANE library/backends: <https://packages.debian.org/trixie/libsane1>
- Debian eSCL/WSD backend: <https://packages.debian.org/trixie/sane-airscan>
- Debian KDE Skanpage: <https://packages.debian.org/trixie/skanpage>
- KDE Skanpage capabilities: <https://apps.kde.org/skanpage/>
- AirScan backend policy: <https://manpages.debian.org/trixie/sane-airscan/sane-airscan.5.en.html>
