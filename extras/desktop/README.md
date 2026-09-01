# Echo desktop release bridge

Echo has one canonical React frontend in `frontend/`. The Electron main
process lives beside that frontend in `frontend/electron/`; this directory is
the native build bridge used to produce the frozen backend and pinned Codex
runtime before electron-builder creates a platform installer.

Released desktop applications are self-contained. They do not download
Python, resolve a host `codex` command, or connect to a second Agent WebUI on
first launch.

## Supported artifacts

| Platform    | Artifact                           | Native payload                                           |
| ----------- | ---------------------------------- | -------------------------------------------------------- |
| Windows x64 | signed NSIS `.exe`                 | PyInstaller backend + Authenticode-signed Codex runtime  |
| macOS arm64 | signed and notarized `.dmg`        | PyInstaller backend + pinned Apple Silicon Codex runtime |
| macOS x64   | local/explicit compatibility build | Rosetta-built backend + pinned Intel Codex runtime       |
| Linux x64   | `.AppImage`                        | PyInstaller backend + pinned musl Codex runtime          |
| Linux arm64 | explicit architecture build        | PyInstaller backend + pinned musl Codex runtime          |

PyInstaller cannot cross-compile. The build Python must be version 3.11.9 and
must match the target operating system and CPU architecture; the build scripts
enforce all three properties before removing or producing artifacts.

## Local native build

From the repository root, create the locked platform environment, then invoke
the bridge:

```bash
uv sync --locked --python 3.11.9 --extra desktop-core --extra desktop-build
pnpm --dir frontend install --frozen-lockfile

# Run the command matching the current host.
PYTHON_EXE="$PWD/.venv/bin/python" pnpm --dir extras/desktop electron:build:mac
PYTHON_EXE="$PWD/.venv/bin/python" pnpm --dir extras/desktop electron:build:linux
```

Windows uses `PYTHON_EXE=%CD%\.venv\Scripts\python.exe` and
`pnpm --dir extras/desktop electron:build:win`.

The canonical output directory is `frontend/release/`. macOS packages can be
replayed locally with:

```bash
ECHO_MACOS_APP_LAUNCH_SMOKE=1 \
  ./packaging/desktop/verify-packaged-macos.sh \
  "$PWD/frontend/release/mac-arm64/Echo.app"
```

This checks the native architectures, every Codex manifest hash, App Server
startup, backend `/readyz`, first-launch resource materialization, and the
packaged Electron-to-backend path.

## Protected release identities

Production Windows builds use the `windows-code-signing` GitHub environment
and its `WINDOWS_CODE_SIGNING_CERTIFICATE_BASE64` and
`WINDOWS_CODE_SIGNING_CERTIFICATE_PASSWORD` secrets.

Production macOS builds use the `macos-code-signing` GitHub environment and
these secrets:

- `MACOS_CODE_SIGNING_CERTIFICATE_BASE64`
- `MACOS_CODE_SIGNING_CERTIFICATE_PASSWORD`
- `APPLE_NOTARIZATION_API_KEY_BASE64`
- `APPLE_NOTARIZATION_API_KEY_ID`
- `APPLE_NOTARIZATION_API_ISSUER`

The macOS workflow rejects missing identities, requires hardened-runtime
Developer ID signatures for the app, backend, and Codex executable, verifies
Gatekeeper assessment and the stapled notarization ticket, and performs a
packaged first-launch smoke before uploading the DMG.

Tag releases only accept successful Windows, macOS, and Linux build workflows
from the exact tagged commit. The draft GitHub Release contains all three
installers and platform-specific SHA-256 manifests.

## Files

| File                      | Purpose                                                     |
| ------------------------- | ----------------------------------------------------------- |
| `build-backend-*.cjs`     | Produce the native frozen backend for one platform          |
| `assert-build-python.cjs` | Enforce the locked Python version, OS, and architecture     |
| `prepare-codex-*.cjs`     | Materialize and hash the pinned official Codex package      |
| `licenses/`               | Reviewed Codex and native third-party notices               |
| `package.json`            | Compatibility commands that delegate to `frontend/electron` |

`extras/desktop/electron/` is retained only as compatibility source history;
it is not a second shipped desktop shell.
