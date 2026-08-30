import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import {
  BatteryFullIcon,
  BellIcon,
  BluetoothIcon,
  BotIcon,
  CheckCircle2Icon,
  ChevronRightIcon,
  CommandIcon,
  DropletsIcon,
  HardDriveIcon,
  InfoIcon,
  LayoutGridIcon,
  LockIcon,
  Loader2Icon,
  MoonIcon,
  SearchIcon,
  SettingsIcon,
  ShoppingBagIcon,
  SlidersHorizontalIcon,
  RefreshCwIcon,
  SunIcon,
  Volume2Icon,
  WifiIcon,
  XIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { EchoMark } from "@/components/brand/echo-mark";
import type {
  AgentDesktopHealth,
  AgentDesktopHealthState,
} from "@/appliance/agent-health";
import type {
  NativeNotification,
  SystemControlState,
  SystemUpdateCapabilities,
  SystemUpdateStatus,
} from "@/types/electron";
import {
  CLEAR_LIQUID_GLASS_TUNING,
  LIQUID_GLASS_TINTS,
  type LiquidGlassTuning,
} from "@/appliance/liquid-glass-settings";
import "@/styles/macos-desktop.css";

type MacIcon = typeof SearchIcon;

type EchoAppIconPalette = {
  surface: string;
  accent: string;
  glow: string;
};

function echoSolidPalette(
  surface: string,
  accent: string,
  glow: string,
): EchoAppIconPalette {
  return {
    surface,
    accent,
    glow,
  };
}

const ECHO_SIGNAL_GLASS = {
  carbon: echoSolidPalette("#061522", "#b9e6ff", "rgba(60, 170, 255, .52)"),
  cobalt: echoSolidPalette("#087ff5", "#effeff", "rgba(0, 145, 255, .54)"),
  marine: echoSolidPalette("#08a982", "#effff8", "rgba(0, 207, 151, .5)"),
  oxide: echoSolidPalette("#f06431", "#fff7ee", "rgba(255, 102, 42, .5)"),
  sonar: echoSolidPalette("#00a6c7", "#f2ffff", "rgba(0, 194, 229, .52)"),
  orbital: echoSolidPalette("#4867e2", "#f7faff", "rgba(73, 106, 255, .5)"),
  amber: echoSolidPalette("#f2a20d", "#fffdf5", "rgba(255, 174, 20, .5)"),
  command: echoSolidPalette("#3e59d7", "#f5f8ff", "rgba(58, 89, 235, .5)"),
  signal: echoSolidPalette("#06231e", "#79f7c5", "rgba(54, 246, 177, .5)"),
  gunmetal: echoSolidPalette("#58778d", "#ffffff", "rgba(73, 157, 215, .44)"),
  azure: echoSolidPalette("#1594d3", "#f4fdff", "rgba(0, 161, 235, .52)"),
  titanium: echoSolidPalette("#bfd5e4", "#ffffff", "rgba(86, 163, 218, .38)"),
  slate: echoSolidPalette("#5267d3", "#ffffff", "rgba(78, 104, 239, .5)"),
  glacier: echoSolidPalette("#bfd3df", "#ffffff", "rgba(67, 151, 207, .4)"),
} as const;

const ECHO_APP_ICON_PALETTES: Record<string, EchoAppIconPalette> = {
  "echo:/workspace/realtime/new": ECHO_SIGNAL_GLASS.carbon,
  "echo:/browser": ECHO_SIGNAL_GLASS.cobalt,
  "echo:/workspace/storage": ECHO_SIGNAL_GLASS.marine,
  "echo:/photos": ECHO_SIGNAL_GLASS.oxide,
  "echo:/storage-center": ECHO_SIGNAL_GLASS.sonar,
  "echo:/device-link": ECHO_SIGNAL_GLASS.orbital,
  "echo:/workspace/knowledge": ECHO_SIGNAL_GLASS.amber,
  "echo:/workspace/store": ECHO_SIGNAL_GLASS.command,
  "echo:/hub": ECHO_SIGNAL_GLASS.command,
  "echo:/workspace/observability": ECHO_SIGNAL_GLASS.signal,
  "echo:/workspace": ECHO_SIGNAL_GLASS.gunmetal,
  "system:finder": ECHO_SIGNAL_GLASS.azure,
  "system:files": ECHO_SIGNAL_GLASS.azure,
  "system:launchpad": ECHO_SIGNAL_GLASS.titanium,
  "system:app-store": ECHO_SIGNAL_GLASS.command,
  "system:tasks": ECHO_SIGNAL_GLASS.slate,
  "system:activity-monitor": ECHO_SIGNAL_GLASS.signal,
  "system:settings": ECHO_SIGNAL_GLASS.gunmetal,
  "system:trash": ECHO_SIGNAL_GLASS.glacier,
};

const ECHO_TECH_FIELD_APP_IDS = new Set([
  "echo:/storage-center",
  "echo:/workspace/observability",
  "system:activity-monitor",
]);

export type MacShellApp = {
  id: string;
  name: string;
  subtitle?: string;
  icon: MacIcon;
  gradient: string;
  iconUrl?: string;
  running?: boolean;
  muted?: boolean;
  onOpen: () => void;
};

/**
 * Echo artwork for the built-in shell apps.
 *
 * Dynamic apps keep the Lucide fallback, while the high-frequency desktop
 * apps get layered silhouettes and optical highlights so the shell reads as a
 * single original Echo icon family.
 */
function MacAppArtwork({ appId }: { appId?: string }): ReactNode {
  const common = {
    className: "mac-app-icon-art mac-shell-icon-art",
    viewBox: "0 0 64 64",
    fill: "none",
    "aria-hidden": true,
  } as const;

  switch (appId) {
    case "system:finder":
    case "system:files":
      return (
        <svg {...common}>
          <path
            d="M12 23.5a4.5 4.5 0 0 1 4.5-4.5h11l4.5 5h15.5a4.5 4.5 0 0 1 4.5 4.5v17a4.5 4.5 0 0 1-4.5 4.5h-31a4.5 4.5 0 0 1-4.5-4.5v-22Z"
            fill="rgba(245, 252, 255, .5)"
            stroke="rgba(235, 250, 255, .94)"
            strokeLinejoin="round"
            strokeWidth="1.6"
          />
          <path
            d="M12 29h40v16.5a4.5 4.5 0 0 1-4.5 4.5h-31a4.5 4.5 0 0 1-4.5-4.5V29Z"
            fill="rgba(15, 99, 188, .28)"
          />
          <path
            d="M12.5 29h39M20 38h10l4-5 5 7h7"
            stroke="rgba(243, 252, 255, .9)"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="1.6"
          />
          <circle cx="20" cy="38" r="2.2" fill="#dff8ff" />
          <circle cx="46" cy="40" r="2.2" fill="#75d9ff" />
        </svg>
      );
    case "system:launchpad":
      return (
        <svg {...common}>
          <path
            d="M21 21h22M21 43h22M21 21v22M43 21v22M21 21l22 22M43 21 21 43"
            stroke="rgba(158, 179, 255, .4)"
            strokeWidth="1.1"
          />
          {[
            [21, 21, "#62d9ff"],
            [43, 21, "#e1ad4c"],
            [21, 43, "#67cdb2"],
            [43, 43, "#7189a7"],
          ].map(([cx, cy, fill]) => (
            <rect
              key={`${cx}-${cy}`}
              x={Number(cx) - 5}
              y={Number(cy) - 5}
              width="10"
              height="10"
              rx="3"
              fill={String(fill)}
              fillOpacity=".82"
              stroke="rgba(255,255,255,.86)"
              strokeWidth="1.1"
            />
          ))}
          <circle cx="32" cy="32" r="3.5" fill="rgba(235,244,255,.96)" />
        </svg>
      );
    case "system:app-store":
    case "echo:/hub":
      return (
        <svg {...common}>
          <path
            d="M32 12 50 22.5 32 33 14 22.5 32 12Z"
            fill="rgba(255, 255, 255, .5)"
            stroke="rgba(255, 255, 255, .96)"
            strokeLinejoin="round"
            strokeWidth="1.45"
          />
          <path
            d="M14 22.5 32 33v19L14 41.5v-19Z"
            fill="rgba(128, 180, 255, .32)"
            stroke="rgba(238, 247, 255, .86)"
            strokeLinejoin="round"
            strokeWidth="1.35"
          />
          <path
            d="M50 22.5 32 33v19l18-10.5v-19Z"
            fill="rgba(72, 114, 180, .46)"
            stroke="rgba(232, 243, 255, .88)"
            strokeLinejoin="round"
            strokeWidth="1.35"
          />
          <path
            d="M32 17.5 42 23.2 32 29 22 23.2 32 17.5ZM20 28v10.5l9 5.2M44 28v10.5l-9 5.2"
            stroke="rgba(255, 255, 255, .66)"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="1.15"
          />
          <circle cx="32" cy="33" r="7" fill="rgba(218, 234, 255, .48)" />
          <circle
            cx="32"
            cy="33"
            r="4.7"
            fill="rgba(66, 112, 184, .88)"
            stroke="rgba(255, 255, 255, .92)"
            strokeWidth="1.1"
          />
          <circle cx="32" cy="33" r="1.8" fill="#fff" />
          <circle cx="32" cy="12" r="2.1" fill="#e8dcff" />
          <circle cx="14" cy="41.5" r="2.1" fill="#7fe0ff" />
          <circle cx="50" cy="41.5" r="2.1" fill="#e1ad4c" />
        </svg>
      );
    case "echo:/workspace/realtime/new":
      return (
        <EchoMark
          tone="light"
          className="mac-app-icon-art mac-shell-icon-art"
        />
      );
    case "echo:/browser":
      return (
        <svg {...common}>
          <circle
            cx="32"
            cy="32"
            r="20"
            fill="rgba(248,253,255,.72)"
            stroke="rgba(255,255,255,.96)"
            strokeWidth="1.9"
          />
          <circle cx="32" cy="32" r="16" fill="rgba(24,133,214,.28)" />
          <path
            d="M14 32h36M32 14c5.1 5.4 7.7 11.4 7.7 18S37.1 44.6 32 50M32 14c-5.1 5.4-7.7 11.4-7.7 18S26.9 44.6 32 50"
            stroke="rgba(239,252,255,.9)"
            strokeWidth="1.2"
          />
          <path
            d="M20 23c3.5-3.1 7.6-4.7 12-4.7s8.5 1.6 12 4.7M20 41c3.5 3.1 7.6 4.7 12 4.7s8.5-1.6 12-4.7"
            stroke="rgba(255,255,255,.58)"
            strokeWidth="1"
          />
          <path
            d="m32 19 4.2 13.1L32 45l-4.2-12.9L32 19Z"
            fill="rgba(255,255,255,.94)"
            stroke="rgba(17,104,190,.88)"
            strokeLinejoin="round"
            strokeWidth="1.2"
          />
          <circle cx="32" cy="32" r="2.2" fill="#1686dd" />
        </svg>
      );
    case "echo:/workspace/storage":
      return (
        <svg {...common}>
          <path
            d="M18 24c7 3.2 21 3.2 28 0"
            stroke="rgba(255,255,255,.4)"
            strokeLinecap="round"
            strokeWidth="1"
          />
          <ellipse
            cx="32"
            cy="19"
            rx="17"
            ry="7"
            fill="rgba(250,255,253,.72)"
            stroke="rgba(255,255,255,.96)"
            strokeWidth="1.5"
          />
          <path
            d="M15 19v25c0 3.9 7.6 7 17 7s17-3.1 17-7V19"
            fill="rgba(238,255,248,.34)"
            stroke="rgba(255,255,255,.94)"
            strokeWidth="1.5"
          />
          <path
            d="M15 31c0 3.9 7.6 7 17 7s17-3.1 17-7M15 43c0 3.9 7.6 7 17 7s17-3.1 17-7"
            stroke="rgba(231,255,247,.74)"
            strokeWidth="1.3"
          />
          <ellipse
            cx="32"
            cy="19"
            rx="7"
            ry="2.2"
            fill="rgba(25,145,104,.42)"
          />
        </svg>
      );
    case "echo:/storage-center":
      return (
        <svg {...common}>
          <rect
            x="11"
            y="13"
            width="42"
            height="12"
            rx="6"
            fill="rgba(255, 255, 255, .55)"
            stroke="rgba(255, 255, 255, .9)"
            strokeWidth="1.2"
          />
          <rect
            x="11"
            y="27"
            width="42"
            height="12"
            rx="6"
            fill="rgba(231, 251, 255, .46)"
            stroke="rgba(255, 255, 255, .82)"
            strokeWidth="1.2"
          />
          <rect
            x="11"
            y="41"
            width="42"
            height="12"
            rx="6"
            fill="rgba(195, 237, 255, .38)"
            stroke="rgba(246, 253, 255, .76)"
            strokeWidth="1.2"
          />
          <path
            d="M19 19h22M19 33h22M19 47h22"
            stroke="rgba(255, 255, 255, .8)"
            strokeLinecap="round"
            strokeWidth="1.7"
          />
          <path
            d="M16 25v2M48 25v2M16 39v2M48 39v2"
            stroke="rgba(150, 225, 255, .72)"
            strokeLinecap="round"
            strokeWidth="1.2"
          />
          <circle cx="47" cy="19" r="2.15" fill="#79f0cf" />
          <circle cx="47" cy="33" r="2.15" fill="#79f0cf" />
          <circle cx="47" cy="47" r="2.15" fill="#ffe38c" />
        </svg>
      );
    case "echo:/device-link":
      return (
        <svg {...common}>
          <path
            d="M25 13h14a5 5 0 0 1 5 5v28a5 5 0 0 1-5 5H25a5 5 0 0 1-5-5V18a5 5 0 0 1 5-5Z"
            fill="rgba(255,255,255,.56)"
            stroke="rgba(255,255,255,.96)"
            strokeWidth="1.6"
          />
          <rect
            x="24"
            y="19"
            width="16"
            height="24"
            rx="3.5"
            fill="rgba(88, 104, 207, .3)"
            stroke="rgba(226, 238, 255, .62)"
            strokeWidth="1"
          />
          <path
            d="m27 34 3-3 3 2 4-5"
            stroke="rgba(174, 247, 231, .9)"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="1.4"
          />
          <path
            d="M28 17h8M29 46h6"
            stroke="rgba(220,239,255,.72)"
            strokeLinecap="round"
            strokeWidth="1.5"
          />
          <path
            d="M12 28c3.3-3.3 6.6-3.3 10 0M9 23c5-5.3 10.7-5.3 16 0M52 28c-3.3-3.3-6.6-3.3-10 0M55 23c-5-5.3-10.7-5.3-16 0"
            stroke="rgba(177,239,255,.82)"
            strokeLinecap="round"
            strokeWidth="1.7"
          />
          <circle cx="32" cy="33" r="4" fill="rgba(142,243,218,.9)" />
          <circle cx="32" cy="33" r="1.5" fill="rgba(255,255,255,.95)" />
        </svg>
      );
    case "echo:/workspace/knowledge":
      return (
        <svg {...common}>
          <path
            d="M11 21.5a4.5 4.5 0 0 1 4.5-4.5h12l4 4h17a4.5 4.5 0 0 1 4.5 4.5v19A4.5 4.5 0 0 1 48.5 49h-33a4.5 4.5 0 0 1-4.5-4.5v-23Z"
            fill="rgba(255,250,224,.7)"
            stroke="rgba(255,255,255,.96)"
            strokeWidth="1.5"
          />
          <path
            d="M13 25h36l-3.7 18.3a4 4 0 0 1-3.9 3.2H18.6a4 4 0 0 1-3.9-3.2L13 25Z"
            fill="rgba(226,145,25,.3)"
          />
          <path
            d="M22 32h20M22 38h15"
            stroke="rgba(255,255,255,.76)"
            strokeLinecap="round"
            strokeWidth="2"
          />
        </svg>
      );
    case "echo:/photos":
      return (
        <svg {...common}>
          <circle
            cx="32"
            cy="32"
            r="18"
            fill="rgba(36, 18, 12, .18)"
            stroke="rgba(255, 247, 238, .86)"
            strokeWidth="1.35"
          />
          <circle
            cx="32"
            cy="32"
            r="12.5"
            fill="rgba(255, 247, 238, .13)"
            stroke="rgba(255, 247, 238, .58)"
            strokeWidth="1.15"
          />
          <path
            d="m32 19 7.2 4.2-2.1 8.2-8.4-1.1-3.4-7.7L32 19Zm7.2 4.2 5.2 6.5-5.7 6.3-7.7-3.5.7-8.4 7.5-.9Zm5.2 6.5-1.9 8.1-8.4.1-2.5-8.1 6.4-5.5 6.4 5.4Zm-1.9 8.1-7.4 5.5-6.4-5.5 3.5-7.7 8.4 1 .9 6.9Zm-7.4 5.5-8.1-.9-2.6-8 6.9-4.9 7.1 4.6-3.3 6.2Zm-8.1-.9-5.9-5.7 3-7.9 8.5.2 2.4 8.1-8 5.3Zm-5.9-5.7.6-8.2 7.8-3.3 5.7 6.2-4.2 7.4-9.9-2.1Z"
            fill="rgba(255, 245, 232, .3)"
            stroke="rgba(255, 250, 244, .68)"
            strokeLinejoin="round"
            strokeWidth=".75"
          />
          <circle
            cx="32"
            cy="32"
            r="5.4"
            fill="rgba(43, 20, 13, .5)"
            stroke="rgba(255, 250, 244, .94)"
            strokeWidth="1.35"
          />
          <circle cx="32" cy="32" r="2" fill="rgba(255, 221, 170, .95)" />
        </svg>
      );
    case "echo:/workspace/store":
      return (
        <svg {...common}>
          <path
            d="M16 24h32l-2.2 25H18.2L16 24Z"
            fill="rgba(252,248,255,.58)"
            stroke="rgba(255,255,255,.96)"
            strokeLinejoin="round"
            strokeWidth="1.6"
          />
          <path
            d="M23 25v-3.5a9 9 0 0 1 18 0V25"
            stroke="rgba(255,255,255,.86)"
            strokeLinecap="round"
            strokeWidth="2"
          />
          <path
            d="M22 34h20M22 40h13"
            stroke="rgba(255,255,255,.72)"
            strokeLinecap="round"
            strokeWidth="1.8"
          />
          <circle cx="43" cy="40" r="3" fill="rgba(222,174,255,.95)" />
        </svg>
      );
    case "echo:/workspace/observability":
      return (
        <svg {...common}>
          <path
            d="M15 19h34M15 27h25M15 35h16"
            stroke="rgba(182,245,255,.78)"
            strokeLinecap="round"
            strokeWidth="1.8"
          />
          <path
            d="m16 47 7-6 5 3 8-9 11 4"
            stroke="#71f0c5"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="1.8"
          />
        </svg>
      );
    case "system:tasks":
      return (
        <svg {...common}>
          <rect
            x="14"
            y="11"
            width="36"
            height="42"
            rx="9"
            fill="rgba(255,255,255,.3)"
            stroke="rgba(255,255,255,.72)"
            strokeWidth="1.2"
          />
          <path
            d="M21 16h22"
            stroke="rgba(210,236,255,.72)"
            strokeLinecap="round"
            strokeWidth="1.5"
          />
          <path
            d="m17 24 3 3 5-5M30 25h17M17 35l3 3 5-5M30 36h17M17 46l3 3 5-5M30 47h13"
            stroke="rgba(255,255,255,.9)"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="2"
          />
        </svg>
      );
    case "system:activity-monitor":
      return (
        <svg {...common}>
          <path
            d="M13 49V37h8l4-16 7 26 5-18 3 8h11v12Z"
            fill="rgba(92,244,166,.1)"
          />
          <path
            d="M13 37h8l4-16 7 26 5-18 3 8h11"
            stroke="#75f4ad"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="2.4"
          />
          <path d="M13 49h38" stroke="rgba(167,255,205,.42)" strokeWidth="1" />
          <circle cx="32" cy="47" r="2" fill="#c7ffdd" />
        </svg>
      );
    case "echo:/workspace":
    case "system:settings":
      return (
        <svg {...common}>
          <g fill="rgba(242, 249, 255, .64)">
            <rect x="28.5" y="7" width="7" height="14" rx="3.2" />
            <rect
              x="28.5"
              y="7"
              width="7"
              height="14"
              rx="3.2"
              transform="rotate(45 32 32)"
            />
            <rect
              x="28.5"
              y="7"
              width="7"
              height="14"
              rx="3.2"
              transform="rotate(90 32 32)"
            />
            <rect
              x="28.5"
              y="7"
              width="7"
              height="14"
              rx="3.2"
              transform="rotate(135 32 32)"
            />
            <rect x="28.5" y="43" width="7" height="14" rx="3.2" />
            <rect
              x="28.5"
              y="43"
              width="7"
              height="14"
              rx="3.2"
              transform="rotate(45 32 32)"
            />
            <rect
              x="28.5"
              y="43"
              width="7"
              height="14"
              rx="3.2"
              transform="rotate(90 32 32)"
            />
            <rect
              x="28.5"
              y="43"
              width="7"
              height="14"
              rx="3.2"
              transform="rotate(135 32 32)"
            />
          </g>
          <circle
            cx="32"
            cy="32"
            r="17"
            fill="rgba(239, 247, 255, .62)"
            stroke="rgba(255, 255, 255, .94)"
            strokeWidth="1.6"
          />
          <circle
            cx="32"
            cy="32"
            r="8.5"
            fill="rgba(73, 112, 147, .42)"
            stroke="#d9efff"
            strokeWidth="1.7"
          />
          <circle cx="32" cy="32" r="3.2" fill="#8ed5ff" />
          <circle cx="30.8" cy="30.7" r="1" fill="#fff" />
        </svg>
      );
    case "system:trash":
      return (
        <svg {...common}>
          <path
            d="M19 22h26l-2.2 27H21.2L19 22Z"
            fill="rgba(245, 251, 255, .52)"
            stroke="rgba(255, 255, 255, .96)"
            strokeLinejoin="round"
            strokeWidth="1.6"
          />
          <path
            d="M16 22h32M26 17h12M27 29v13M37 29v13"
            stroke="rgba(194, 221, 244, .82)"
            strokeLinecap="round"
            strokeWidth="1.7"
          />
          <path
            d="M24 47h16"
            stroke="#7edaff"
            strokeLinecap="round"
            strokeWidth="1.4"
          />
          <circle cx="44" cy="22" r="2" fill="#c2ddf4" />
        </svg>
      );
    default:
      return null;
  }
}

/** Original fluid ribbon artwork shared by the desktop and lock screen. */
export function MacDesktopWallpaperArtwork() {
  return (
    <img
      aria-hidden="true"
      className="desktop-wallpaper-art"
      src="/third-party/appletechie-macos/wallpaper-day2.jpg"
      alt=""
    />
  );
}

export function MacAppIcon({
  icon: Icon,
  gradient,
  iconUrl,
  appId,
  className,
  liquidBackdrop = false,
}: {
  icon: MacIcon;
  gradient: string;
  iconUrl?: string;
  appId?: string;
  className?: string;
  liquidBackdrop?: boolean;
}) {
  const appArtwork = iconUrl ? null : MacAppArtwork({ appId });
  const echoPalette = appId ? ECHO_APP_ICON_PALETTES[appId] : undefined;
  const showTechField = appId ? ECHO_TECH_FIELD_APP_IDS.has(appId) : false;

  return (
    <span
      className={cn("mac-app-icon", className)}
      style={
        {
          "--mac-app-gradient": gradient,
          "--echo-app-surface": echoPalette?.surface,
          "--echo-app-accent": echoPalette?.accent,
          "--echo-app-glow": echoPalette?.glow,
        } as CSSProperties
      }
      data-app-id={appId}
      data-icon-source={iconUrl ? "image" : appArtwork ? "art" : "glyph"}
      data-echo-family={echoPalette ? "true" : undefined}
      data-liquid-backdrop={liquidBackdrop ? "true" : undefined}
      aria-hidden
    >
      {liquidBackdrop && <MacIconLiquidBackdrop />}
      {liquidBackdrop && (
        <span
          className="mac-app-icon-tint"
          style={{ background: gradient }}
          aria-hidden
        />
      )}
      {echoPalette && showTechField && (
        <span className="mac-app-icon-tech-field" />
      )}
      {echoPalette && (
        <span className="mac-app-icon-echo-signal">
          <span />
        </span>
      )}
      <span className="mac-app-icon-gloss" />
      <span className="mac-app-icon-specular" />
      {echoPalette && <span className="mac-app-icon-optical-rim" />}
      {iconUrl ? (
        <img src={iconUrl} alt="" className="mac-app-icon-image" />
      ) : appArtwork ? (
        appArtwork
      ) : (
        <Icon className="mac-app-icon-glyph" strokeWidth={1.8} />
      )}
    </span>
  );
}

type MenuAction = {
  label?: string;
  shortcut?: string;
  divider?: boolean;
  disabled?: boolean;
  onSelect?: () => void;
};

export type MacSystemAction = "logout" | "suspend" | "restart" | "shutdown";

export type MacSystemCapabilities = {
  lock: boolean;
  logout: boolean;
  suspend: boolean;
  restart: boolean;
  shutdown: boolean;
};

export type MacLiquidGlassStyle = "crystal" | "softlight";
export type MacLiquidGlassIntensity = "weak" | "balanced" | "strong";

function MacMenuDropdown({
  items,
  align = "left",
}: {
  items: MenuAction[];
  align?: "left" | "right";
}) {
  return (
    <div
      className={cn("mac-menu-dropdown", align === "right" && "right-0")}
      data-liquid-surface="thick"
    >
      {items.map((item, index) =>
        item.divider ? (
          <div key={`divider-${index}`} className="mac-menu-separator" />
        ) : (
          <button
            key={`${item.label}-${index}`}
            type="button"
            disabled={item.disabled}
            onClick={item.onSelect}
            className="mac-menu-item"
          >
            <span>{item.label}</span>
            {item.shortcut && (
              <span className="mac-menu-shortcut">{item.shortcut}</span>
            )}
          </button>
        ),
      )}
    </div>
  );
}

export function MacMenuBar({
  activeApp = "文件管理器",
  controlCenterOpen,
  notificationsOpen,
  liquidGlassOpen,
  onOpenSpotlight,
  onToggleControlCenter,
  onToggleNotifications,
  onToggleLiquidGlass,
  onOpenAbout,
  onOpenFiles,
  onOpenSettings,
  appStoreAvailable,
  onOpenAppStore,
  onOpenLaunchpad,
  systemCapabilities,
  systemControls,
  onLockScreen,
  onSystemAction,
  notificationCount = 0,
}: {
  activeApp?: string;
  controlCenterOpen: boolean;
  notificationsOpen: boolean;
  liquidGlassOpen: boolean;
  onOpenSpotlight: () => void;
  onToggleControlCenter: () => void;
  onToggleNotifications: () => void;
  onToggleLiquidGlass: () => void;
  onOpenAbout: () => void;
  onOpenFiles: () => void;
  onOpenSettings: () => void;
  appStoreAvailable: boolean;
  onOpenAppStore: () => void;
  onOpenLaunchpad: () => void;
  systemCapabilities: MacSystemCapabilities;
  systemControls?: SystemControlState | null;
  onLockScreen: () => void;
  onSystemAction: (action: MacSystemAction) => void;
  notificationCount?: number;
}) {
  const [activeMenu, setActiveMenu] = useState<string | null>(null);
  const [now, setNow] = useState(() => new Date());
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setActiveMenu(null);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, []);

  const select = (action: () => void) => {
    setActiveMenu(null);
    action();
  };

  const menus: Array<{ label: string; key: string; items: MenuAction[] }> = [
    {
      label: activeApp,
      key: "app",
      items: [
        { label: `关于 ${activeApp}`, onSelect: () => select(onOpenAbout) },
        { divider: true },
        {
          label: "设置…",
          shortcut: "Ctrl+,",
          onSelect: () => select(onOpenSettings),
        },
        { divider: true },
        { label: `隐藏 ${activeApp}`, shortcut: "Ctrl+H" },
        { label: "隐藏其他", shortcut: "Ctrl+Alt+H" },
      ],
    },
    {
      label: "文件",
      key: "file",
      items: [
        {
          label: "新建文件窗口",
          shortcut: "Ctrl+N",
          onSelect: () => select(onOpenFiles),
        },
        { label: "新建文件夹", shortcut: "Ctrl+Shift+N", disabled: true },
        { divider: true },
        {
          label: "打开",
          shortcut: "Ctrl+O",
          onSelect: () => select(onOpenFiles),
        },
        { label: "关闭窗口", shortcut: "Ctrl+W", disabled: true },
      ],
    },
    {
      label: "编辑",
      key: "edit",
      items: [
        { label: "撤销", shortcut: "Ctrl+Z", disabled: true },
        { label: "重做", shortcut: "Ctrl+Shift+Z", disabled: true },
        { divider: true },
        { label: "剪切", shortcut: "Ctrl+X", disabled: true },
        { label: "拷贝", shortcut: "Ctrl+C", disabled: true },
        { label: "粘贴", shortcut: "Ctrl+V", disabled: true },
      ],
    },
    {
      label: "显示",
      key: "view",
      items: [
        {
          label: "显示应用库",
          shortcut: "F4",
          onSelect: () => select(onOpenLaunchpad),
        },
        { label: "显示边栏", shortcut: "Ctrl+Shift+S", disabled: true },
        { divider: true },
        { label: "进入全屏幕", shortcut: "Ctrl+Shift+F", disabled: true },
      ],
    },
    {
      label: "前往",
      key: "go",
      items: [
        {
          label: "个人",
          shortcut: "Ctrl+Shift+H",
          onSelect: () => select(onOpenFiles),
        },
        {
          label: "桌面",
          shortcut: "Ctrl+Shift+D",
          onSelect: () => select(onOpenFiles),
        },
        {
          label: "应用程序",
          shortcut: "Ctrl+Shift+A",
          onSelect: () => select(onOpenLaunchpad),
        },
      ],
    },
    {
      label: "窗口",
      key: "window",
      items: [
        { label: "最小化", shortcut: "Ctrl+M", disabled: true },
        { label: "缩放", disabled: true },
        { divider: true },
        { label: "前置全部窗口", disabled: true },
      ],
    },
    {
      label: "帮助",
      key: "help",
      items: [
        { label: "Echo OS 帮助", onSelect: () => select(onOpenAbout) },
        { label: "键盘快捷键", disabled: true },
      ],
    },
  ];

  const dateText = `${now.getMonth() + 1}月${now.getDate()}日 周${"日一二三四五六"[now.getDay()]}`;
  const timeText = now.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const batteryLabel = systemControls?.nativeShell
    ? systemControls.battery.present &&
      systemControls.battery.percentage !== null
      ? `电池 ${systemControls.battery.percentage}%`
      : "未检测到电池"
    : "电池";
  const wifiLabel = systemControls?.nativeShell
    ? systemControls.wifi.enabled
      ? `Wi-Fi ${systemControls.wifi.connection || "已打开"}`
      : systemControls.wifi.enabled === false
        ? "Wi-Fi 已关闭"
        : "Wi-Fi 不可用"
    : "Wi-Fi";

  return (
    <header
      ref={menuRef}
      data-desktop-interactive
      data-liquid-surface="ultra-thin"
      className="mac-menu-bar"
    >
      <div className="mac-menu-left">
        <div className="relative">
          <button
            type="button"
            className={cn(
              "mac-menu-logo",
              activeMenu === "system" && "is-active",
            )}
            onClick={() =>
              setActiveMenu((value) => (value === "system" ? null : "system"))
            }
            aria-label="Echo 菜单"
          >
            <img src="/favicon.svg" alt="" className="size-[17px]" />
          </button>
          {activeMenu === "system" && (
            <MacMenuDropdown
              items={[
                { label: "关于本机", onSelect: () => select(onOpenAbout) },
                { divider: true },
                { label: "系统设置…", onSelect: () => select(onOpenSettings) },
                {
                  label: "应用中心…",
                  disabled: !appStoreAvailable,
                  onSelect: () => select(onOpenAppStore),
                },
                { divider: true },
                { label: "最近使用的项目", disabled: true },
                { divider: true },
                { label: "结束无响应应用…", disabled: true },
                { divider: true },
                {
                  label: "睡眠…",
                  disabled: !systemCapabilities.suspend,
                  onSelect: () => select(() => onSystemAction("suspend")),
                },
                {
                  label: "重新启动…",
                  disabled: !systemCapabilities.restart,
                  onSelect: () => select(() => onSystemAction("restart")),
                },
                {
                  label: "关机…",
                  disabled: !systemCapabilities.shutdown,
                  onSelect: () => select(() => onSystemAction("shutdown")),
                },
                { divider: true },
                {
                  label: "锁定屏幕",
                  shortcut: "Ctrl+Alt+Q",
                  disabled: !systemCapabilities.lock,
                  onSelect: () => select(onLockScreen),
                },
                {
                  label: "退出登录…",
                  disabled: !systemCapabilities.logout,
                  onSelect: () => select(() => onSystemAction("logout")),
                },
              ]}
            />
          )}
        </div>
        {menus.map((menu, index) => (
          <div key={menu.key} className="relative">
            <button
              type="button"
              className={cn(
                "mac-menu-button",
                index === 0 && "font-semibold",
                activeMenu === menu.key && "is-active",
              )}
              onClick={() =>
                setActiveMenu((value) => (value === menu.key ? null : menu.key))
              }
              onPointerEnter={() => activeMenu && setActiveMenu(menu.key)}
            >
              {menu.label}
            </button>
            {activeMenu === menu.key && <MacMenuDropdown items={menu.items} />}
          </div>
        ))}
      </div>

      <div className="mac-menu-right">
        <button
          type="button"
          className={cn("mac-status-icon", liquidGlassOpen && "is-active")}
          onClick={onToggleLiquidGlass}
          aria-label="流光玻璃设置"
          aria-pressed={liquidGlassOpen}
        >
          <DropletsIcon className="size-[15px]" />
        </button>
        <button
          type="button"
          className="mac-status-icon"
          aria-label={batteryLabel}
          title={batteryLabel}
        >
          <BatteryFullIcon className="size-[15px]" />
        </button>
        <button
          type="button"
          className={cn(
            "mac-status-icon",
            systemControls?.nativeShell &&
              systemControls.wifi.enabled === false &&
              "opacity-45",
          )}
          aria-label={wifiLabel}
          title={wifiLabel}
        >
          <WifiIcon className="size-[15px]" />
        </button>
        <button
          type="button"
          className={cn("mac-status-icon", controlCenterOpen && "is-active")}
          onClick={onToggleControlCenter}
          aria-label="控制中心"
        >
          <SlidersHorizontalIcon className="size-[15px]" />
        </button>
        <button
          type="button"
          className="mac-status-icon"
          onClick={onOpenSpotlight}
          aria-label="全局搜索"
        >
          <SearchIcon className="size-[14px]" />
        </button>
        <button
          type="button"
          className={cn(
            "mac-clock",
            notificationsOpen && "is-active",
            notificationCount > 0 && "has-notifications",
          )}
          onClick={onToggleNotifications}
          aria-label={
            notificationCount > 0
              ? `通知中心，${notificationCount} 条通知`
              : "通知中心"
          }
        >
          <span className="hidden sm:inline">{dateText}</span>
          <span>{timeText}</span>
          {notificationCount > 0 && (
            <span className="mac-notification-count" aria-hidden>
              {notificationCount > 99 ? "99+" : notificationCount}
            </span>
          )}
        </button>
      </div>
    </header>
  );
}

const GLASS_STYLE_OPTIONS: Array<{
  value: MacLiquidGlassStyle;
  label: string;
  description: string;
}> = [
  {
    value: "crystal",
    label: "晶透",
    description: "清透折射 · 镜面高光",
  },
  {
    value: "softlight",
    label: "柔光",
    description: "雾化扩散 · 沉浸光场",
  },
];

const GLASS_INTENSITY_OPTIONS: Array<{
  value: MacLiquidGlassIntensity;
  label: string;
}> = [
  { value: "weak", label: "克制" },
  { value: "balanced", label: "均衡" },
  { value: "strong", label: "沉浸" },
];

function MacGlassParameter({
  label,
  value,
  min,
  max,
  unit,
  formatValue,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  unit: string;
  formatValue?: (value: number) => string;
  onChange: (value: number) => void;
}) {
  const progress = ((value - min) / Math.max(1, max - min)) * 100;

  return (
    <label className="mac-glass-parameter">
      <span>
        <strong>{label}</strong>
        <output>{formatValue ? formatValue(value) : `${value}${unit}`}</output>
      </span>
      <input
        type="range"
        aria-label={label}
        min={min}
        max={max}
        value={value}
        style={{ "--glass-range-progress": `${progress}%` } as CSSProperties}
        onChange={(event) => onChange(Number(event.currentTarget.value))}
      />
    </label>
  );
}

export function MacLiquidGlassPanel({
  open,
  style,
  intensity,
  tuning,
  onStyleChange,
  onIntensityChange,
  onTuningChange,
  onResetTuning,
  onClose,
}: {
  open: boolean;
  style: MacLiquidGlassStyle;
  intensity: MacLiquidGlassIntensity;
  tuning: LiquidGlassTuning;
  onStyleChange: (style: MacLiquidGlassStyle) => void;
  onIntensityChange: (intensity: MacLiquidGlassIntensity) => void;
  onTuningChange: (patch: Partial<LiquidGlassTuning>) => void;
  onResetTuning: () => void;
  onClose: () => void;
}) {
  if (!open) return null;

  return (
    <>
      <button
        className="mac-panel-scrim"
        type="button"
        onClick={onClose}
        aria-label="关闭流光玻璃设置"
      />
      <section
        className="mac-liquid-glass-panel"
        data-desktop-interactive
        data-liquid-surface="thick"
        aria-label="流光玻璃设置"
      >
        <header className="mac-glass-panel-header">
          <span className="mac-glass-panel-mark">
            <DropletsIcon />
          </span>
          <span>
            <strong>流光玻璃</strong>
            <small>材质、光学与颜色滤镜</small>
          </span>
          <button
            type="button"
            className="mac-glass-panel-reset"
            onClick={onResetTuning}
            aria-label="恢复全部默认玻璃设置"
            title="恢复全部默认"
          >
            <RefreshCwIcon />
          </button>
        </header>

        <div className="mac-glass-preview" aria-hidden>
          <span className="mac-glass-preview-orb is-primary" />
          <span className="mac-glass-preview-orb is-secondary" />
          <span className="mac-glass-preview-lens">
            <DropletsIcon />
          </span>
          <small>{style === "crystal" ? "Crystal Flow" : "Soft Aurora"}</small>
        </div>

        <button
          type="button"
          className="mac-glass-clear-preset"
          onClick={() => {
            onStyleChange("crystal");
            onIntensityChange("balanced");
            onTuningChange(CLEAR_LIQUID_GLASS_TUNING);
          }}
          aria-label="应用净透液态预设"
        >
          <DropletsIcon />
          <span>
            <strong>净透液态</strong>
            <small>低底色 · 清晰折射 · 轻磨砂</small>
          </span>
          <ChevronRightIcon />
        </button>

        <fieldset className="mac-glass-fieldset">
          <legend>材质风格</legend>
          <div className="mac-glass-style-options">
            {GLASS_STYLE_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                className={cn(style === option.value && "is-selected")}
                onClick={() => onStyleChange(option.value)}
                aria-pressed={style === option.value}
              >
                <strong>{option.label}</strong>
                <small>{option.description}</small>
              </button>
            ))}
          </div>
        </fieldset>

        <fieldset className="mac-glass-fieldset">
          <legend>动态光感</legend>
          <div className="mac-glass-intensity-options">
            {GLASS_INTENSITY_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                className={cn(intensity === option.value && "is-selected")}
                onClick={() => onIntensityChange(option.value)}
                aria-pressed={intensity === option.value}
              >
                {option.label}
              </button>
            ))}
          </div>
        </fieldset>

        <fieldset className="mac-glass-fieldset mac-glass-parameter-fieldset">
          <legend>光学参数</legend>
          <div className="mac-glass-parameter-list">
            <MacGlassParameter
              label="透明度"
              value={tuning.transparency}
              min={35}
              max={100}
              unit="%"
              onChange={(transparency) => onTuningChange({ transparency })}
            />
            <MacGlassParameter
              label="磨砂度"
              value={tuning.frost}
              min={0}
              max={64}
              unit="px"
              onChange={(frost) => onTuningChange({ frost })}
            />
            <MacGlassParameter
              label="折射率"
              value={tuning.refraction}
              min={0}
              max={100}
              unit=""
              formatValue={(value) => (1 + value * 0.008).toFixed(2)}
              onChange={(refraction) => onTuningChange({ refraction })}
            />
            <MacGlassParameter
              label="光学厚度"
              value={tuning.thickness}
              min={1}
              max={24}
              unit="mm"
              onChange={(thickness) => onTuningChange({ thickness })}
            />
            <MacGlassParameter
              label="色散 Δn"
              value={tuning.dispersion}
              min={0}
              max={40}
              unit=""
              formatValue={(value) => (value / 1000).toFixed(3)}
              onChange={(dispersion) => onTuningChange({ dispersion })}
            />
            <MacGlassParameter
              label="色彩浓度"
              value={tuning.saturation}
              min={70}
              max={180}
              unit="%"
              onChange={(saturation) => onTuningChange({ saturation })}
            />
          </div>
        </fieldset>

        <fieldset className="mac-glass-fieldset">
          <legend>玻璃滤镜</legend>
          <div className="mac-glass-tint-options">
            {LIQUID_GLASS_TINTS.map((option) => (
              <button
                key={option.value}
                type="button"
                className={cn(tuning.tint === option.value && "is-selected")}
                onClick={() => onTuningChange({ tint: option.value })}
                aria-label={`使用${option.label}滤镜`}
                aria-pressed={tuning.tint === option.value}
              >
                <span style={{ backgroundColor: option.value }} />
                {option.label}
              </button>
            ))}
            <label className="mac-glass-custom-tint" title="自定义颜色">
              <input
                type="color"
                aria-label="自定义玻璃颜色"
                value={tuning.tint}
                onChange={(event) =>
                  onTuningChange({ tint: event.currentTarget.value })
                }
              />
              自定
            </label>
          </div>
          <MacGlassParameter
            label="染色浓度"
            value={tuning.tintStrength}
            min={0}
            max={40}
            unit="%"
            onChange={(tintStrength) => onTuningChange({ tintStrength })}
          />
        </fieldset>

        <footer>
          <span className="mac-glass-live-dot" />
          交互时实时计算，静止后折射层自动休眠
        </footer>
      </section>
    </>
  );
}

export function MacDesktopIcon({ app }: { app: MacShellApp }) {
  return (
    <button
      type="button"
      data-desktop-interactive
      data-liquid-icon
      className="mac-desktop-icon"
      onDoubleClick={app.onOpen}
      onClick={(event) => {
        if (event.detail === 2) app.onOpen();
      }}
      title={`打开${app.name}`}
    >
      <MacAppIcon
        icon={app.icon}
        gradient={app.gradient}
        iconUrl={app.iconUrl}
        appId={app.id}
        liquidBackdrop
      />
      <span className="mac-desktop-icon-label">{app.name}</span>
    </button>
  );
}

/**
 * A small, viewport-aligned copy of the wallpaper used inside the desktop
 * widgets. Chromium's backdrop-filter can blur the scene reliably, but its
 * custom backdrop displacement is not consistent across compositor paths.
 * Keeping this lens as a clipped, low-opacity copy gives the glass a real
 * colour seam to bend while retaining the regular backdrop-filter fallback.
 */
function MacSurfaceLens() {
  const lensRef = useRef<HTMLSpanElement>(null);

  useLayoutEffect(() => {
    const lens = lensRef.current;
    const surface = lens?.parentElement;
    if (!lens || !surface) return;

    const sync = () => {
      const rect = surface.getBoundingClientRect();
      lens.style.setProperty("--mac-lens-left", `${rect.left}px`);
      lens.style.setProperty("--mac-lens-top", `${rect.top}px`);
    };

    sync();
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(sync);
    observer?.observe(surface);
    window.addEventListener("resize", sync);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", sync);
    };
  }, []);

  return (
    <span ref={lensRef} className="mac-surface-lens" aria-hidden>
      <span className="mac-surface-lens-raster">
        <img src="/third-party/appletechie-macos/wallpaper-day2.jpg" alt="" />
      </span>
      <MacDesktopWallpaperArtwork />
    </span>
  );
}

/**
 * A quiet wallpaper transmission layer for desktop app icons. Native macOS
 * icons remain authored artwork, while Echo's desktop treatment lets the
 * surrounding scene tint their glassy finish. Keeping this copy aligned to
 * the viewport makes the colour seam continuous with the widgets and Dock.
 */
function MacIconLiquidBackdrop() {
  const backdropRef = useRef<HTMLSpanElement>(null);

  useLayoutEffect(() => {
    const icon = backdropRef.current?.parentElement;
    if (!icon) return;

    const sync = () => {
      const rect = icon.getBoundingClientRect();
      icon.style.setProperty("--mac-icon-left", `${rect.left}px`);
      icon.style.setProperty("--mac-icon-top", `${rect.top}px`);
    };

    sync();
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(sync);
    observer?.observe(icon);
    window.addEventListener("resize", sync);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", sync);
    };
  }, []);

  return (
    <span
      ref={backdropRef}
      className="mac-app-icon-liquid-backdrop"
      aria-hidden
    >
      <img src="/third-party/appletechie-macos/wallpaper-day2.jpg" alt="" />
    </span>
  );
}

export function MacDesktopWidgets({
  onOpenWorkspace,
  onOpenNotifications,
  agentHealth,
}: {
  onOpenWorkspace: () => void;
  onOpenNotifications?: () => void;
  agentHealth: AgentDesktopHealth;
}) {
  const today = new Date();
  const agentCopy: Record<
    AgentDesktopHealthState,
    { title: string; detail: string; label: string }
  > = {
    checking: {
      title: "正在连接 Echo",
      detail: "正在检查本机 Agent Runtime",
      label: "正在连接 Echo Agent，打开工作台",
    },
    ready: {
      title: "Echo Agent 在线",
      detail: "点击开始新的 Agent 会话",
      label: "Echo Agent 在线，打开工作台",
    },
    "restart-required": {
      title: "Echo Agent 等待重启",
      detail: "Runtime 更新将在重启后生效",
      label: "Echo Agent 等待重启，打开工作台",
    },
    unavailable: {
      title: "Echo Agent 未连接",
      detail: "点击打开工作台检查连接",
      label: "Echo Agent 未连接，打开工作台检查连接",
    },
  };
  const statusCopy = agentCopy[agentHealth.state];
  if (agentHealth.state === "ready") {
    if (agentHealth.verifiedBundle && agentHealth.sourceId) {
      statusCopy.title = "Echo Agent 已验证";
      statusCopy.detail = `${agentHealth.version ? `v${agentHealth.version} · ` : ""}${agentHealth.sourceId.slice(0, 8)}`;
      statusCopy.label = `${statusCopy.title}，${statusCopy.detail}，打开工作台`;
    } else {
      statusCopy.detail = agentHealth.version
        ? `v${agentHealth.version} · 来源未验证`
        : "Runtime 版本未知 · 来源未验证";
      statusCopy.label = `${statusCopy.title}，${statusCopy.detail}，打开工作台`;
    }
  }

  return (
    <aside className="mac-widget-stack" data-desktop-interactive>
      <button
        type="button"
        className="mac-calendar-widget"
        data-liquid-surface="thick-dark"
        aria-label="日历"
        onClick={onOpenNotifications}
      >
        <MacSurfaceLens />
        <span className="mac-widget-eyebrow">
          周{"日一二三四五六"[today.getDay()]}
        </span>
        <span className="mac-widget-date">{today.getDate()}</span>
        <span className="mac-widget-caption">
          {today.getFullYear()}年{today.getMonth() + 1}月
        </span>
      </button>
      <button
        type="button"
        className={cn("mac-agent-widget", `is-${agentHealth.state}`)}
        data-liquid-surface="thick-dark"
        data-agent-status={agentHealth.state}
        onClick={onOpenWorkspace}
        aria-label={statusCopy.label}
      >
        <MacSurfaceLens />
        <span className="mac-agent-orb">
          <EchoMark tone="light" className="size-5" />
        </span>
        <span className="min-w-0 text-left" aria-live="polite">
          <strong>{statusCopy.title}</strong>
          <small>{statusCopy.detail}</small>
        </span>
        <ChevronRightIcon className="size-4 shrink-0 opacity-55" />
      </button>
    </aside>
  );
}

export function MacSpotlight({
  open,
  query,
  apps,
  onQueryChange,
  onClose,
  onSubmit,
}: {
  open: boolean;
  query: string;
  apps: MacShellApp[];
  onQueryChange: (value: string) => void;
  onClose: () => void;
  onSubmit: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return apps.slice(0, 5);
    return apps
      .filter((app) =>
        `${app.name} ${app.subtitle ?? ""}`.toLowerCase().includes(needle),
      )
      .slice(0, 6);
  }, [apps, query]);

  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(() => inputRef.current?.focus(), 80);
    return () => window.clearTimeout(timer);
  }, [open]);

  if (!open) return null;
  return (
    <div
      className="mac-overlay mac-spotlight-overlay"
      data-desktop-interactive
      onPointerDown={onClose}
    >
      <section
        className="mac-spotlight"
        data-liquid-surface="thick"
        onPointerDown={(event) => event.stopPropagation()}
      >
        <div className="mac-spotlight-input-row">
          <SearchIcon className="mac-spotlight-search-icon" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") onSubmit();
              if (event.key === "Escape") onClose();
            }}
            placeholder="全局搜索"
            className="mac-spotlight-input"
          />
          <kbd className="mac-keycap">esc</kbd>
        </div>
        <div className="mac-spotlight-results">
          <p className="mac-spotlight-heading">最佳匹配</p>
          {filtered.length ? (
            filtered.map((app, index) => (
              <button
                type="button"
                key={app.id}
                className={cn(
                  "mac-spotlight-result",
                  index === 0 && "is-selected",
                )}
                onClick={() => {
                  app.onOpen();
                  onClose();
                }}
              >
                <MacAppIcon
                  icon={app.icon}
                  gradient={app.gradient}
                  iconUrl={app.iconUrl}
                  appId={app.id}
                />
                <span>
                  <strong>{app.name}</strong>
                  <small>{app.subtitle || "应用程序"}</small>
                </span>
                <span className="mac-result-kind">应用程序</span>
              </button>
            ))
          ) : (
            <div className="mac-spotlight-empty">没有找到匹配的项目</div>
          )}
        </div>
        <footer className="mac-spotlight-footer">
          <span>按下 Return 打开</span>
          <span>Ctrl + Space 显示或隐藏全局搜索</span>
        </footer>
      </section>
    </div>
  );
}

export function MacLaunchpad({
  open,
  apps,
  onClose,
}: {
  open: boolean;
  apps: MacShellApp[];
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const visibleApps = useMemo(() => {
    const value = query.trim().toLowerCase();
    return value
      ? apps.filter((app) =>
          `${app.name} ${app.subtitle ?? ""}`.toLowerCase().includes(value),
        )
      : apps;
  }, [apps, query]);

  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  if (!open) return null;
  return (
    <div className="mac-launchpad" data-desktop-interactive>
      <div className="mac-launchpad-backdrop" />
      <button
        type="button"
        onClick={onClose}
        className="mac-launchpad-close"
        aria-label="关闭应用库"
      >
        <XIcon className="size-4" />
      </button>
      <div className="mac-launchpad-search">
        <SearchIcon className="size-3.5" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索"
          autoFocus
        />
      </div>
      <div className="mac-launchpad-grid">
        {visibleApps.map((app) => (
          <button
            type="button"
            key={app.id}
            className="mac-launchpad-app"
            onClick={() => {
              app.onOpen();
              onClose();
            }}
          >
            <MacAppIcon
              icon={app.icon}
              gradient={app.gradient}
              iconUrl={app.iconUrl}
              appId={app.id}
            />
            <span>{app.name}</span>
          </button>
        ))}
      </div>
      <div className="mac-page-dots">
        <span className="is-active" />
        <span />
      </div>
    </div>
  );
}

export function MacControlCenter({
  open,
  onClose,
  onOpenSettings,
  systemControls,
  onSetWifiEnabled,
  onSetBluetoothEnabled,
  onSetAudioVolume,
  onSetDisplayBrightness,
}: {
  open: boolean;
  onClose: () => void;
  onOpenSettings: () => void;
  systemControls?: SystemControlState | null;
  onSetWifiEnabled?: (enabled: boolean) => Promise<unknown> | unknown;
  onSetBluetoothEnabled?: (enabled: boolean) => Promise<unknown> | unknown;
  onSetAudioVolume?: (percentage: number) => Promise<unknown> | unknown;
  onSetDisplayBrightness?: (percentage: number) => Promise<unknown> | unknown;
}) {
  const [previewWifi, setPreviewWifi] = useState(true);
  const [previewBluetooth, setPreviewBluetooth] = useState(true);
  const [focus, setFocus] = useState(false);
  const [brightness, setBrightness] = useState(78);
  const [volume, setVolume] = useState(42);
  const [busyControl, setBusyControl] = useState<"wifi" | "bluetooth" | null>(
    null,
  );

  useEffect(() => {
    const nativeBrightness = systemControls?.display?.brightness;
    const nativeVolume = systemControls?.audio?.volume;
    if (typeof nativeBrightness === "number") {
      setBrightness(nativeBrightness);
    }
    if (typeof nativeVolume === "number") {
      setVolume(nativeVolume);
    }
  }, [systemControls]);

  const nativeControls = systemControls?.nativeShell === true;
  const wifi = nativeControls
    ? systemControls.wifi.enabled === true
    : previewWifi;
  const bluetooth = nativeControls
    ? systemControls.bluetooth.enabled === true
    : previewBluetooth;
  const wifiUnavailable =
    nativeControls &&
    (!systemControls?.wifi.available || systemControls?.wifi.enabled === null);
  const bluetoothUnavailable =
    nativeControls &&
    (!systemControls?.bluetooth.available ||
      !systemControls?.bluetooth.present ||
      systemControls?.bluetooth.enabled === null);

  const toggleWifi = async () => {
    const next = !wifi;
    if (!nativeControls) {
      setPreviewWifi(next);
      return;
    }
    if (wifiUnavailable || !onSetWifiEnabled) return;
    setBusyControl("wifi");
    try {
      await onSetWifiEnabled(next);
    } finally {
      setBusyControl(null);
    }
  };

  const toggleBluetooth = async () => {
    const next = !bluetooth;
    if (!nativeControls) {
      setPreviewBluetooth(next);
      return;
    }
    if (bluetoothUnavailable || !onSetBluetoothEnabled) return;
    setBusyControl("bluetooth");
    try {
      await onSetBluetoothEnabled(next);
    } finally {
      setBusyControl(null);
    }
  };

  const commitSlider = (kind: "audio" | "display", value: number) => {
    if (!nativeControls) return;
    if (kind === "audio") void onSetAudioVolume?.(value);
    else void onSetDisplayBrightness?.(value);
  };

  if (!open) return null;
  return (
    <>
      <button
        className="mac-panel-scrim"
        type="button"
        onClick={onClose}
        aria-label="关闭控制中心"
      />
      <section className="mac-control-center" data-desktop-interactive>
        <div className="mac-control-grid">
          <button
            type="button"
            className="mac-control-network"
            data-liquid-surface="thick"
            disabled={wifiUnavailable || busyControl === "wifi"}
            onClick={() => void toggleWifi()}
          >
            <span className={cn("mac-control-round", wifi && "is-on")}>
              <WifiIcon />
            </span>
            <span>
              <strong>Wi-Fi</strong>
              <small>
                {wifiUnavailable
                  ? "不可用"
                  : wifi
                    ? systemControls?.wifi.connection || "打开"
                    : "关闭"}
              </small>
            </span>
            <ChevronRightIcon className="ml-auto size-3.5 opacity-40" />
          </button>
          <button
            type="button"
            className="mac-control-network"
            data-liquid-surface="thick"
            disabled={bluetoothUnavailable || busyControl === "bluetooth"}
            onClick={() => void toggleBluetooth()}
          >
            <span className={cn("mac-control-round", bluetooth && "is-on")}>
              <BluetoothIcon />
            </span>
            <span>
              <strong>蓝牙</strong>
              <small>
                {bluetoothUnavailable ? "不可用" : bluetooth ? "打开" : "关闭"}
              </small>
            </span>
            <ChevronRightIcon className="ml-auto size-3.5 opacity-40" />
          </button>
          <button
            type="button"
            className={cn("mac-control-tile", focus && "is-on")}
            data-liquid-surface="thick"
            aria-pressed={focus}
            onClick={() => setFocus((value) => !value)}
          >
            <span className="mac-control-round">
              <MoonIcon />
            </span>
            <strong>专注模式</strong>
          </button>
          <button
            type="button"
            className="mac-control-tile"
            data-liquid-surface="thick"
            onClick={onOpenSettings}
          >
            <span className="mac-control-round">
              <SettingsIcon />
            </span>
            <strong>系统设置</strong>
          </button>
        </div>
        <label className="mac-control-slider" data-liquid-surface="thick">
          <span>
            <SunIcon className="size-4" />
            显示器
          </span>
          <input
            type="range"
            min="10"
            max="100"
            value={brightness}
            style={
              { "--mac-control-progress": `${brightness}%` } as CSSProperties
            }
            aria-label="显示器亮度"
            disabled={nativeControls && !systemControls.display.available}
            onChange={(event) => setBrightness(Number(event.target.value))}
            onPointerUp={(event) =>
              commitSlider("display", Number(event.currentTarget.value))
            }
            onKeyUp={(event) =>
              commitSlider("display", Number(event.currentTarget.value))
            }
          />
        </label>
        <label className="mac-control-slider" data-liquid-surface="thick">
          <span>
            <Volume2Icon className="size-4" />
            声音
          </span>
          <input
            type="range"
            min="0"
            max="100"
            value={volume}
            style={{ "--mac-control-progress": `${volume}%` } as CSSProperties}
            aria-label="系统音量"
            disabled={nativeControls && !systemControls.audio.available}
            onChange={(event) => setVolume(Number(event.target.value))}
            onPointerUp={(event) =>
              commitSlider("audio", Number(event.currentTarget.value))
            }
            onKeyUp={(event) =>
              commitSlider("audio", Number(event.currentTarget.value))
            }
          />
        </label>
        <div className="mac-control-footer">
          <span>
            <CommandIcon className="size-3.5" /> Echo OS
          </span>
          <button type="button" onClick={onOpenSettings}>
            控制中心设置…
          </button>
        </div>
      </section>
    </>
  );
}

export function MacNotificationCenter({
  open,
  onClose,
  notifications = [],
  nativeServiceAvailable = false,
  onDismiss,
  onClear,
}: {
  open: boolean;
  onClose: () => void;
  notifications?: NativeNotification[];
  nativeServiceAvailable?: boolean;
  onDismiss?: (notificationId: number) => void;
  onClear?: () => void;
}) {
  const today = new Date();
  const first = new Date(today.getFullYear(), today.getMonth(), 1).getDay();
  const days = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate();
  if (!open) return null;
  return (
    <>
      <button
        className="mac-panel-scrim"
        type="button"
        onClick={onClose}
        aria-label="关闭通知中心"
      />
      <aside className="mac-notification-center" data-desktop-interactive>
        <div className="mac-notification-date" data-liquid-surface="thin">
          <span className="mac-notification-day-row">
            <span>周{"日一二三四五六"[today.getDay()]}</span>
            {notifications.length > 0 && onClear && (
              <button type="button" onClick={onClear}>
                全部清除
              </button>
            )}
          </span>
          <strong>
            {today.getMonth() + 1}月{today.getDate()}日
          </strong>
        </div>
        <section className="mac-calendar-panel" data-liquid-surface="thick">
          <header>
            <strong>
              {today.getFullYear()}年{today.getMonth() + 1}月
            </strong>
            <span>‹　›</span>
          </header>
          <div className="mac-calendar-week">
            {"日一二三四五六".split("").map((day) => (
              <span key={day}>{day}</span>
            ))}
          </div>
          <div className="mac-calendar-days">
            {Array.from({ length: first }, (_, i) => (
              <span key={`blank-${i}`} />
            ))}
            {Array.from({ length: days }, (_, i) => (
              <span
                key={i + 1}
                className={i + 1 === today.getDate() ? "is-today" : ""}
              >
                {i + 1}
              </span>
            ))}
          </div>
        </section>
        <div className="mac-notification-list" role="list">
          {notifications.map((notification) => (
            <section
              className="mac-notification-card"
              role="listitem"
              key={notification.id}
              data-liquid-surface="thick"
            >
              <div className="mac-notification-app">
                <span>
                  <EchoMark tone="light" />
                </span>
                <strong>{notification.appName}</strong>
                <small>
                  {new Intl.DateTimeFormat("zh-CN", {
                    hour: "2-digit",
                    minute: "2-digit",
                    hour12: false,
                  }).format(new Date(notification.updatedAt))}
                </small>
                {onDismiss && (
                  <button
                    type="button"
                    className="mac-notification-dismiss"
                    onClick={() => onDismiss(notification.id)}
                    aria-label={`清除 ${notification.appName} 通知`}
                  >
                    <XIcon />
                  </button>
                )}
              </div>
              {notification.summary && <p>{notification.summary}</p>}
              {notification.body && <span>{notification.body}</span>}
            </section>
          ))}
          {notifications.length === 0 && (
            <section
              className="mac-notification-empty"
              role="status"
              data-liquid-surface="thick"
            >
              <BellIcon />
              <strong>暂无通知</strong>
              <span>
                {nativeServiceAvailable
                  ? "来自应用和系统服务的新通知会显示在这里。"
                  : "系统通知在 Echo OS 原生 Linux 会话中启用。"}
              </span>
            </section>
          )}
        </div>
      </aside>
    </>
  );
}

export function MacAboutDialog({
  open,
  onClose,
  onOpenSettings,
  agentHealth,
  updateStatus,
  updateCapabilities,
  updateBusy = false,
  onRefreshUpdate,
  onApplyUpdate,
  onRestart,
}: {
  open: boolean;
  onClose: () => void;
  onOpenSettings: () => void;
  agentHealth: AgentDesktopHealth;
  updateStatus?: SystemUpdateStatus | null;
  updateCapabilities?: SystemUpdateCapabilities | null;
  updateBusy?: boolean;
  onRefreshUpdate?: () => void;
  onApplyUpdate?: () => void;
  onRestart?: () => void;
}) {
  if (!open) return null;
  const state = updateStatus?.state || "unavailable";
  const updateCopy: Record<
    SystemUpdateStatus["state"],
    { title: string; detail: string }
  > = {
    idle: {
      title: "等待自动检查",
      detail: "系统会在联网并接通电源后检查签名更新。",
    },
    checking: {
      title: "正在检查更新",
      detail: "正在通过 HTTPS 下载并验证发布签名。",
    },
    ready: {
      title: updateStatus?.version
        ? `Echo OS ${updateStatus.version} 已认证`
        : "更新已认证",
      detail: "安装将写入未启用的系统槽，当前系统保持可启动。",
    },
    installing: {
      title: "正在安装到备用系统",
      detail: "请保持供电；完成前不会切换当前启动系统。",
    },
    "reboot-required": {
      title: updateStatus?.version
        ? `Echo OS ${updateStatus.version} 已就绪`
        : "更新已就绪",
      detail: "新系统已写入备用槽，重新启动后进行首次验证启动。",
    },
    failed: {
      title: "最近一次更新未完成",
      detail: "当前系统没有被替换；系统会保留可启动的旧槽。",
    },
    unavailable: {
      title: "原生系统更新不可用",
      detail:
        updateStatus?.error || "请在 Echo OS 原生 Linux 桌面中查看系统更新。",
    },
  };
  const copy = updateCopy[state];
  const active = state === "checking" || state === "installing" || updateBusy;
  const agentStateCopy: Record<AgentDesktopHealthState, string> = {
    checking: "正在检查",
    ready: agentHealth.verifiedBundle ? "已验证并在线" : "在线",
    "restart-required": "等待重启",
    unavailable: "未连接",
  };
  return (
    <div
      className="mac-overlay mac-dialog-overlay"
      data-desktop-interactive
      onPointerDown={onClose}
    >
      <section
        className="mac-about-dialog"
        data-liquid-surface="ultra-thick"
        onPointerDown={(event) => event.stopPropagation()}
      >
        <button
          type="button"
          className="mac-about-close"
          onClick={onClose}
          aria-label="关闭"
        >
          <span />
        </button>
        <div className="mac-about-mark">
          <EchoMark tone="light" />
        </div>
        <h1>Echo OS</h1>
        <p>Personal Agent Operating System</p>
        <dl data-liquid-surface="thick">
          <div>
            <dt>版本</dt>
            <dd>0.2.0 Beta</dd>
          </div>
          <div>
            <dt>Agent</dt>
            <dd>{agentStateCopy[agentHealth.state]}</dd>
          </div>
          <div>
            <dt>Agent 版本</dt>
            <dd>{agentHealth.version || "未知"}</dd>
          </div>
          <div>
            <dt>Agent 来源</dt>
            <dd className="mac-about-agent-source">
              {agentHealth.sourceId || "未提供"}
            </dd>
          </div>
          <div>
            <dt>来源验证</dt>
            <dd>
              {agentHealth.verifiedBundle ? "已验证镜像 bundle" : "来源未验证"}
            </dd>
          </div>
          <div>
            <dt>系统</dt>
            <dd>Debian Appliance Layer</dd>
          </div>
        </dl>
        <section
          className="mac-about-update"
          aria-label="系统更新"
          data-liquid-surface="thick"
        >
          <div className="mac-about-update-heading">
            <span className="mac-about-update-icon" aria-hidden>
              {active ? (
                <Loader2Icon className="is-spinning" />
              ) : state === "ready" || state === "reboot-required" ? (
                <CheckCircle2Icon />
              ) : (
                <RefreshCwIcon />
              )}
            </span>
            <span>
              <strong>{copy.title}</strong>
              <small>{copy.detail}</small>
            </span>
          </div>
          <div className="mac-about-update-actions">
            <button
              type="button"
              onClick={onRefreshUpdate}
              disabled={active || !updateCapabilities?.status}
            >
              刷新状态
            </button>
            {state === "ready" && (
              <button
                type="button"
                className="is-primary"
                onClick={onApplyUpdate}
                disabled={active || !updateCapabilities?.apply}
              >
                安装更新…
              </button>
            )}
            {state === "reboot-required" && (
              <button
                type="button"
                className="is-primary"
                onClick={onRestart}
                disabled={!onRestart}
              >
                重新启动…
              </button>
            )}
          </div>
        </section>
        <button
          type="button"
          className="mac-about-settings"
          onClick={onOpenSettings}
        >
          打开系统设置…
        </button>
        <small>© 2026 Echo Project</small>
      </section>
    </div>
  );
}

const SYSTEM_ACTION_COPY: Record<
  MacSystemAction,
  { title: string; detail: string; confirm: string }
> = {
  logout: {
    title: "退出当前 Echo OS 会话？",
    detail: "正在运行的应用会被关闭，并返回本地系统登录界面。",
    confirm: "退出登录",
  },
  suspend: {
    title: "让 Echo OS 进入睡眠？",
    detail: "当前会话会暂停，按电源键或键盘即可唤醒设备。",
    confirm: "睡眠",
  },
  restart: {
    title: "重新启动 Echo OS？",
    detail: "正在运行的应用会被关闭，请先保存尚未完成的工作。",
    confirm: "重新启动",
  },
  shutdown: {
    title: "关闭 Echo OS？",
    detail: "正在运行的应用会被关闭，设备需要再次按下电源键才能启动。",
    confirm: "关机",
  },
};

export function MacSystemActionDialog({
  action,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  action: MacSystemAction | null;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  if (!action) return null;
  const copy = SYSTEM_ACTION_COPY[action];
  return (
    <div
      className="mac-system-action-overlay"
      data-desktop-interactive
      onPointerDown={() => !busy && onCancel()}
    >
      <section
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="mac-system-action-title"
        className="mac-system-action-dialog"
        data-liquid-surface="ultra-thick"
        onPointerDown={(event) => event.stopPropagation()}
      >
        <div className="mac-system-action-mark">
          <EchoMark tone="light" />
        </div>
        <div className="mac-system-action-copy">
          <h2 id="mac-system-action-title">{copy.title}</h2>
          <p>{copy.detail}</p>
          {error && <p className="mac-system-action-error">{error}</p>}
        </div>
        <div className="mac-system-action-buttons">
          <button type="button" disabled={busy} onClick={onCancel}>
            取消
          </button>
          <button
            type="button"
            className="is-primary"
            disabled={busy}
            onClick={onConfirm}
          >
            {busy ? "正在执行…" : copy.confirm}
          </button>
        </div>
      </section>
    </div>
  );
}

export function MacLockedBadge() {
  return (
    <span className="mac-locked-badge">
      <LockIcon />
      安全会话
    </span>
  );
}

export function MacStatusPill() {
  return (
    <span className="mac-status-pill">
      <InfoIcon />
      本地模式
    </span>
  );
}

export const MAC_SYSTEM_APPS = {
  launchpad: {
    icon: LayoutGridIcon,
    gradient: "linear-gradient(145deg, #f7f7fa, #bfc4cf)",
  },
  settings: {
    icon: SettingsIcon,
    gradient: "linear-gradient(145deg, #7f8793, #343940)",
  },
  appStore: {
    icon: ShoppingBagIcon,
    gradient: "linear-gradient(145deg, #72b9ff, #3158d8)",
  },
  notifications: {
    icon: BellIcon,
    gradient: "linear-gradient(145deg, #ff6b6b, #d3183e)",
  },
  agent: {
    icon: BotIcon,
    gradient: "linear-gradient(145deg, #7c5cff, #2b54d6)",
  },
  disk: {
    icon: HardDriveIcon,
    gradient: "linear-gradient(145deg, #e6e9ee, #89919d)",
  },
};
