import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import { localControlPlaneProxy, localManagementProxy } from "./server/control-plane.mjs";

const backendHttpUrl = process.env.OAW_DEV_BACKEND_HTTP_URL ?? "http://127.0.0.1:8000";
const backendWsUrl = process.env.OAW_DEV_BACKEND_WS_URL ?? "ws://127.0.0.1:8000";

export default defineConfig({
  // PDF.js worker is a URL asset, not a dependency to prebundle as JavaScript.
  optimizeDeps: { exclude: ["pdfjs-dist"] },
  resolve: {
    dedupe: ["react", "react-dom"],
    alias: {
      "@oaw/plugin-api": fileURLToPath(new URL("./src/plugins/sdk.ts", import.meta.url)),
      "pdfjs-dist": fileURLToPath(new URL("./node_modules/pdfjs-dist", import.meta.url)),
      "react": fileURLToPath(new URL("./node_modules/react", import.meta.url)),
      "react-dom": fileURLToPath(new URL("./node_modules/react-dom", import.meta.url)),
    },
  },
  plugins: [localControlPlaneProxy(), react()],
  server: {
    proxy: {
      "/api": localManagementProxy(backendHttpUrl),
      "/ws": localManagementProxy(backendWsUrl, true),
    },
  },
});
