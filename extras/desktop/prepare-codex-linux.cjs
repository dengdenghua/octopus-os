"use strict";

// Linux mirror of prepare-codex-win.cjs / prepare-codex-mac.cjs. Materializes
// the exact @openai/codex-linux-{x64,arm64} native package pinned by
// frontend's pnpm lock into the directory consumed by electron-builder.
// Build step only: released applications never contact npm or consult PATH.
//
// The linux native package is an optional dependency filtered out by pnpm on
// non-Linux hosts, so the script resolves it from node_modules when present
// and otherwise fetches the registry tarball pinned by the lockfile's
// recorded integrity (verified byte-for-byte before extraction).

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");
const { createRequire } = require("module");

const repoRoot = path.resolve(__dirname, "..", "..");
const frontendRoot = path.join(repoRoot, "frontend");
const outputRoot = path.join(__dirname, "build", "codex");
const downloadRoot = path.join(__dirname, "build", "codex-download");
const CODEX_VERSION = "0.149.0";
// ECHO_LINUX_ARCH selects the target slice; it defaults to the host arch.
const LINUX_ARCH = process.env.ECHO_LINUX_ARCH || process.arch;
// Per-arch profile pinned by frontend's pnpm lock.
const LINUX_PROFILES = {
  x64: {
    platformPackage: "@openai/codex-linux-x64",
    platformPackageVersion: `${CODEX_VERSION}-linux-x64`,
    // pnpm-lock.yaml: '@openai/codex@0.149.0-linux-x64' resolution.integrity
    integrity:
      "sha512-uZXaN9JPxu0/jjnqqJeTd4kRYPnjVZK3MiVndfG1mHhEaoDKL7ScWHfPqvAEOjwsSDEmQSlMfUkmvYp/CHciYw==",
    tarballUrl:
      "https://registry.npmjs.org/@openai/codex/-/codex-0.149.0-linux-x64.tgz",
    targetTriple: "x86_64-unknown-linux-musl",
  },
  arm64: {
    platformPackage: "@openai/codex-linux-arm64",
    platformPackageVersion: `${CODEX_VERSION}-linux-arm64`,
    // pnpm-lock.yaml: '@openai/codex@0.149.0-linux-arm64' resolution.integrity
    integrity:
      "sha512-fAXPpvIob+11RNZJS9CVVTsKb+V4Hw3woGFPj42D7fU2wBJUKI2jfAc4fLJNtrpwRecLeW601mtkMHOSIbWuuA==",
    tarballUrl:
      "https://registry.npmjs.org/@openai/codex/-/codex-0.149.0-linux-arm64.tgz",
    targetTriple: "aarch64-unknown-linux-musl",
  },
};
const PROFILE = LINUX_PROFILES[LINUX_ARCH];
if (!PROFILE) {
  throw new Error(
    `unsupported ECHO_LINUX_ARCH: ${LINUX_ARCH} (expected x64 or arm64)`,
  );
}
const PLATFORM_PACKAGE = PROFILE.platformPackage;
const PLATFORM_PACKAGE_VERSION = PROFILE.platformPackageVersion;
const PLATFORM_PACKAGE_INTEGRITY = PROFILE.integrity;
const TARBALL_URL = PROFILE.tarballUrl;
const TARGET_TRIPLE = PROFILE.targetTriple;
const CODEX_CARGO_LOCK_SHA256 =
  "0c32858e9c47d0acf82735c8620c96840a5381152eec63acad15d1acadb9edad";
const CODEX_SOURCE_COMMIT = "758ef40f50c1a458425c7cfbf1eb12cbc07af0b0";
const CARGO_ABOUT_VERSION = "0.9.2";
const RATATUI_VERSION = "0.30.2";
const RATATUI_CRATE_SHA256 =
  "3274ba0a2c5e1bcad2a2005d20f4dc59dad26b2eb0940fb094500dba4099d57d";
const RIPGREP_VERSION = "15.2.0";
const RIPGREP_SOURCE_COMMIT =
  "e89fff89ac9af12e8d4ce9d5fd07beb408ca730f";
const RIPGREP_CARGO_LOCK_SHA256 =
  "7a7d39cda8a03930e578f1dbb724e055771901842eca239e03b01e19da946a64";
// Linux vendor layout mirrors the darwin package (bwrap bubblewrap sandbox is
// shipped instead of the Windows sandbox helpers).
const REQUIRED_VENDOR_FILES = [
  "bin/codex",
  "bin/codex-code-mode-host",
  "codex-path/rg",
  "codex-resources/zsh/bin/zsh",
  "codex-resources/bwrap",
  "codex-package.json",
];
const EXECUTABLE_FILES = new Set([
  "bin/codex",
  "bin/codex-code-mode-host",
  "codex-path/rg",
  "codex-resources/zsh/bin/zsh",
  "codex-resources/bwrap",
]);
// ELF magic: first 4 bytes of every ELF64 binary are 7f 45 4c 46.
const EXECUTABLE_MAGIC = "7f454c46";
// Platform-neutral license texts reviewed for the Windows/darwin closures; the
// same rust crate versions ship in the linux package, so the texts carry over.
const LICENSE_FILES = [
  {
    source: path.join(
      __dirname,
      "licenses",
      `codex-${CODEX_VERSION}`,
      "THIRD_PARTY_LICENSES-codex-cli.html",
    ),
    destination: "third-party/codex-rust/THIRD_PARTY_LICENSES-codex-cli.html",
    sha256: "841d5072916479fc3d6fbe8c4b240b66d468de9f625a2fcb658c34fe1a4ec771",
  },
  {
    source: path.join(
      __dirname,
      "licenses",
      `codex-${CODEX_VERSION}`,
      "THIRD_PARTY_LICENSES.md",
    ),
    destination: "third-party/codex-rust/THIRD_PARTY_LICENSES.md",
    sha256: "198967762991bc9c638b7c304a11bf5b564cb1898e48acf35eca793c2b4557ae",
  },
  {
    source: path.join(
      __dirname,
      "licenses",
      `ratatui-${RATATUI_VERSION}`,
      "LICENSE",
    ),
    destination: "third-party/ratatui/LICENSE",
    sha256: "50eb43e8d742c9c61a9391e42b2184fce54dbd1893a1bb1c85b8c9ee217ab1f5",
  },
  {
    source: path.join(
      __dirname,
      "licenses",
      `codex-${CODEX_VERSION}`,
      "LICENSE",
    ),
    destination: "LICENSE",
    sha256: "d17f227e4df5da1600391338865ce0f3055211760a36688f816941d58232d8dc",
  },
  {
    source: path.join(
      __dirname,
      "licenses",
      `codex-${CODEX_VERSION}`,
      "NOTICE",
    ),
    destination: "NOTICE",
    sha256: "9d71575ecfd9a843fc1677b0efb08053c6ba9fd686a0de1a6f5382fd3c220915",
  },
  {
    source: path.join(
      __dirname,
      "licenses",
      `ripgrep-${RIPGREP_VERSION}`,
      "THIRD_PARTY_LICENSES-ripgrep.html",
    ),
    destination: "third-party/ripgrep/THIRD_PARTY_LICENSES.html",
    sha256: "d55f9ff28424dafc02ff01c2c054cb6bde273c904d6e13708d4ace1ab27b56a5",
  },
  {
    source: path.join(
      __dirname,
      "licenses",
      `ripgrep-${RIPGREP_VERSION}`,
      "THIRD_PARTY_LICENSES.md",
    ),
    destination: "third-party/ripgrep/THIRD_PARTY_LICENSES.md",
    sha256: "4b0bade6d5a1b64f7300db89b0c5190da9e66adc15298acae4dd2a7697c7549b",
  },
  {
    source: path.join(
      __dirname,
      "licenses",
      `ripgrep-${RIPGREP_VERSION}`,
      "COPYING",
    ),
    destination: "third-party/ripgrep/COPYING",
    sha256: "01c266bced4a434da0051174d6bee16a4c82cf634e2679b6155d40d75012390f",
  },
  {
    source: path.join(
      __dirname,
      "licenses",
      `ripgrep-${RIPGREP_VERSION}`,
      "LICENSE-MIT",
    ),
    destination: "third-party/ripgrep/LICENSE-MIT",
    sha256: "0f96a83840e146e43c0ec96a22ec1f392e0680e6c1226e6f3ba87e0740af850f",
  },
  {
    source: path.join(
      __dirname,
      "licenses",
      `ripgrep-${RIPGREP_VERSION}`,
      "UNLICENSE",
    ),
    destination: "third-party/ripgrep/UNLICENSE",
    sha256: "7e12e5df4bae12cb21581ba157ced20e1986a0508dd10d0e8a4ab9a4cf94e85c",
  },
];

function sha256(file) {
  return crypto
    .createHash("sha256")
    .update(fs.readFileSync(file))
    .digest("hex");
}

function verifyPinnedWrapper() {
  const frontendPackagePath = path.join(frontendRoot, "package.json");
  const frontendPackage = JSON.parse(
    fs.readFileSync(frontendPackagePath, "utf8"),
  );
  const declared = frontendPackage.devDependencies?.["@openai/codex"];
  if (declared !== CODEX_VERSION) {
    throw new Error(
      `frontend must pin @openai/codex exactly to ${CODEX_VERSION}; found ${declared || "missing"}`,
    );
  }
  const lockText = fs.readFileSync(
    path.join(frontendRoot, "pnpm-lock.yaml"),
    "utf8",
  );
  const lockedPlatformPackage = [
    `  '@openai/codex@${CODEX_VERSION}-linux-${LINUX_ARCH}':`,
    `    resolution: {integrity: ${PLATFORM_PACKAGE_INTEGRITY}}`,
  ].join("\n");
  if (!lockText.includes(lockedPlatformPackage)) {
    throw new Error(
      `${PLATFORM_PACKAGE} ${CODEX_VERSION} is not locked to the approved npm integrity`,
    );
  }
}

function resolveFromNodeModules() {
  const frontendRequire = createRequire(
    path.join(frontendRoot, "package.json"),
  );
  const wrapperPackagePath = frontendRequire.resolve(
    "@openai/codex/package.json",
  );
  const wrapperRequire = createRequire(wrapperPackagePath);
  try {
    const platformPackagePath = wrapperRequire.resolve(
      `${PLATFORM_PACKAGE}/package.json`,
    );
    const platformPackage = JSON.parse(
      fs.readFileSync(platformPackagePath, "utf8"),
    );
    if (platformPackage.version !== PLATFORM_PACKAGE_VERSION) {
      throw new Error(
        `installed ${PLATFORM_PACKAGE} is ${platformPackage.version}; expected ${PLATFORM_PACKAGE_VERSION}`,
      );
    }
    return path.join(path.dirname(platformPackagePath), "vendor", TARGET_TRIPLE);
  } catch {
    return null;
  }
}

function resolveFromRegistryTarball() {
  fs.rmSync(downloadRoot, { recursive: true, force: true });
  fs.mkdirSync(downloadRoot, { recursive: true });
  const tarball = path.join(
    downloadRoot,
    `codex-linux-${LINUX_ARCH}.tgz`,
  );
  const curl = spawnSync(
    "curl",
    ["-fsSL", "--output", tarball, TARBALL_URL],
    { stdio: "inherit" },
  );
  if (curl.status !== 0) {
    throw new Error(`failed to download pinned ${PLATFORM_PACKAGE} tarball`);
  }
  const integrity =
    "sha512-" +
    crypto
      .createHash("sha512")
      .update(fs.readFileSync(tarball))
      .digest("base64");
  if (integrity !== PLATFORM_PACKAGE_INTEGRITY) {
    throw new Error(
      `${PLATFORM_PACKAGE} tarball integrity mismatch: ${integrity}`,
    );
  }
  const extract = spawnSync(
    "tar",
    ["-xzf", tarball, "-C", downloadRoot],
    { stdio: "inherit" },
  );
  if (extract.status !== 0) {
    throw new Error(`failed to extract ${PLATFORM_PACKAGE} tarball`);
  }
  return path.join(downloadRoot, "package", "vendor", TARGET_TRIPLE);
}

function resolvePinnedPlatformPackage() {
  verifyPinnedWrapper();
  return resolveFromNodeModules() ?? resolveFromRegistryTarball();
}

function prepare() {
  const sourceRoot = resolvePinnedPlatformPackage();
  for (const relative of REQUIRED_VENDOR_FILES) {
    const source = path.join(sourceRoot, relative);
    if (!fs.statSync(source).isFile()) {
      throw new Error(`pinned Codex package is incomplete: ${relative}`);
    }
  }
  for (const license of LICENSE_FILES) {
    if (!fs.statSync(license.source).isFile()) {
      throw new Error(`bundled license text is missing: ${license.source}`);
    }
    const actual = sha256(license.source);
    if (actual !== license.sha256) {
      throw new Error(
        `bundled license text failed its source hash: ${license.destination}`,
      );
    }
  }

  const expectedBuildRoot = path.join(__dirname, "build") + path.sep;
  if (!outputRoot.startsWith(expectedBuildRoot)) {
    throw new Error(
      `refusing to replace Codex output outside build root: ${outputRoot}`,
    );
  }
  fs.rmSync(outputRoot, { recursive: true, force: true });
  fs.mkdirSync(outputRoot, { recursive: true });
  fs.cpSync(sourceRoot, outputRoot, { recursive: true, errorOnExist: false });
  for (const license of LICENSE_FILES) {
    const destination = path.join(outputRoot, license.destination);
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.copyFileSync(license.source, destination);
  }
  for (const relative of EXECUTABLE_FILES) {
    fs.chmodSync(path.join(outputRoot, relative), 0o755);
  }

  const files = {};
  const requiredFiles = [
    ...REQUIRED_VENDOR_FILES,
    ...LICENSE_FILES.map((license) => license.destination),
  ];
  for (const relative of requiredFiles) {
    const copied = path.join(outputRoot, relative);
    const header = fs.readFileSync(copied, { encoding: null }).subarray(0, 4);
    if (
      EXECUTABLE_FILES.has(relative) &&
      header.toString("hex") !== EXECUTABLE_MAGIC
    ) {
      throw new Error(
        `copied Codex executable is not an ELF binary: ${relative}`,
      );
    }
    files[relative.replaceAll(path.sep, "/")] = sha256(copied);
  }
  fs.writeFileSync(
    path.join(outputRoot, "echo-codex-bundle.json"),
    `${JSON.stringify(
      {
        schema: "echo.codex_bundle.v1",
        package: "@openai/codex",
        version: CODEX_VERSION,
        platformPackage: PLATFORM_PACKAGE,
        platformPackageIntegrity: PLATFORM_PACKAGE_INTEGRITY,
        target: TARGET_TRIPLE,
        // Linux AppImages are not re-signed after this manifest is generated,
        // but executable hashes are still verified separately from license
        // hashes at runtime, mirroring the darwin flow.
        fileHashPhase: "pre-package",
        licenses: {
          codex: {
            version: CODEX_VERSION,
            sourceTag: `rust-v${CODEX_VERSION}`,
            sourceCommit: CODEX_SOURCE_COMMIT,
            cargoLockSha256: CODEX_CARGO_LOCK_SHA256,
            cargoAboutVersion: CARGO_ABOUT_VERSION,
          },
          ratatui: {
            version: RATATUI_VERSION,
            crateSha256: RATATUI_CRATE_SHA256,
          },
          ripgrep: {
            version: RIPGREP_VERSION,
            sourceTag: RIPGREP_VERSION,
            sourceCommit: RIPGREP_SOURCE_COMMIT,
            cargoLockSha256: RIPGREP_CARGO_LOCK_SHA256,
            cargoAboutVersion: CARGO_ABOUT_VERSION,
            releaseFeatures: ["pcre2"],
          },
        },
        files,
      },
      null,
      2,
    )}\n`,
    "utf8",
  );
  console.log(
    `[codex] prepared @openai/codex ${CODEX_VERSION} (${TARGET_TRIPLE}) at ${outputRoot}`,
  );
}

prepare();