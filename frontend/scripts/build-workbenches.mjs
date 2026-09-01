import { build } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import path from "node:path";

const frontendDir = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const repositoryDir = path.resolve(frontendDir, "..");
const sourceDir = path.join(frontendDir, "src");
const applications = [
  "paper-trading",
  "design",
  "narrative_studio",
  "self_evolution",
  "intelligence",
  "community",
];

const aliases = [
  { find: "@", replacement: sourceDir },
  {
    find: "motion/react",
    replacement: path.join(sourceDir, "lib/motion-shim.tsx"),
  },
  {
    find: "mermaid-real",
    replacement: path.join(
      frontendDir,
      "node_modules/mermaid/dist/mermaid.esm.min.mjs",
    ),
  },
  { find: "mermaid", replacement: path.join(sourceDir, "lib/mermaid-shim.ts") },
  { find: /^shiki$/, replacement: path.join(sourceDir, "lib/shiki-shim.ts") },
];

for (const application of applications) {
  const applicationRoot = path.join(frontendDir, "workbenches", application);
  await build({
    configFile: false,
    root: applicationRoot,
    base: "./",
    plugins: [react()],
    resolve: { alias: aliases },
    css: { postcss: path.join(frontendDir, "postcss.config.js") },
    build: {
      outDir: path.join(
        repositoryDir,
        "extensions/workbench-apps",
        application,
        "dist",
      ),
      emptyOutDir: true,
      sourcemap: process.env.ECHO_SOURCEMAP === "1" ? "hidden" : false,
      reportCompressedSize: true,
      chunkSizeWarningLimit: 1_400,
    },
  });
}
