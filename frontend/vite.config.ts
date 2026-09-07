import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

const backendHttpUrl = process.env.OAW_DEV_BACKEND_HTTP_URL ?? "http://127.0.0.1:8000";
const backendWsUrl = process.env.OAW_DEV_BACKEND_WS_URL ?? "ws://127.0.0.1:8000";

export default defineConfig({
  resolve: {
    dedupe: ["react", "react-dom"],
    alias: {
      "@oaw/plugin-api": fileURLToPath(new URL("./src/plugins/sdk.ts", import.meta.url)),
      "react": fileURLToPath(new URL("./node_modules/react", import.meta.url)),
      "react-dom": fileURLToPath(new URL("./node_modules/react-dom", import.meta.url)),
    },
  },
  plugins: [react()],
  server: {
    proxy: {
      "/api": backendHttpUrl,
      "/ws": { target: backendWsUrl, ws: true },
    },
  },
});
