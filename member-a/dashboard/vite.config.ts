import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Standalone harness (port 5174). In the integrated app, B's shell imports
// src/screens/index.ts directly and this file is not used.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      "/a": { target: "http://localhost:8001", changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: { include: [/\.module\.css$/] },
    server: { deps: { inline: ["react-leaflet", "@react-leaflet/core"] } },
  },
});
