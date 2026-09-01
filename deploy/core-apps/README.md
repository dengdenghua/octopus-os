# Echo OS core desktop applications

Echo OS carries a small offline-capable native application baseline instead of
requiring a package catalog before common files can open. The signed root ships
Dolphin, Konsole, Firefox ESR, Kate, Okular, Gwenview, Ark, Haruna, Spectacle
and KCalc. Because image construction disables package Recommends, Echo also
installs Ark's Debian-recommended 7zip, bzip2, unar, unzip and zip backends
explicitly. The Echo launcher discovers distribution desktop files and
continues to launch them through fixed-argument `gio launch`, never by
evaluating desktop-entry text in a command shell.

`/etc/xdg/mimeapps.list` establishes system defaults for directories, HTTP(S),
HTML, text/Markdown/CSV/JSON/XML, PDF/PostScript, common image formats,
zip/tar/7z/RAR/compressed archives and common audio/video formats. This is only
the vendor baseline: a signed-in user can override associations in the normal
per-user XDG configuration under the persistent encrypted Home volume.

`echo-core-apps-health` verifies every binary, desktop entry and the exact
root-owned MIME policy before SDDM, the direct desktop or boot blessing. It
validates desktop files but never calls `xdg-open`, `xdg-mime`, `gio` or any
application. The service runs with private devices/network and protected Home,
so readiness cannot open a document, fetch a URL, capture a screen or mutate a
user preference.

Portable tests prove fixed associations, reject added/duplicate/command-like
handlers, reject mutable or redirected policy files and fail when an app or
desktop entry is missing. The opt-in session diagnostic creates a fixed
directory plus text, PDF, PNG, ZIP, WAV and HTML fixtures below private
`XDG_RUNTIME_DIR`. It serves only the HTML fixture from an ephemeral
`127.0.0.1` port, then opens the directory, loopback HTTP URL and five files
through `/usr/bin/xdg-open`. Every target must produce the expected native
desktop identity and fixture filename in a real compositor/EWMH window, after
which the diagnostic closes that exact window through Echo's production
fixed-action provider. It additionally launches the immutable distribution
desktop entries for Konsole and KCalc through the same fixed `/usr/bin/gio launch`
path used by Echo Desktop, requires a new native window with the exact
application identity, and closes it. Echo's production IPC waits for the
bounded `gio` helper to exit zero; missing launchers, timeouts and non-zero
exits are returned to the Dock and shown to the user instead of being reported
as a successful click. The packaged X11 and Wayland diagnostics also traverse
the actual preload API: they first enumerate KCalc through
`window.echo.apps.list()`, launch the fixed `org.kde.kcalc` identity through
`window.echo.apps.launch()`, accept only a zero-exit GIO result, observe one
new non-zero-PID KCalc window, and close that exact X11 id or compositor-owned
Wayland UUID. The private `0600` completion file and final markers are available
only to standalone desktop CI, the credential-gated direct raw session, or the
SDDM Wayland gate carrying an exact root-owned `0444` request in its disposable
encrypted `/etc` overlay. The shipped image has no request file and ordinary user
sessions skip the diagnostic. It never evaluates a shell command, reads an
existing user file or reaches a non-loopback network.

The Debian desktop workflow invokes this matrix under real KWin X11 and
Wayland sessions. The installed direct-desktop raw gate repeats it on the
finished image. The disposable SDDM Wayland raw gate repeats the packaged
preload-to-GIO KCalc result without bypassing production login, and release-evidence
assembly requires both exact completion markers. These Linux gates are
currently source-defined but have not run from this macOS checkout. Dolphin UI
double-click, remote HTTP and HTTPS, per-user override and A/B persistence,
Wayland/X11 screenshot permissions, broad codec/format coverage, malformed
archives and large document/media behavior remain in the raw and hardware
matrix.

Primary package interfaces:

- Kate: <https://packages.debian.org/trixie/kate>
- Okular: <https://packages.debian.org/trixie/okular>
- Gwenview: <https://packages.debian.org/trixie/gwenview>
- Ark: <https://packages.debian.org/trixie/ark>
- Ark 7-Zip backend: <https://packages.debian.org/trixie/7zip>
- Haruna: <https://packages.debian.org/trixie/haruna>
- KDE Spectacle: <https://packages.debian.org/trixie/kde-spectacle>
- KCalc: <https://packages.debian.org/trixie/kcalc>
- freedesktop integration utilities: <https://packages.debian.org/trixie/xdg-utils>
