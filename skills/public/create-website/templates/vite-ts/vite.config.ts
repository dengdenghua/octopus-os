import { defineConfig } from "vite";

// https://vitejs.dev/config/
export default defineConfig({
  server: {
    // Fixed-ish port makes it easy for Echo's preview iframe to
    // locate the dev server. Vite will auto-pick the next free port if
    // this is busy.
    port: 5173,
    strictPort: false,
  },
  build: {
    target: "es2020",
    outDir: "dist",
    sourcemap: true,
  },
});
