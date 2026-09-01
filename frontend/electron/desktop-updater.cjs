"use strict";

const DEFAULT_INITIAL_DELAY_MS = 30000;
const DEFAULT_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;
const SUPPORTED_PLATFORMS = new Set(["darwin", "win32", "linux"]);

function shouldEnableDesktopUpdater({
  isPackaged,
  nativeShell,
  smoke,
  disabled,
  platform = process.platform,
  isAppImage = Boolean(process.env.APPIMAGE),
}) {
  if (!isPackaged || nativeShell || smoke || disabled) return false;
  if (!SUPPORTED_PLATFORMS.has(platform)) return false;
  return platform !== "linux" || isAppImage;
}

function boundedErrorMessage(error) {
  return String(error?.message || error || "unknown error")
    .replace(/[\r\n\t\0]/g, " ")
    .slice(0, 500);
}

function boundedVersionLabel(value) {
  const label = String(value || "")
    .replace(/[\r\n\t\0]/g, " ")
    .trim()
    .slice(0, 64);
  return label || "新版本";
}

function configureDesktopUpdater({
  autoUpdater,
  dialog,
  logger = console,
  requestQuitAndInstall = () => autoUpdater.quitAndInstall(false, true),
  initialDelayMilliseconds = DEFAULT_INITIAL_DELAY_MS,
  checkIntervalMilliseconds = DEFAULT_CHECK_INTERVAL_MS,
  setTimeoutImpl = setTimeout,
  clearTimeoutImpl = clearTimeout,
  setIntervalImpl = setInterval,
  clearIntervalImpl = clearInterval,
}) {
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.allowPrerelease = false;
  autoUpdater.allowDowngrade = false;
  autoUpdater.logger = logger;

  let disposed = false;
  let checking = false;
  let promptingForDownload = false;
  let promptingForInstall = false;

  const reportError = (error) => {
    logger.warn(
      `[echo] desktop update unavailable: ${boundedErrorMessage(error)}`,
    );
  };

  const onUpdateAvailable = (info) => {
    if (disposed || promptingForDownload) return;
    promptingForDownload = true;
    void dialog
      .showMessageBox({
        type: "info",
        title: "Echo 更新可用",
        message: `发现 Echo ${boundedVersionLabel(info?.version)}。`,
        detail: "更新包会先完成完整性校验，下载期间可以继续使用 Echo。",
        buttons: ["下载更新", "稍后"],
        defaultId: 0,
        cancelId: 1,
        noLink: true,
      })
      .then((result) => {
        if (!disposed && result.response === 0) {
          return autoUpdater.downloadUpdate();
        }
        return undefined;
      })
      .catch(reportError)
      .finally(() => {
        promptingForDownload = false;
      });
  };

  const onUpdateDownloaded = (info) => {
    if (disposed || promptingForInstall) return;
    promptingForInstall = true;
    void dialog
      .showMessageBox({
        type: "info",
        title: "Echo 更新已就绪",
        message: `Echo ${boundedVersionLabel(info?.version)} 已安全下载。`,
        detail: "立即重启会先正常停止本地 Agent 后端，再完成更新安装。",
        buttons: ["重启并安装", "退出时安装"],
        defaultId: 0,
        cancelId: 1,
        noLink: true,
      })
      .then((result) => {
        if (!disposed && result.response === 0) {
          return requestQuitAndInstall();
        }
        return undefined;
      })
      .catch(reportError)
      .finally(() => {
        promptingForInstall = false;
      });
  };

  autoUpdater.on("error", reportError);
  autoUpdater.on("update-available", onUpdateAvailable);
  autoUpdater.on("update-downloaded", onUpdateDownloaded);

  async function checkNow() {
    if (disposed || checking) return false;
    checking = true;
    try {
      await autoUpdater.checkForUpdates();
      return true;
    } catch (error) {
      reportError(error);
      return false;
    } finally {
      checking = false;
    }
  }

  const initialTimer = setTimeoutImpl(
    () => void checkNow(),
    initialDelayMilliseconds,
  );
  initialTimer?.unref?.();
  const intervalTimer = setIntervalImpl(
    () => void checkNow(),
    checkIntervalMilliseconds,
  );
  intervalTimer?.unref?.();

  return {
    enabled: true,
    checkNow,
    dispose() {
      if (disposed) return;
      disposed = true;
      clearTimeoutImpl(initialTimer);
      clearIntervalImpl(intervalTimer);
      autoUpdater.removeListener("error", reportError);
      autoUpdater.removeListener("update-available", onUpdateAvailable);
      autoUpdater.removeListener("update-downloaded", onUpdateDownloaded);
    },
  };
}

module.exports = {
  DEFAULT_CHECK_INTERVAL_MS,
  DEFAULT_INITIAL_DELAY_MS,
  boundedErrorMessage,
  boundedVersionLabel,
  configureDesktopUpdater,
  shouldEnableDesktopUpdater,
};
