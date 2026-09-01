/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "url";
import { createRequire } from "module";
import path from "path";
import fs from "fs";

import {
  rejectDefeatedCodeSplitting,
  rejectOversizedJavaScriptChunk,
} from "./scripts/build-warning-policy.mjs";
import { heavyDependencyChunk } from "./scripts/chunk-policy.mjs";

const MAX_JS_CHUNK_KIB = 900;

const require = createRequire(import.meta.url);
const vitePackage = require("vite/package.json");

const gatewayTarget =
  process.env.ECHO_INTERNAL_GATEWAY_BASE_URL ||
  `http://127.0.0.1:${process.env.GATEWAY_PORT || "8000"}`;

function packageNameFromNodeModule(id: string): string | null {
  const normalized = id.replace(/\\/g, "/");
  const marker = "/node_modules/";
  const markerIndex = normalized.lastIndexOf(marker);
  if (markerIndex < 0) return null;
  const rest = normalized.slice(markerIndex + marker.length);
  const parts = rest.split("/");
  if (parts[0]?.startsWith("@")) {
    return parts.length >= 2 ? `${parts[0]}/${parts[1]}` : null;
  }
  return parts[0] || null;
}

const proxyConfig = {
  "/api/files/stream": {
    target: gatewayTarget,
    // Keep the browser's same-origin Host so the appliance trust middleware
    // can validate the request against the visible Echo OS origin.
    changeOrigin: false,
    secure: false,
    timeout: 0,
    proxyTimeout: 0,
    on: {
      proxyReq: (proxyReq: any) => {
        proxyReq.setHeader("Connection", "keep-alive");
        proxyReq.setHeader("Cache-Control", "no-cache");
      },
    },
  },
  "/api/preview/stream": {
    target: gatewayTarget,
    changeOrigin: false,
    secure: false,
    timeout: 0,
    proxyTimeout: 0,
    on: {
      proxyReq: (proxyReq: any) => {
        proxyReq.setHeader("Connection", "keep-alive");
        proxyReq.setHeader("Cache-Control", "no-cache");
      },
    },
  },
  "/api": {
    target: gatewayTarget,
    changeOrigin: false,
    secure: false,
    timeout: 0,
    proxyTimeout: 0,
    ws: true,
    on: {
      proxyReq: (proxyReq: any, req: any, _res: any) => {
        if (req.headers.accept?.includes("text/event-stream")) {
          proxyReq.setHeader("Connection", "keep-alive");
          proxyReq.setHeader("Cache-Control", "no-cache");
        }
      },
      proxyRes: (proxyRes: any, req: any, _res: any) => {
        if (
          req.headers.accept?.includes("text/event-stream") ||
          (proxyRes.headers["content-type"] || "").includes("text/event-stream")
        ) {
          proxyRes.headers["cache-control"] = "no-cache";
          proxyRes.headers["x-accel-buffering"] = "no";
        }
      },
      error: (_err: any, _req: any, res: any) => {
        if (!res.headersSent) {
          res.writeHead(502, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "proxy_error" }));
        }
      },
    },
  },
  "/v1": {
    target: gatewayTarget,
    changeOrigin: true,
    ws: true,
  },
  "/.well-known": {
    target: gatewayTarget,
    changeOrigin: true,
  },
  "/.a2a": {
    target: gatewayTarget,
    changeOrigin: true,
  },
};

function buildTracePlugin() {
  const tracePath = path.resolve("vite-transform-trace.log");
  return {
    name: "echo-build-trace",
    buildStart() {
      fs.writeFileSync(tracePath, "");
    },
    transform(_code: string, id: string) {
      fs.appendFileSync(tracePath, `${id}\n`);
      return null;
    },
  };
}

function chunkSizeGatePlugin() {
  return {
    name: "echo-chunk-size-gate",
    generateBundle(_options: unknown, bundle: Record<string, any>) {
      for (const output of Object.values(bundle)) {
        if (output.type !== "chunk") continue;
        rejectOversizedJavaScriptChunk(
          output.fileName,
          Buffer.byteLength(output.code, "utf8"),
          MAX_JS_CHUNK_KIB,
        );
      }
    },
  };
}

export default defineConfig({
  base: "./",
  define: {
    __VITE_VERSION__: JSON.stringify(vitePackage.version),
  },
  plugins: [
    ...(process.env.ECHO_BUILD_TRACE === "1" ? [buildTracePlugin()] : []),
    chunkSizeGatePlugin(),
    react(),
  ],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      "motion/react": fileURLToPath(
        new URL("./src/lib/motion-shim.tsx", import.meta.url),
      ),
      "mermaid-real": fileURLToPath(
        new URL(
          "./node_modules/mermaid/dist/mermaid.core.mjs",
          import.meta.url,
        ),
      ),
      // ``mermaid`` is aliased to a local shim because the upstream
      // package ships a large ESM bundle with worker-based parsing
      // we don't need in the workspace UI. ``resolve.alias`` covers
      // both dev and build; no pre-resolve plugin required.
      mermaid: fileURLToPath(
        new URL("./src/lib/mermaid-shim.ts", import.meta.url),
      ),
    },
  },
  server: {
    port: parseInt(process.env.FRONTEND_PORT || "3000"),
    host: "0.0.0.0",
    proxy: proxyConfig,
  },
  preview: {
    port: parseInt(process.env.FRONTEND_PORT || "3000"),
    host: "0.0.0.0",
    proxy: proxyConfig,
  },
  build: {
    outDir: "dist",
    sourcemap: process.env.ECHO_SOURCEMAP === "1" ? "hidden" : false,
    reportCompressedSize: true,
    // Heavy editors/diagram engines stay lazy and package-split below. Keep
    // this as a real regression gate: raising it can silently collapse those
    // dynamic boundaries back into multi-megabyte parse units.
    chunkSizeWarningLimit: MAX_JS_CHUNK_KIB,
    rollupOptions: {
      onwarn(warning, defaultHandler) {
        rejectDefeatedCodeSplitting(warning.code, warning.message);
        defaultHandler(warning);
      },
      output: {
        manualChunks(id) {
          const pkg = packageNameFromNodeModule(id);

          if (
            id.includes("node_modules/react-dom") ||
            id.includes("node_modules/react/") ||
            id.includes("node_modules/react-router-dom")
          ) {
            return "react-vendor";
          }
          if (id.includes("node_modules/@radix-ui/")) {
            return "ui-radix";
          }

          if (pkg === "@uiw/react-codemirror") {
            return heavyDependencyChunk(pkg);
          }
          if (pkg?.startsWith("@uiw/codemirror-theme-")) {
            return heavyDependencyChunk(pkg);
          }
          if (pkg?.startsWith("@codemirror/")) {
            // Language packages are imported on demand by codemirror-config.
            // A single vendor chunk defeats that split and makes opening any
            // editor download every language grammar.
            return heavyDependencyChunk(pkg);
          }
          if (pkg === "codemirror") {
            return heavyDependencyChunk(pkg);
          }
          if (pkg?.startsWith("@lezer/")) {
            return heavyDependencyChunk(pkg);
          }
          if (id.includes("node_modules/@tanstack/")) {
            return "query-virtual";
          }
          if (pkg === "lodash-es") {
            return "lodash-es";
          }
          if (pkg === "streamdown") {
            return "markdown-streamdown";
          }
          if (
            pkg?.startsWith("rehype-") ||
            pkg?.startsWith("remark-") ||
            pkg === "unified" ||
            pkg === "hast" ||
            pkg === "unist-util-visit"
          ) {
            return "markdown-plugins";
          }
          if (pkg === "mermaid") {
            // Mermaid uses dynamic imports for individual diagram engines.
            // Let Rollup retain those native boundaries; assigning the whole
            // package to one manual chunk collapses them into ~3 MB.
            return heavyDependencyChunk(pkg);
          }
          if (
            pkg === "cytoscape" ||
            pkg === "dagre-d3-es" ||
            pkg === "elkjs" ||
            pkg === "khroma"
          ) {
            return heavyDependencyChunk(pkg);
          }
          if (id.includes("node_modules/@xyflow/")) {
            return "xyflow";
          }
          if (id.includes("node_modules/katex/")) {
            return "katex";
          }
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    exclude: ["node_modules/**", "dist/**", "e2e/**", "scripts/**/*.test.mjs"],
  },
});
