# OpenAI Codex 0.149.0 third-party licenses

The Windows x64 desktop bundle contains four Codex executables represented by
three Rust package/component roots built from the OpenAI Codex `rust-v0.149.0`
source tag. Their normal-runtime license closures are recorded in the
component-specific HTML files beside this note:

- `THIRD_PARTY_LICENSES-codex-cli.html`
- `THIRD_PARTY_LICENSES-code-mode-host.html`
- `THIRD_PARTY_LICENSES-windows-sandbox.html`

The source tag resolves to commit
`758ef40f50c1a458425c7cfbf1eb12cbc07af0b0`. Its original `codex-rs/Cargo.lock`
has SHA-256
`0c32858e9c47d0acf82735c8620c96840a5381152eec63acad15d1acadb9edad`.
The reports were generated for `x86_64-pc-windows-msvc` with
`cargo-about 0.9.2`, the checked-in `cargo-about.toml`, and the checked-in
template.

That tagged source declares workspace version `0.149.0` while its locked local
workspace packages remain at `0.0.0`. The reproducible generator creates an
exact archive of the pinned commit and changes only the temporary
`[workspace.package]` version to `0.0.0`, allowing Cargo to honor the original
lock file with `--locked`. It verifies the lock hash before and after every
component report. No shipped source or dependency version is changed.

The bundled `rg.exe` is distributed separately by ripgrep and its license texts
are provided in the sibling `ripgrep` directory.
