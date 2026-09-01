"use strict";

// Materialize the packaged YAML template into Electron's per-user data
// directory.  The bundled template must never carry a shared JWT signing key:
// every installation gets a cryptographically random secret before Python is
// allowed to load the config.

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const ACCOUNT_SECRET_MARKER = "__ECHO_DESKTOP_ACCOUNT_JWT_SECRET__";
const LEGACY_LOCAL_AUTH_SECRET_MARKER =
  "__ECHO_DESKTOP_LOCAL_AUTH_JWT_SECRET__";
const LEGACY_WEAK_SECRETS = new Set([
  "echo-desktop-local-jwt-secret-change-me",
  "dev-secret-key-32-chars-minimum-required",
]);
const DESKTOP_RESOURCE_DIRECTORIES = [
  "agents",
  "prompts",
  "protocols",
  "resources",
  "extensions",
  ".echo/plugins",
];
const DESKTOP_RESOURCE_FILES = ["skills.lock.json"];

function generateDesktopJwtSecret() {
  // Prefix guarantees all four character classes required by the Python
  // config validator; randomBytes supplies 384 bits of per-install entropy.
  return `Od9!${crypto.randomBytes(48).toString("base64url")}`;
}

function yamlScalarValue(raw) {
  let value = raw.trim();
  if (value.startsWith('"')) {
    try {
      return JSON.parse(value);
    } catch {
      return value;
    }
  }
  if (value.startsWith("'") && value.endsWith("'")) {
    return value.slice(1, -1).replace(/''/g, "'");
  }
  value = value.replace(/\s+#.*$/, "").trim();
  return value;
}

function secretNeedsRotation(value) {
  if (
    !value ||
    value === ACCOUNT_SECRET_MARKER ||
    value === LEGACY_LOCAL_AUTH_SECRET_MARKER
  )
    return true;
  if (/^\$\{[A-Z_][A-Z0-9_]*\}$/.test(value)) return false;
  if (LEGACY_WEAK_SECRETS.has(value)) return true;
  if (/change[-_ ]?me|replace[-_ ]?me|development[-_ ]?secret/i.test(value)) {
    return true;
  }
  if (value.length < 32) return true;
  const classes = [
    /[a-z]/.test(value),
    /[A-Z]/.test(value),
    /[0-9]/.test(value),
    /[^A-Za-z0-9]/.test(value),
  ].filter(Boolean).length;
  return classes < 3;
}

function configSectionRange(lines, section) {
  const start = lines.findIndex((line) =>
    new RegExp(`^${section}\\s*:\\s*(?:#.*)?$`).test(line),
  );
  if (start < 0) return null;
  let end = lines.length;
  for (let index = start + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (!line.trim() || /^\s*#/.test(line)) continue;
    if (/^[^\s]/.test(line)) {
      end = index;
      break;
    }
  }
  return { start, end };
}

function withAccountSecrets(source, { forceRotate = false } = {}) {
  const newline = source.includes("\r\n") ? "\r\n" : "\n";
  const hadTrailingNewline = source.endsWith("\n");
  const lines = source.split(/\r?\n/);
  if (hadTrailingNewline) lines.pop();
  const sections = ["oct", "local_auth"];
  const targets = sections.map((section) => {
    const range = configSectionRange(lines, section);
    if (!range) return null;
    let secretIndex = -1;
    let currentValue = "";
    for (let index = range.start + 1; index < range.end; index += 1) {
      const match = lines[index].match(/^\s+jwt_secret\s*:\s*(.*)$/);
      if (!match) continue;
      secretIndex = index;
      currentValue = yamlScalarValue(match[1]);
      break;
    }
    return { section, range, secretIndex, currentValue };
  });
  if (!targets.some(Boolean)) {
    throw new Error("desktop config is missing an account auth block");
  }
  const needsChange = targets.some(
    (target) =>
      target &&
      (forceRotate ||
        target.secretIndex < 0 ||
        secretNeedsRotation(target.currentValue)),
  );
  if (!needsChange) return { text: source, changed: false };

  const secret = generateDesktopJwtSecret();
  const replacement = `  jwt_secret: ${JSON.stringify(secret)}`;
  for (const target of targets.filter(Boolean).reverse()) {
    if (target.secretIndex >= 0) lines[target.secretIndex] = replacement;
    else lines.splice(target.range.start + 1, 0, replacement);
  }
  return {
    text: lines.join(newline) + (hadTrailingNewline ? newline : ""),
    changed: true,
  };
}

function writeFileAtomically(targetPath, contents) {
  const directory = path.dirname(targetPath);
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  const temporary = path.join(
    directory,
    `.${path.basename(targetPath)}.${process.pid}.${crypto.randomBytes(8).toString("hex")}.tmp`,
  );
  let descriptor;
  try {
    descriptor = fs.openSync(temporary, "wx", 0o600);
    fs.writeFileSync(descriptor, contents, "utf8");
    fs.fsyncSync(descriptor);
    fs.closeSync(descriptor);
    descriptor = undefined;
    fs.renameSync(temporary, targetPath);
    fs.chmodSync(targetPath, 0o600);
  } finally {
    if (descriptor !== undefined) fs.closeSync(descriptor);
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
  }
}

function ensureDesktopConfigFile({ bundledPath, targetPath }) {
  const targetExists = fs.existsSync(targetPath);
  if (targetExists && fs.lstatSync(targetPath).isSymbolicLink()) {
    throw new Error("refusing to read desktop config through a symbolic link");
  }
  const sourcePath = targetExists ? targetPath : bundledPath;
  if (!fs.existsSync(sourcePath)) {
    throw new Error(`desktop config source is missing: ${sourcePath}`);
  }
  const source = fs.readFileSync(sourcePath, "utf8");
  const materialized = withAccountSecrets(source, {
    // Even if a future template accidentally contains a strong literal,
    // first launch must rotate it so installations never share a key.
    forceRotate: !targetExists,
  });
  if (!targetExists || materialized.changed) {
    writeFileAtomically(targetPath, materialized.text);
  } else {
    fs.chmodSync(targetPath, 0o600);
  }
  return { path: targetPath, changed: !targetExists || materialized.changed };
}

function ensureDesktopResources({ bundledRoot, targetRoot }) {
  if (fs.existsSync(targetRoot) && fs.lstatSync(targetRoot).isSymbolicLink()) {
    throw new Error(
      "refusing to seed desktop resources through a symbolic link",
    );
  }
  fs.mkdirSync(targetRoot, { recursive: true, mode: 0o700 });
  for (const name of DESKTOP_RESOURCE_DIRECTORIES) {
    const source = path.join(bundledRoot, name);
    if (!fs.statSync(source).isDirectory()) {
      throw new Error(`desktop resource directory is missing: ${source}`);
    }
    fs.cpSync(source, path.join(targetRoot, name), {
      recursive: true,
      // userData is the mutable authority after first launch. App updates may
      // add missing packaged resources but must not replace an administrator's
      // installed or edited agent/prompt/protocol at the same path.
      force: false,
      errorOnExist: false,
      filter: (candidate) => {
        if (name !== "agents") return true;
        const relative = path.relative(source, candidate);
        const parts = relative.split(path.sep);
        const basename = path.basename(candidate);
        if (parts.some((part) => ["sessions", "workspace"].includes(part))) {
          return false;
        }
        if (
          relative.includes(
            `${path.sep}visuals${path.sep}backups${path.sep}`,
          ) ||
          relative.includes(
            `${path.sep}agent-core${path.sep}.soul_history${path.sep}`,
          )
        ) {
          return false;
        }
        return !/\.(jsonl|lock|bak)$/.test(basename);
      },
    });
  }
  for (const name of DESKTOP_RESOURCE_FILES) {
    const source = path.join(bundledRoot, name);
    if (!fs.statSync(source).isFile()) {
      throw new Error(`desktop resource file is missing: ${source}`);
    }
    const target = path.join(targetRoot, name);
    if (!fs.existsSync(target)) {
      writeFileAtomically(target, fs.readFileSync(source, "utf8"));
    }
  }
  return { path: targetRoot };
}

module.exports = {
  ACCOUNT_SECRET_MARKER,
  ensureDesktopConfigFile,
  ensureDesktopResources,
  generateDesktopJwtSecret,
  secretNeedsRotation,
  withAccountSecrets,
};
