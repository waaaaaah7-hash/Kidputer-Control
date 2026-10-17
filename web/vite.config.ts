/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import pkg from "./package.json" with { type: "json" };

export default defineConfig({
  plugins: [react()],
  // Shown on the Help page, so parents can say which version they have.
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  server: {
    // `npm run dev` talks to a local API (docker compose up) on the same origin via this proxy.
    proxy: { "/api": "http://localhost:8000" },
  },
  build: { outDir: "dist", sourcemap: false },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test-setup.ts"],
  },
});
