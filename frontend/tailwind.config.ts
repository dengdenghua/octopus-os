import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Echo palette: deep blue-purple base with cyan and violet accents, matching the embedded dashboard.
        ink: {
          950: "#070a10",
          900: "#0a0e14",
          800: "#111721",
          700: "#1a2433",
          600: "#1f2733",
          500: "#2a3444",
        },
        cephalo: {
          // Echo violet.
          50: "#f3f0ff",
          100: "#e9e4ff",
          200: "#d4caff",
          300: "#b5a4ff",
          400: "#9775ff",
          500: "#7a4dff",
          600: "#5f2df0",
          700: "#4c1fd3",
          800: "#3f1cae",
          900: "#351c8a",
        },
        sucker: {
          // cyan accent
          300: "#7ee2f5",
          400: "#38bdf8",
          500: "#0ea5e9",
          600: "#0284c7",
        },
        ok: "#a6e3a1",
        warn: "#f9e2af",
        bad: "#f38ba8",
        mute: "#6e7278",
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Helvetica",
          "Arial",
          "sans-serif",
        ],
        mono: ["SF Mono", "JetBrains Mono", "Consolas", "monospace"],
      },
      fontSize: {
        "2xs": ["11px", "14px"],
      },
      animation: {
        "pulse-slow": "pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "fade-in": "fadeIn 0.2s ease-in",
      },
      keyframes: {
        fadeIn: {
          "0%": { opacity: "0", transform: "translateY(4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
