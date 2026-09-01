const DEFAULT_DEV_URL = "http://localhost:3000";

function resolveDevURL(environment = process.env) {
  const configured = String(environment.ELECTRON_START_URL || "").trim();
  return configured || DEFAULT_DEV_URL;
}

module.exports = {
  DEFAULT_DEV_URL,
  resolveDevURL,
};
