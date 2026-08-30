"use strict";
// Desktop backend runtime.
//
// Production installers are self-contained: electron-builder copies the
// PyInstaller backend into resources/backend and packaged Electron processes
// may start only that executable.  They never create a venv, consult a system
// `uv`, or download Python dependencies at first launch.
//
// The uv-managed path below is deliberately development-only.  It keeps the
// unpackaged `--smoke-test-backend` workflow useful without becoming a hidden
// network or host-tool fallback in a released installer.

const { spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { app } = require("electron");

const PACKAGED_CODEX_VERSION = "0.149.0";
const IS_WINDOWS = process.platform === "win32";

// Shared provenance of the codex 0.149.0 rust closure — identical across
// platforms because the same crates ship in every native package.
const CODEX_SOURCE_COMMIT = "758ef40f50c1a458425c7cfbf1eb12cbc07af0b0";
const CODEX_CARGO_LOCK_SHA256 =
  "0c32858e9c47d0acf82735c8620c96840a5381152eec63acad15d1acadb9edad";
const RATATUI_VERSION = "0.30.2";
const RATATUI_CRATE_SHA256 =
  "3274ba0a2c5e1bcad2a2005d20f4dc59dad26b2eb0940fb094500dba4099d57d";
const RIPGREP_VERSION = "15.2.0";
const RIPGREP_SOURCE_COMMIT = "e89fff89ac9af12e8d4ce9d5fd07beb408ca730f";
const RIPGREP_CARGO_LOCK_SHA256 =
  "7a7d39cda8a03930e578f1dbb724e055771901842eca239e03b01e19da946a64";

// License texts reviewed once per codex release; shared by every platform.
const SHARED_LICENSE_FILES = [
  {
    relative: "LICENSE",
    expectedSha256:
      "d17f227e4df5da1600391338865ce0f3055211760a36688f816941d58232d8dc",
  },
  {
    relative: "NOTICE",
    expectedSha256:
      "9d71575ecfd9a843fc1677b0efb08053c6ba9fd686a0de1a6f5382fd3c220915",
  },
  {
    relative: "third-party/codex-rust/THIRD_PARTY_LICENSES.md",
    expectedSha256:
      "198967762991bc9c638b7c304a11bf5b564cb1898e48acf35eca793c2b4557ae",
  },
  {
    relative: "third-party/ratatui/LICENSE",
    expectedSha256:
      "50eb43e8d742c9c61a9391e42b2184fce54dbd1893a1bb1c85b8c9ee217ab1f5",
  },
  {
    relative: "third-party/ripgrep/COPYING",
    expectedSha256:
      "01c266bced4a434da0051174d6bee16a4c82cf634e2679b6155d40d75012390f",
  },
  {
    relative: "third-party/ripgrep/THIRD_PARTY_LICENSES.html",
    expectedSha256:
      "d55f9ff28424dafc02ff01c2c054cb6bde273c904d6e13708d4ace1ab27b56a5",
  },
  {
    relative: "third-party/ripgrep/THIRD_PARTY_LICENSES.md",
    expectedSha256:
      "4b0bade6d5a1b64f7300db89b0c5190da9e66adc15298acae4dd2a7697c7549b",
  },
  {
    relative: "third-party/ripgrep/LICENSE-MIT",
    expectedSha256:
      "0f96a83840e146e43c0ec96a22ec1f392e0680e6c1226e6f3ba87e0740af850f",
  },
  {
    relative: "third-party/ripgrep/UNLICENSE",
    expectedSha256:
      "7e12e5df4bae12cb21581ba157ced20e1986a0508dd10d0e8a4ab9a4cf94e85c",
  },
];

// Linux vendor tree files shared by both linux profiles. Mirrors the darwin
// layout and additionally bundles bwrap (bubblewrap), the sandbox helper used
// on Linux instead of the Windows sandbox artifacts.
const LINUX_VENDOR_FILES = [
  { relative: "bin/codex", executable: true },
  { relative: "bin/codex-code-mode-host", executable: true },
  { relative: "codex-path/rg", executable: true },
  { relative: "codex-resources/zsh/bin/zsh", executable: true },
  { relative: "codex-resources/bwrap", executable: true },
  { relative: "codex-package.json" },
  ...SHARED_LICENSE_FILES,
  {
    relative: "third-party/codex-rust/THIRD_PARTY_LICENSES-codex-cli.html",
    expectedSha256:
      "085bfd0627d8011777788beb1c74a7399c5acba157bac3eb766e0562f58a432b",
  },
];

// linux-native are musl ELF executables; magic is the 4-byte ELF header.
const LINUX_ARM64_PROFILE = {
  platformPackage: "@openai/codex-linux-arm64",
  integrity:
    "sha512-fAXPpvIob+11RNZJS9CVVTsKb+V4Hw3woGFPj42D7fU2wBJUKI2jfAc4fLJNtrpwRecLeW601mtkMHOSIbWuuA==",
  target: "aarch64-unknown-linux-musl",
  fileHashPhase: "pre-package",
  executableName: "codex",
  executableMagic: "7f454c46", // first 4 bytes hex, ELF 64-bit
  files: LINUX_VENDOR_FILES,
};

const LINUX_X64_PROFILE = {
  platformPackage: "@openai/codex-linux-x64",
  integrity:
    "sha512-uZXaN9JPxu0/jjnqqJeTd4kRYPnjVZK3MiVndfG1mHhEaoDKL7ScWHfPqvAEOjwsSDEmQSlMfUkmvYp/CHciYw==",
  target: "x86_64-unknown-linux-musl",
  fileHashPhase: "pre-package",
  executableName: "codex",
  executableMagic: "7f454c46", // first 4 bytes hex, ELF 64-bit
  files: LINUX_VENDOR_FILES,
};

// Platform profile: which native package is pinned, where its executable
// lives, how the manifest was produced, and how executables are identified.
// Profiles are keyed by `platform/arch`; linux mirrors the darwin layout plus
// the bwrap (bubblewrap) sandbox helper, and its executables are ELF.
const CODEX_PROFILE = IS_WINDOWS
  ? {
      platformPackage: "@openai/codex-win32-x64",
      integrity:
        "sha512-qKbwSOOO/fdhQ5MlXE2fts6taPxRPZ/zqeC+eqHD72hLRymV9rFCUbUxOCquognUPRPvS/2/kRCV0UVhoDd3yQ==",
      target: "x86_64-pc-windows-msvc",
      fileHashPhase: "pre-authenticode",
      executableName: "codex.exe",
      executableMagic: "4d5a", // first 2 bytes hex, Windows PE ("MZ")
      files: [
        { relative: "bin/codex.exe", executable: true },
        { relative: "bin/codex-code-mode-host.exe", executable: true },
        {
          relative: "codex-resources/codex-command-runner.exe",
          executable: true,
        },
        {
          relative: "codex-resources/codex-windows-sandbox-setup.exe",
          executable: true,
        },
        { relative: "codex-path/rg.exe", executable: true },
        { relative: "codex-package.json" },
        ...SHARED_LICENSE_FILES,
        {
          relative:
            "third-party/codex-rust/THIRD_PARTY_LICENSES-codex-cli.html",
          expectedSha256:
            "085bfd0627d8011777788beb1c74a7399c5acba157bac3eb766e0562f58a432b",
        },
        {
          relative:
            "third-party/codex-rust/THIRD_PARTY_LICENSES-code-mode-host.html",
          expectedSha256:
            "df6e9546efb4f6a30f06cc7417bb81beeee81bc2e9ea5c670cdfd04a2e9a1503",
        },
        {
          relative:
            "third-party/codex-rust/THIRD_PARTY_LICENSES-windows-sandbox.html",
          expectedSha256:
            "df32d1e635d49d3b86caa4b56e0015dcf116025792ef3c0957dec342a1909721",
        },
        {
          relative: "third-party/codex-native/NATIVE_PROVENANCE.json",
          expectedSha256:
            "65e2c0c7f7b239ee758133ce17cfb680bc38aec84876ca81015458c41a988c7a",
        },
        {
          relative: "third-party/codex-native/NATIVE_THIRD_PARTY_NOTICES.md",
          expectedSha256:
            "da7997facd0e36f4ebca01594c60abdc1204f5421a35d28c4760b13addf247c5",
        },
      ],
    }
  : process.platform === "linux"
    ? process.arch === "arm64"
      ? LINUX_ARM64_PROFILE
      : LINUX_X64_PROFILE
    : process.arch === "arm64"
      ? {
          platformPackage: "@openai/codex-darwin-arm64",
          integrity:
            "sha512-GsZJbzBWiD48RETrO8VHGAQNgfSrUVxItXZFeD87wswatPi0+lKuQo8Dx4nMYmOZhZrVtwr3al/feRrZxnDV8Q==",
          target: "aarch64-apple-darwin",
          fileHashPhase: "pre-codesign",
          executableName: "codex",
          executableMagic: "cffaedfe", // first 4 bytes hex, Mach-O 64-bit LE
          files: [
            { relative: "bin/codex", executable: true },
            { relative: "bin/codex-code-mode-host", executable: true },
            { relative: "codex-path/rg", executable: true },
            { relative: "codex-resources/zsh/bin/zsh", executable: true },
            { relative: "codex-package.json" },
            ...SHARED_LICENSE_FILES,
            {
              relative:
                "third-party/codex-rust/THIRD_PARTY_LICENSES-codex-cli.html",
              expectedSha256:
                "085bfd0627d8011777788beb1c74a7399c5acba157bac3eb766e0562f58a432b",
            },
          ],
        }
      : {
          platformPackage: "@openai/codex-darwin-x64",
          integrity:
            "sha512-H+mMgW3Nhc5QzGWEklCoFqACuOc0cVpgPkPQRw0LShoK7P5664T6BRnyl1yzT6orKPKv49cXry7DIWWZ19SanQ==",
          target: "x86_64-apple-darwin",
          fileHashPhase: "pre-codesign",
          executableName: "codex",
          executableMagic: "cffaedfe", // first 4 bytes hex, Mach-O 64-bit LE
          files: [
            { relative: "bin/codex", executable: true },
            { relative: "bin/codex-code-mode-host", executable: true },
            { relative: "codex-path/rg", executable: true },
            { relative: "codex-resources/zsh/bin/zsh", executable: true },
            { relative: "codex-package.json" },
            ...SHARED_LICENSE_FILES,
            {
              relative:
                "third-party/codex-rust/THIRD_PARTY_LICENSES-codex-cli.html",
              expectedSha256:
                "085bfd0627d8011777788beb1c74a7399c5acba157bac3eb766e0562f58a432b",
            },
          ],
        };
const PACKAGED_CODEX_TARGET = CODEX_PROFILE.target;
const PACKAGED_CODEX_INTEGRITY = CODEX_PROFILE.integrity;
const PACKAGED_CODEX_FILES = CODEX_PROFILE.files;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;

// Development layout roots the uv-managed venv under userData/backend. A test
// can override it to reuse an existing venv — e.g. the Playwright smoke reuses
// the checkout's own .venv so the spawn path is exercised without a download.
const backendRoot = () =>
  process.env.ECHO_DESKTOP_BACKEND_ROOT ||
  path.join(app.getPath("userData"), "backend");
const resourcesPath = () => process.resourcesPath;

// The port must match what main.cjs advertises to the renderer via
// ECHO_BACKEND_URL; derive it from that same env when present.
function backendPort() {
  const m = (process.env.ECHO_BACKEND_URL || "").match(/:(\d+)$/);
  return m ? m[1] : "8000";
}

function pythonExe() {
  return process.platform === "win32"
    ? path.join(backendRoot(), ".venv", "Scripts", "python.exe")
    : path.join(backendRoot(), ".venv", "bin", "python");
}

function packagedBackendExecutable() {
  const exe =
    process.platform === "win32" ? "echo-backend.exe" : "echo-backend";
  return path.join(resourcesPath(), "backend", exe);
}

function requirePackagedBackendExecutable() {
  const executable = packagedBackendExecutable();
  let info;
  try {
    info = fs.statSync(executable);
  } catch {
    throw new Error(
      `packaged backend executable is missing: ${executable}; refusing system/runtime fallback`,
    );
  }
  if (!info.isFile()) {
    throw new Error(`packaged backend path is not a file: ${executable}`);
  }
  return executable;
}

function packagedCodexExecutable() {
  return path.join(
    resourcesPath(),
    "codex",
    "bin",
    CODEX_PROFILE.executableName,
  );
}

function sha256File(file) {
  return crypto
    .createHash("sha256")
    .update(fs.readFileSync(file))
    .digest("hex");
}

function pathIsInside(root, candidate) {
  const relative = path.relative(root, candidate);
  return (
    relative !== "" &&
    relative !== ".." &&
    !relative.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(relative)
  );
}

function requireRegularFileInside(root, realRoot, relative) {
  const candidate = path.resolve(root, relative);
  if (!pathIsInside(root, candidate)) {
    throw new Error(
      `packaged Codex bundle path escapes its resource root: ${relative}`,
    );
  }
  let info;
  try {
    info = fs.lstatSync(candidate);
  } catch {
    throw new Error(
      `packaged Codex bundle file is missing: ${relative}; refusing PATH/network fallback`,
    );
  }
  if (!info.isFile() || info.isSymbolicLink()) {
    throw new Error(
      `packaged Codex bundle path is not a regular file: ${relative}`,
    );
  }
  const realCandidate = fs.realpathSync.native(candidate);
  if (!pathIsInside(realRoot, realCandidate)) {
    throw new Error(
      `packaged Codex bundle file escapes its resource root: ${relative}`,
    );
  }
  return candidate;
}

function requirePackagedCodexBundle() {
  const executable = packagedCodexExecutable();
  let info;
  try {
    info = fs.lstatSync(executable);
  } catch {
    throw new Error(
      `packaged Codex executable is missing: ${executable}; refusing PATH/network fallback`,
    );
  }
  if (!info.isFile() || info.isSymbolicLink()) {
    throw new Error(`packaged Codex path is not a file: ${executable}`);
  }

  const root = path.resolve(resourcesPath(), "codex");
  const rootInfo = fs.lstatSync(root);
  if (!rootInfo.isDirectory() || rootInfo.isSymbolicLink()) {
    throw new Error(
      `packaged Codex resource root is not a regular directory: ${root}`,
    );
  }
  const realRoot = fs.realpathSync.native(root);
  const manifestPath = requireRegularFileInside(
    root,
    realRoot,
    "echo-codex-bundle.json",
  );
  const manifestInfo = fs.statSync(manifestPath);
  if (manifestInfo.size <= 0 || manifestInfo.size > 1024 * 1024) {
    throw new Error("packaged Codex bundle manifest has an invalid size");
  }

  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  } catch {
    throw new Error("packaged Codex bundle manifest is not valid JSON");
  }
  if (
    manifest?.schema !== "echo.codex_bundle.v1" ||
    manifest?.package !== "@openai/codex" ||
    manifest?.version !== PACKAGED_CODEX_VERSION ||
    manifest?.platformPackage !== CODEX_PROFILE.platformPackage ||
    manifest?.platformPackageIntegrity !== PACKAGED_CODEX_INTEGRITY ||
    manifest?.target !== PACKAGED_CODEX_TARGET ||
    manifest?.fileHashPhase !== CODEX_PROFILE.fileHashPhase ||
    manifest?.licenses?.codex?.version !== PACKAGED_CODEX_VERSION ||
    manifest?.licenses?.codex?.sourceTag !==
      `rust-v${PACKAGED_CODEX_VERSION}` ||
    manifest?.licenses?.codex?.sourceCommit !== CODEX_SOURCE_COMMIT ||
    manifest?.licenses?.codex?.cargoLockSha256 !== CODEX_CARGO_LOCK_SHA256 ||
    manifest?.licenses?.codex?.cargoAboutVersion !== "0.9.2" ||
    manifest?.licenses?.ratatui?.version !== RATATUI_VERSION ||
    manifest?.licenses?.ratatui?.crateSha256 !== RATATUI_CRATE_SHA256 ||
    manifest?.licenses?.ripgrep?.version !== RIPGREP_VERSION ||
    manifest?.licenses?.ripgrep?.sourceTag !== RIPGREP_VERSION ||
    manifest?.licenses?.ripgrep?.sourceCommit !== RIPGREP_SOURCE_COMMIT ||
    manifest?.licenses?.ripgrep?.cargoLockSha256 !==
      RIPGREP_CARGO_LOCK_SHA256 ||
    manifest?.licenses?.ripgrep?.cargoAboutVersion !== "0.9.2" ||
    JSON.stringify(manifest?.licenses?.ripgrep?.releaseFeatures) !==
      '["pcre2"]' ||
    !manifest.files ||
    typeof manifest.files !== "object"
  ) {
    throw new Error(
      "packaged Codex bundle manifest does not match the pinned runtime",
    );
  }
  // Windows-only closure artifacts: the native provenance documents the
  // reviewed win32 component list, and ripgrep's prebuilt Windows archive is
  // hashed to the exact reviewed binary. The darwin bundle carries neither —
  // its rg binary is covered by the manifest file map below.
  if (IS_WINDOWS) {
    if (
      manifest?.licenses?.native?.schemaVersion !== "codex-native-notices.v1" ||
      manifest?.licenses?.native?.provenanceSha256 !==
        "65e2c0c7f7b239ee758133ce17cfb680bc38aec84876ca81015458c41a988c7a" ||
      manifest?.licenses?.native?.noticeSha256 !==
        "da7997facd0e36f4ebca01594c60abdc1204f5421a35d28c4760b13addf247c5" ||
      manifest?.licenses?.native?.componentCount !== 12 ||
      manifest?.licenses?.native?.licenseInputCount !== 80 ||
      manifest?.licenses?.ripgrep?.windowsArchiveSha256 !==
        "71b2fef860abe467217a538ff31de02f5258807c0129f771846f87bd029aafc5" ||
      manifest?.licenses?.ripgrep?.windowsExecutableSha256 !==
        "14231169855ec5205cf5a1b6f1db358ff4aed4247c86b69ce8aae647c77f6680"
    ) {
      throw new Error(
        "packaged Codex bundle manifest does not match the pinned runtime",
      );
    }
  }

  for (const required of PACKAGED_CODEX_FILES) {
    const file = requireRegularFileInside(root, realRoot, required.relative);
    const sourceHash = manifest.files[required.relative];
    if (typeof sourceHash !== "string" || !SHA256_PATTERN.test(sourceHash)) {
      throw new Error(
        `packaged Codex manifest is missing a source SHA-256: ${required.relative}`,
      );
    }
    if (required.executable) {
      const magicBytes = CODEX_PROFILE.executableMagic.length / 2;
      const descriptor = fs.openSync(file, "r");
      try {
        const header = Buffer.alloc(magicBytes);
        if (
          fs.readSync(descriptor, header, 0, magicBytes, 0) !== magicBytes ||
          header.toString("hex") !== CODEX_PROFILE.executableMagic
        ) {
          throw new Error(
            `packaged Codex executable is not a ${CODEX_PROFILE.target} binary: ${required.relative}`,
          );
        }
      } finally {
        fs.closeSync(descriptor);
      }
      // electron-builder adds Authenticode after this manifest is generated,
      // so the source-package digest cannot match the signed PE. CI verifies
      // every post-signing PE digest, publisher, and timestamp separately.
      // macOS builds may be re-signed/ad-hoc signed during packaging, so
      // executable digests are excluded from hash comparison there too.
      continue;
    }
    const actualHash = sha256File(file);
    const expectedHash = required.expectedSha256 || sourceHash;
    if (sourceHash !== expectedHash || actualHash !== expectedHash) {
      throw new Error(
        `packaged Codex bundle hash mismatch: ${required.relative}`,
      );
    }
  }

  return executable;
}

function requirePackagedCodexExecutable() {
  return requirePackagedCodexBundle();
}

function developmentUvCmd() {
  if (app.isPackaged) {
    throw new Error("packaged desktop builds must not invoke uv");
  }
  return process.env.ECHO_DESKTOP_DEV_UV || "uv";
}

// Lean core deps for the development-only uv smoke runtime. Keep in sync with
// the `desktop-core` extra in pyproject.toml; released installers use the
// PyInstaller dependency graph instead.
const CORE_DEPS = [
  // Clip Studio is a shipped desktop surface. Its module imports the local
  // frame renderer and MP4 exporter at registration time, so these are core
  // release dependencies rather than first-use downloads.
  "av>=12.0",
  "bcrypt>=4.0",
  "cryptography>=50.0.0",
  "fastapi>=0.115,<1.0",
  "starlette>=1.3.1",
  "uvicorn[standard]>=0.32",
  "pyyaml>=6.0",
  "python-multipart>=0.0.31",
  "httpx>=0.27",
  "numpy>=2.0",
  "python-dotenv>=1.0",
  "ddgs>=9.0",
  "pydantic>=2.12.0",
  "packaging>=24.0",
  // anthropic is required at boot: the default desktop config routes the
  // planner through AnthropicModelRouter, which is constructed eagerly in
  // builder._build_planner. Small pure-Python SDK (~2-3MB), so it lives in
  // the core set instead of the lazy extras.
  "anthropic>=0.40,<1.0",
  "python-docx>=1.2.0",
  "openpyxl>=3.1.5",
  "python-pptx>=1.0.2",
  "pypdf>=6.15.0",
  "reportlab>=4.4.4",
];

// Heavy optional capability → pyproject extra. Installed lazily on first use.
const OPTIONAL_GROUPS = {
  browser: ["playwright>=1.48"],
  desktop: [
    "pyautogui>=0.9.54",
    "pillow>=10.0",
    "uiautomation>=2.0; platform_system == 'Windows'",
  ],
  "code-intel": [
    "tree-sitter>=0.23",
    "tree-sitter-python>=0.23",
    "tree-sitter-typescript>=0.23",
  ],
  vision: [
    "fastembed>=0.8.0",
    "insightface>=1.0.1",
    "opencv-python-headless>=5.0.0.93",
    "rapidocr-onnxruntime>=1.3.0",
  ],
  extract: ["trafilatura>=2.0", "pypdf>=6.15.0"],
  office: [
    "python-docx>=1.2.0",
    "openpyxl>=3.1.5",
    "python-pptx>=1.0.2",
    "pypdf>=6.15.0",
    "reportlab>=4.4.4",
  ],
  mcp: [
    "mcp>=2.0.0,<3.0",
    "pydantic-settings>=2.14.2",
    "pyjwt[crypto]>=2.13.0",
  ],
};

function venvReady() {
  return fs.existsSync(pythonExe());
}

// Spawn a process, forward output, resolve on successful exit.
function runProcess(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, {
      stdio: opts.stream ? "inherit" : ["ignore", "inherit", "inherit"],
      env: { ...process.env, ...opts.env },
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${cmd} exited with code ${code}`));
    });
  });
}

// Create the venv + install the core deps if missing. Runs once on first
// launch and downloads the Python interpreter + a lean dep set.
async function bootstrapCore(onProgress) {
  if (app.isPackaged) {
    throw new Error(
      "packaged desktop builds use the bundled backend and cannot bootstrap dependencies",
    );
  }
  if (venvReady()) return;
  onProgress?.({ stage: "venv", message: "首次启动：创建后端虚拟环境…" });
  await runProcess(developmentUvCmd(), [
    "venv",
    path.join(backendRoot(), ".venv"),
  ]);
  onProgress?.({
    stage: "deps",
    message: "安装核心依赖（仅首次，约几百 MB）…",
  });
  await runProcess(developmentUvCmd(), [
    "pip",
    "install",
    "--python",
    pythonExe(),
    ...CORE_DEPS,
  ]);
}

// Lazily install a heavy optional capability group on first use.
async function ensureOptionalDeps(group, onProgress) {
  if (app.isPackaged) {
    throw new Error(
      "released desktop capabilities are fixed at build time; rebuild the signed installer with the required optional dependency",
    );
  }
  const pkgs = OPTIONAL_GROUPS[group];
  if (!pkgs) throw new Error(`unknown optional group: ${group}`);
  onProgress?.({ stage: "optional", message: `安装 ${group} 能力…` });
  await runProcess(developmentUvCmd(), [
    "pip",
    "install",
    "--python",
    pythonExe(),
    ...pkgs,
  ]);
}

let backendChild = null;

// Start a fixed bundled executable in packaged mode.  The unpackaged smoke
// path may use the development venv, but there is intentionally no packaged
// fallback to Python, uv, PATH, or the network.
async function spawnBackend(configPath, onProgress) {
  if (backendChild) return backendChild;
  const packaged = Boolean(app.isPackaged);
  if (!packaged) await bootstrapCore(onProgress);
  const env = {
    ...process.env,
    ECHO_DESKTOP: "1",
    ECHO_DATA_DIR: path.join(app.getPath("userData"), "data"),
    ECHO_RESOURCES_DIR: path.join(app.getPath("userData"), "resources"),
    ECHO_PACKAGED_CODEX_VERSION: PACKAGED_CODEX_VERSION,
  };
  if (packaged) {
    env.ECHO_BROWSER_EXTENSION_DIR = path.join(
      resourcesPath(),
      "extensions",
      "echo-browser-relay",
    );
  }
  if (!packaged) env.PYTHONPATH = resourcesPath();
  fs.mkdirSync(env.ECHO_DATA_DIR, { recursive: true, mode: 0o700 });
  const executable = packaged
    ? requirePackagedBackendExecutable()
    : pythonExe();
  if (packaged) {
    // This absolute, verified resource path is the only Codex executable the
    // packaged backend may resolve. Never inherit a host PATH installation.
    env.ECHO_CODEX_EXECUTABLE = requirePackagedCodexExecutable();
  }
  const args = packaged
    ? [
        "serve",
        "--config",
        configPath,
        "--host",
        "127.0.0.1",
        "--port",
        backendPort(),
      ]
    : [
        "-m",
        "runtime",
        "serve",
        "--config",
        configPath,
        "--host",
        "127.0.0.1",
        "--port",
        backendPort(),
      ];
  const child = spawn(executable, args, {
    stdio: "inherit",
    env,
    windowsHide: true,
  });
  backendChild = child;
  child.on("exit", (code, signal) => {
    console.warn(
      `[echo] backend exited (code=${code}, signal=${signal}); restart via backend.restart`,
    );
    if (backendChild === child) backendChild = null;
  });
  child.on("error", (err) => {
    console.warn("[echo] backend spawn failed:", err.message);
    if (backendChild === child) backendChild = null;
  });
  return child;
}

async function killBackend() {
  if (!backendChild) return;
  const child = backendChild;
  backendChild = null;
  await new Promise((resolve) => {
    let settled = false;
    let forceTimer = null;
    let hardTimer = null;
    const finish = () => {
      if (settled) return;
      settled = true;
      if (forceTimer) clearTimeout(forceTimer);
      if (hardTimer) clearTimeout(hardTimer);
      resolve();
    };
    child.once("exit", finish);
    child.once("error", finish);
    try {
      child.kill();
    } catch (err) {
      console.warn("[echo] backend kill failed:", err.message);
      finish();
      return;
    }
    forceTimer = setTimeout(() => {
      try {
        child.kill("SIGKILL");
      } catch (err) {
        console.warn("[echo] backend force-kill failed:", err.message);
      }
    }, 5000);
    hardTimer = setTimeout(finish, 7000);
    forceTimer.unref?.();
    hardTimer.unref?.();
  });
}

module.exports = {
  spawnBackend,
  killBackend,
  ensureOptionalDeps,
  bootstrapCore,
  venvReady,
  pythonExe,
  packagedBackendExecutable,
  requirePackagedBackendExecutable,
  packagedCodexExecutable,
  requirePackagedCodexBundle,
  requirePackagedCodexExecutable,
};
