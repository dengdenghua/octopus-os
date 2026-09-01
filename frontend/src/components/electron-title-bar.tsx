import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

const ELECTRON_TITLE_BAR_HEIGHT = 36;
// macOS hiddenInset traffic lights: three 12px buttons with 8px gaps = ~52px wide,
// positioned at x:12, y:10 by default. Leave 60px to comfortably avoid overlap.
const MAC_TRAFFIC_LIGHTS_WIDTH_WINDOWED = 60;
// Fullscreen: traffic lights are hidden until top-of-screen hover — no left padding needed.
const MAC_TRAFFIC_LIGHTS_WIDTH_FULLSCREEN = 0;

const inElectron = (): boolean =>
  typeof window !== "undefined" && !!window.echo?.isElectron;

const isMac = (): boolean =>
  typeof navigator !== "undefined" &&
  (/Mac|iPod|iPhone|iPad/.test(navigator.platform) ||
    navigator.userAgent.includes("Mac"));

const isWindows = (): boolean =>
  typeof navigator !== "undefined" && navigator.userAgent.includes("Windows");

function useTitleBarThemeSync() {
  useEffect(() => {
    if (!isWindows() || !window.echo) {
      return;
    }
    const apply = () => {
      const root = document.documentElement;
      const cs = getComputedStyle(root);
      const bg = cs.getPropertyValue("--background").trim();
      const fg = cs.getPropertyValue("--foreground").trim();
      const wrap = (v: string, fb: string) => {
        if (!v) return fb;
        if (v.startsWith("#") || v.startsWith("rgb") || v.startsWith("oklch"))
          return v;
        return `hsl(${v})`;
      };
      void window
        .echo!.window.setTitleBarOverlay({
          color: wrap(bg, "#fcfcfd"),
          symbolColor: wrap(fg, "#525252"),
        })
        .catch(() => {});
    };
    apply();
    const obs = new MutationObserver(apply);
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "class"],
    });
    return () => obs.disconnect();
  }, []);
}

interface ElectronTitleBarContextValue {
  fullScreen: boolean;
  macTrafficLightsWidth: number;
}

const ElectronTitleBarContext = createContext<ElectronTitleBarContextValue>({
  fullScreen: false,
  macTrafficLightsWidth: 0,
});

export function ElectronTitleBarProvider({
  children,
}: {
  children: ReactNode;
}) {
  const [fullScreen, setFullScreen] = useState(false);

  useEffect(() => {
    if (!inElectron() || !window.echo) return;

    // Initial state
    window.echo.window
      .isFullScreen()
      .then((res: { ok: boolean; fullScreen?: boolean }) => {
        if (res.ok) setFullScreen(!!res.fullScreen);
      })
      .catch(() => {});

    // Listen for changes
    const off = window.echo.on(
      "window:fullscreen-changed",
      (...args: unknown[]) => {
        const payload = args[0] as { fullScreen?: boolean } | undefined;
        setFullScreen(!!payload?.fullScreen);
      },
    );
    return off;
  }, []);

  const macTrafficLightsWidth =
    isMac() && inElectron() && !fullScreen
      ? MAC_TRAFFIC_LIGHTS_WIDTH_WINDOWED
      : MAC_TRAFFIC_LIGHTS_WIDTH_FULLSCREEN;

  return (
    <ElectronTitleBarContext.Provider
      value={{ fullScreen, macTrafficLightsWidth }}
    >
      {children}
    </ElectronTitleBarContext.Provider>
  );
}

export function useElectronTitleBar() {
  return useContext(ElectronTitleBarContext);
}

export function ElectronTitleBar() {
  const electron = inElectron();
  useTitleBarThemeSync();

  if (!electron) return null;

  return (
    <>
      {/* Global drag region - covers full width of the window top */}
      <div
        aria-hidden
        className="pointer-events-none fixed left-0 right-0 top-0 z-[60] h-9"
        style={{ WebkitAppRegion: "drag" } as React.CSSProperties}
      />
    </>
  );
}

export {
  inElectron,
  isMac,
  isWindows,
  ELECTRON_TITLE_BAR_HEIGHT,
  MAC_TRAFFIC_LIGHTS_WIDTH_WINDOWED,
};
