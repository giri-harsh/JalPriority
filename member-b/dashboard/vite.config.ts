import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const useMockScreens = process.env.VITE_B_USE_MOCK_A_SCREENS !== "false";
  const realScreensPath =
    process.env.VITE_B_A_SCREENS_PATH ||
    path.resolve(__dirname, "../../member-a/dashboard/src/screens/index.ts");
  const aScreensTarget = useMockScreens
    ? path.resolve(__dirname, "./src/mocks/aScreens.tsx")
    : path.resolve(__dirname, realScreensPath);

  return {
    plugins: [react()],
    resolve: {
      alias: {
        "@jp/a-screens": aScreensTarget,
      },
      dedupe: ["react", "react-dom", "react/jsx-runtime"],
    },
    server: {
      port: 5173,
      fs: {
        allow: [".", "../../member-a/dashboard"],
      },
      proxy: {
        "/b": {
          target: "http://localhost:8002",
          changeOrigin: true,
        },
        "/a": {
          target: "http://localhost:8001",
          changeOrigin: true,
        },
      },
    },
    test: {
      globals: true,
      environment: "jsdom",
      setupFiles: "./src/setupTests.ts",
      watch: false,
    },
  };
});
