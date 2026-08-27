/*
 * Echo OS compositor window bridge for KWin 6.
 *
 * KWin retains the actual Window objects. This script exports a sanitized
 * snapshot and executes only three fixed UUID-addressed actions received from
 * the session-private bridge. It never accepts an executable or command text.
 */

const BRIDGE_SERVICE = "org.echoos.WindowBridge";
const BRIDGE_PATH = "/org/echoos/WindowBridge";
const BRIDGE_INTERFACE = "org.echoos.WindowBridge1";
const GENERATION = "kwin-script-v2";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const observedWindows = Object.create(null);
const observedOutputs = Object.create(null);

function canonicalApplicationId(value) {
  const raw = String(value || "")
    .trim()
    .toLowerCase()
    .replace(/\\/g, "/");
  const basename = raw.slice(raw.lastIndexOf("/") + 1);
  return basename.endsWith(".desktop") ? basename.slice(0, -8) : basename;
}

function isEchoShellWindow(window) {
  if (!window) return false;
  const identities = [
    window.desktopFileName,
    window.resourceClass,
    window.resourceName,
  ];
  for (let index = 0; index < identities.length; index += 1) {
    const identity = canonicalApplicationId(identities[index]);
    if (identity === "echo-shell" || identity === "echo-os-desktop") {
      return true;
    }
  }
  return false;
}

function enforceEchoShellLayer(window) {
  if (!isEchoShellWindow(window)) return;
  // Echo owns the desktop surface, not the topmost application layer. These
  // compositor-side properties are authoritative for native Wayland clients;
  // renderer CSS and X11-only EWMH tools cannot enforce them.
  window.keepBelow = true;
  window.skipTaskbar = true;
  window.skipPager = true;
  window.noBorder = true;
  window.onAllDesktops = true;
}

function canonicalWindowId(window) {
  const raw = String(window.internalId || "")
    .trim()
    .replace(/^\{/, "")
    .replace(/\}$/, "")
    .toLowerCase();
  return UUID.test(raw) ? raw : "";
}

function desktopIndex(window) {
  if (window.onAllDesktops) return -1;
  const assigned = window.desktops || [];
  const desktops = workspace.desktops || [];
  if (!assigned.length) return 0;
  for (let index = 0; index < desktops.length; index += 1) {
    if (desktops[index] === assigned[0]) return index;
  }
  return 0;
}

function isTaskWindow(window) {
  return Boolean(
    window &&
    window.normalWindow &&
    !window.skipTaskbar &&
    canonicalWindowId(window),
  );
}

function serializeWindow(window) {
  const desktopFileName = String(window.desktopFileName || "");
  const resourceClass = String(window.resourceClass || "");
  return {
    id: canonicalWindowId(window),
    desktop: desktopIndex(window),
    pid: Math.max(0, Number(window.pid) || 0),
    host: "",
    wmClass: desktopFileName || resourceClass,
    title: String(
      window.caption || desktopFileName || resourceClass || "Application",
    ),
    active: workspace.activeWindow === window,
    minimized: Boolean(window.minimized),
    provider: "kwin-wayland",
  };
}

function serializeOutput(output, index) {
  const geometry = output.geometry || {};
  return {
    name: String(output.name || `output-${index + 1}`),
    x: Math.trunc(Number(geometry.x) || 0),
    y: Math.trunc(Number(geometry.y) || 0),
    width: Math.max(1, Math.trunc(Number(geometry.width) || 1)),
    height: Math.max(1, Math.trunc(Number(geometry.height) || 1)),
    scale: Number(output.devicePixelRatio) || 1,
  };
}

function outputs() {
  const screens = workspace.screens || [];
  const result = [];
  for (let index = 0; index < screens.length; index += 1) {
    observeOutput(screens[index], index);
    result.push(serializeOutput(screens[index], index));
  }
  return result;
}

function taskWindows() {
  const stackingOrder = workspace.stackingOrder || [];
  const windows = [];
  for (let index = 0; index < stackingOrder.length; index += 1) {
    const window = stackingOrder[index];
    if (isTaskWindow(window)) windows.push(serializeWindow(window));
  }
  return windows;
}

function publishState() {
  callDBus(
    BRIDGE_SERVICE,
    BRIDGE_PATH,
    BRIDGE_INTERFACE,
    "PublishState",
    JSON.stringify({ windows: taskWindows(), outputs: outputs() }),
    GENERATION,
  );
}

const publishTimer = new QTimer();
publishTimer.singleShot = true;
publishTimer.interval = 25;
publishTimer.timeout.connect(publishState);

function schedulePublish() {
  publishTimer.start();
}

function connectSignal(signal, callback) {
  if (signal && typeof signal.connect === "function") signal.connect(callback);
}

function observeWindow(window) {
  const id = canonicalWindowId(window);
  if (!id || observedWindows[id]) return;
  observedWindows[id] = true;
  enforceEchoShellLayer(window);
  connectSignal(window.minimizedChanged, schedulePublish);
  connectSignal(window.captionChanged, schedulePublish);
  connectSignal(window.desktopFileNameChanged, () => {
    enforceEchoShellLayer(window);
    schedulePublish();
  });
  connectSignal(window.windowClassChanged, () => {
    enforceEchoShellLayer(window);
    schedulePublish();
  });
  connectSignal(window.skipTaskbarChanged, schedulePublish);
  connectSignal(window.desktopsChanged, schedulePublish);
  connectSignal(window.outputChanged, schedulePublish);
}

function observeOutput(output, index) {
  const key = String(output.name || `output-${index + 1}`);
  if (observedOutputs[key]) return;
  observedOutputs[key] = true;
  connectSignal(output.geometryChanged, schedulePublish);
  connectSignal(output.scaleChanged, schedulePublish);
}

function refreshOutputs() {
  const keys = Object.keys(observedOutputs);
  for (let index = 0; index < keys.length; index += 1) {
    delete observedOutputs[keys[index]];
  }
  outputs();
  schedulePublish();
}

function findWindow(id) {
  const stackingOrder = workspace.stackingOrder || [];
  for (let index = 0; index < stackingOrder.length; index += 1) {
    const window = stackingOrder[index];
    if (canonicalWindowId(window) === id) return window;
  }
  return null;
}

function completeAction(sequence, ok, error) {
  callDBus(
    BRIDGE_SERVICE,
    BRIDGE_PATH,
    BRIDGE_INTERFACE,
    "CompleteAction",
    String(sequence),
    Boolean(ok),
    String(error || ""),
  );
}

function executeAction(request) {
  const sequence = Number(request.sequence);
  const action = String(request.action || "");
  const windowId = String(request.windowId || "").toLowerCase();
  if (!Number.isInteger(sequence) || sequence <= 0 || !UUID.test(windowId)) {
    completeAction(sequence || 0, false, "invalid bridge action");
    return;
  }
  const window = findWindow(windowId);
  if (!window || !isTaskWindow(window)) {
    completeAction(sequence, false, "KWin window no longer exists");
    return;
  }
  try {
    if (action === "focus") {
      window.minimized = false;
      workspace.activeWindow = window;
      workspace.raiseWindow(window);
    } else if (action === "minimize") {
      window.minimized = true;
    } else if (action === "close") {
      window.closeWindow();
    } else {
      completeAction(sequence, false, "unknown bridge action");
      return;
    }
    completeAction(sequence, true, "");
    schedulePublish();
  } catch (error) {
    completeAction(sequence, false, String(error));
  }
}

function receiveActions(payload) {
  try {
    const actions = JSON.parse(String(payload || "[]"));
    if (!Array.isArray(actions) || actions.length > 256) return;
    for (let index = 0; index < actions.length; index += 1) {
      executeAction(actions[index]);
    }
  } catch (_error) {
    // A malformed peer response is ignored; the daemon times the action out.
  }
}

function pollActions() {
  callDBus(
    BRIDGE_SERVICE,
    BRIDGE_PATH,
    BRIDGE_INTERFACE,
    "TakeActions",
    receiveActions,
  );
}

workspace.windowAdded.connect((window) => {
  enforceEchoShellLayer(window);
  observeWindow(window);
  schedulePublish();
});
workspace.windowRemoved.connect((window) => {
  const id = canonicalWindowId(window);
  if (id) delete observedWindows[id];
  schedulePublish();
});
workspace.windowActivated.connect(schedulePublish);
connectSignal(workspace.currentDesktopChanged, schedulePublish);
connectSignal(workspace.screensChanged, refreshOutputs);
connectSignal(workspace.virtualScreenGeometryChanged, schedulePublish);

const initialWindows = workspace.stackingOrder || [];
for (let index = 0; index < initialWindows.length; index += 1) {
  observeWindow(initialWindows[index]);
}

const actionTimer = new QTimer();
actionTimer.interval = 100;
actionTimer.timeout.connect(pollActions);
actionTimer.start();

publishState();
