import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backendHttpUrl = process.env.OAW_DEV_BACKEND_HTTP_URL ?? "http://127.0.0.1:8000";
const backendWsUrl = process.env.OAW_DEV_BACKEND_WS_URL ?? "ws://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": backendHttpUrl,
      "/ws": { target: backendWsUrl, ws: true },
    },
  },
});
