"use strict";

// Materialize the exact Windows Codex native package pinned by frontend's
// pnpm lock into the directory consumed by electron-builder.  This is a build
// step only: released applications never contact npm or consult PATH.

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { createRequire } = require("module");

const repoRoot = path.resolve(__dirname, "..", "..");
const frontendRoot = path.join(repoRoot, "frontend");
const outputRoot = path.join(__dirname, "build", "codex");
const CODEX_VERSION = "0.149.0";
const PLATFORM_PACKAGE = "@openai/codex-win32-x64";
const PLATFORM_PACKAGE_INTEGRITY =
  "sha512-qKbwSOOO/fdhQ5MlXE2fts6taPxRPZ/zqeC+eqHD72hLRymV9rFCUbUxOCquognUPRPvS/2/kRCV0UVhoDd3yQ==";
const TARGET_TRIPLE = "x86_64-pc-windows-msvc";
const CODEX_CARGO_LOCK_SHA256 =
  "0c32858e9c47d0acf82735c8620c96840a5381152eec63acad15d1acadb9edad";
const CODEX_SOURCE_COMMIT = "758ef40f50c1a458425c7cfbf1eb12cbc07af0b0";
const CARGO_ABOUT_VERSION = "0.9.2";
const NATIVE_PROVENANCE_SHA256 =
  "65e2c0c7f7b239ee758133ce17cfb680bc38aec84876ca81015458c41a988c7a";
const NATIVE_NOTICE_SHA256 =
  "da7997facd0e36f4ebca01594c60abdc1204f5421a35d28c4760b13addf247c5";
const NATIVE_COMPONENT_COUNT = 12;
const NATIVE_LICENSE_INPUT_COUNT = 80;
const RATATUI_VERSION = "0.30.2";
const RATATUI_CRATE_SHA256 =
  "3274ba0a2c5e1bcad2a2005d20f4dc59dad26b2eb0940fb094500dba4099d57d";
const RIPGREP_VERSION = "15.2.0";
const RIPGREP_SOURCE_COMMIT = "e89fff89ac9af12e8d4ce9d5fd07beb408ca730f";
const RIPGREP_CARGO_LOCK_SHA256 =
  "7a7d39cda8a03930e578f1dbb724e055771901842eca239e03b01e19da946a64";
const RIPGREP_WINDOWS_ARCHIVE_SHA256 =
  "71b2fef860abe467217a538ff31de02f5258807c0129f771846f87bd029aafc5";
const RIPGREP_WINDOWS_EXE_SHA256 =
  "14231169855ec5205cf5a1b6f1db358ff4aed4247c86b69ce8aae647c77f6680";
const REQUIRED_VENDOR_FILES = [
  "bin/codex.exe",
  "bin/codex-code-mode-host.exe",
  "codex-resources/codex-command-runner.exe",
  "codex-resources/codex-windows-sandbox-setup.exe",
  "codex-path/rg.exe",
  "codex-package.json",
];
const LICENSE_FILES = [
  {
    source: path.join(
      __dirname,
      "licenses",
      `codex-${CODEX_VERSION}`,
      "THIRD_PARTY_LICENSES-codex-cli.html",
    ),
    destination: "third-party/codex-rust/THIRD_PARTY_LICENSES-codex-cli.html",
    sha256: "085bfd0627d8011777788beb1c74a7399c5acba157bac3eb766e0562f58a432b",
  },
  {
    source: path.join(
      __dirname,
      "licenses",
      `codex-${CODEX_VERSION}`,
      "THIRD_PARTY_LICENSES-code-mode-host.html",
    ),
    destination:
      "third-party/codex-rust/THIRD_PARTY_LICENSES-code-mode-host.html",
    sha256: "df6e9546efb4f6a30f06cc7417bb81beeee81bc2e9ea5c670cdfd04a2e9a1503",
  },
  {
    source: path.join(
      __dirname,
      "licenses",
      `codex-${CODEX_VERSION}`,
      "THIRD_PARTY_LICENSES-windows-sandbox.html",
    ),
    destination:
      "third-party/codex-rust/THIRD_PARTY_LICENSES-windows-sandbox.html",
    sha256: "df32d1e635d49d3b86caa4b56e0015dcf116025792ef3c0957dec342a1909721",
  },
  {
    source: path.join(
      __dirname,
      "licenses",
      `codex-${CODEX_VERSION}`,
      "THIRD_PARTY_LICENSES.md",
    ),
    destination: "third-party/codex-rust/README.md",
    sha256: "198967762991bc9c638b7c304a11bf5b564cb1898e48acf35eca793c2b4557ae",
  },
  {
    source: path.join(
      __dirname,
      "licenses",
      `codex-${CODEX_VERSION}`,
      "NATIVE_PROVENANCE.json",
    ),
    destination: "third-party/codex-native/NATIVE_PROVENANCE.json",
    sha256: NATIVE_PROVENANCE_SHA256,
  },
  {
    source: path.join(
      __dirname,
      "licenses",
      `codex-${CODEX_VERSION}`,
      "NATIVE_THIRD_PARTY_NOTICES.md",
    ),
    destination: "third-party/codex-native/NATIVE_THIRD_PARTY_NOTICES.md",
    sha256: NATIVE_NOTICE_SHA256,
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

function resolvePinnedPlatformPackage() {
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

  const frontendRequire = createRequire(frontendPackagePath);
  const lockText = fs.readFileSync(
    path.join(frontendRoot, "pnpm-lock.yaml"),
    "utf8",
  );
  const lockedPlatformPackage = [
    `  '@openai/codex@${CODEX_VERSION}-win32-x64':`,
    `    resolution: {integrity: ${PLATFORM_PACKAGE_INTEGRITY}}`,
  ].join("\n");
  if (!lockText.includes(lockedPlatformPackage)) {
    throw new Error(
      `${PLATFORM_PACKAGE} ${CODEX_VERSION} is not locked to the approved npm integrity`,
    );
  }
  const wrapperPackagePath = frontendRequire.resolve(
    "@openai/codex/package.json",
  );
  const wrapperPackage = JSON.parse(
    fs.readFileSync(wrapperPackagePath, "utf8"),
  );
  if (wrapperPackage.version !== CODEX_VERSION) {
    throw new Error(
      `installed @openai/codex is ${wrapperPackage.version}; expected ${CODEX_VERSION}`,
    );
  }
  const expectedPlatformAlias = `npm:@openai/codex@${CODEX_VERSION}-win32-x64`;
  if (
    wrapperPackage.optionalDependencies?.[PLATFORM_PACKAGE] !==
    expectedPlatformAlias
  ) {
    throw new Error(
      `${PLATFORM_PACKAGE} is not pinned by @openai/codex ${CODEX_VERSION}`,
    );
  }

  const wrapperRequire = createRequire(wrapperPackagePath);
  const platformPackagePath = wrapperRequire.resolve(
    `${PLATFORM_PACKAGE}/package.json`,
  );
  const platformPackage = JSON.parse(
    fs.readFileSync(platformPackagePath, "utf8"),
  );
  if (platformPackage.version !== `${CODEX_VERSION}-win32-x64`) {
    throw new Error(
      `installed ${PLATFORM_PACKAGE} is ${platformPackage.version}; expected ${CODEX_VERSION}-win32-x64`,
    );
  }
  return path.join(path.dirname(platformPackagePath), "vendor", TARGET_TRIPLE);
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
  const nativeProvenancePath = path.join(
    __dirname,
    "licenses",
    `codex-${CODEX_VERSION}`,
    "NATIVE_PROVENANCE.json",
  );
  let nativeProvenance;
  try {
    nativeProvenance = JSON.parse(
      fs.readFileSync(nativeProvenancePath, "utf8"),
    );
  } catch {
    throw new Error("Codex native provenance is not valid JSON");
  }
  if (
    nativeProvenance?.schema_version !== "codex-native-notices.v1" ||
    nativeProvenance?.target !== TARGET_TRIPLE ||
    nativeProvenance?.components?.length !== NATIVE_COMPONENT_COUNT ||
    nativeProvenance?.notice_bundle?.license_input_count !==
      NATIVE_LICENSE_INPUT_COUNT ||
    nativeProvenance?.notice_bundle?.sha256 !== NATIVE_NOTICE_SHA256
  ) {
    throw new Error(
      "Codex native provenance does not match the reviewed closure",
    );
  }
  const bundledRipgrep = path.join(sourceRoot, "codex-path", "rg.exe");
  if (sha256(bundledRipgrep) !== RIPGREP_WINDOWS_EXE_SHA256) {
    throw new Error(
      "pinned Codex package does not contain the reviewed ripgrep binary",
    );
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

  const files = {};
  const requiredFiles = [
    ...REQUIRED_VENDOR_FILES,
    ...LICENSE_FILES.map((license) => license.destination),
  ];
  for (const relative of requiredFiles) {
    const copied = path.join(outputRoot, relative);
    const header = fs.readFileSync(copied, { encoding: null }).subarray(0, 2);
    if (relative.endsWith(".exe") && header.toString("ascii") !== "MZ") {
      throw new Error(
        `copied Codex executable is not a Windows PE file: ${relative}`,
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
        // electron-builder signs every copied PE after this manifest is
        // generated. Non-executable hashes remain stable in the package;
        // protected CI emits separate post-signing PE hashes and identities.
        fileHashPhase: "pre-authenticode",
        licenses: {
          codex: {
            version: CODEX_VERSION,
            sourceTag: `rust-v${CODEX_VERSION}`,
            sourceCommit: CODEX_SOURCE_COMMIT,
            cargoLockSha256: CODEX_CARGO_LOCK_SHA256,
            cargoAboutVersion: CARGO_ABOUT_VERSION,
          },
          native: {
            schemaVersion: "codex-native-notices.v1",
            provenanceSha256: NATIVE_PROVENANCE_SHA256,
            noticeSha256: NATIVE_NOTICE_SHA256,
            componentCount: NATIVE_COMPONENT_COUNT,
            licenseInputCount: NATIVE_LICENSE_INPUT_COUNT,
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
            windowsArchiveSha256: RIPGREP_WINDOWS_ARCHIVE_SHA256,
            windowsExecutableSha256: RIPGREP_WINDOWS_EXE_SHA256,
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
    `[codex] prepared @openai/codex ${CODEX_VERSION} at ${outputRoot}`,
  );
}

prepare();

