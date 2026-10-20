import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import autoprefixer from "autoprefixer";
import { fileURLToPath, URL } from "node:url";
import { localControlPlaneProxy, localManagementProxy } from "./server/control-plane.mjs";
import { sourcePackArtifacts } from "./server/source-packs.mjs";

const backendHttpUrl = process.env.OAW_DEV_BACKEND_HTTP_URL ?? "http://127.0.0.1:8000";
const backendWsUrl = process.env.OAW_DEV_BACKEND_WS_URL ?? "ws://127.0.0.1:8000";

export default defineConfig(({ mode }) => ({
  css: { postcss: { plugins: [autoprefixer({ overrideBrowserslist: ["Chrome >= 109", "Firefox >= 115", "Safari >= 16.4"] })] } },
  optimizeDeps: { include: ["sigma", "@sigma/edge-curve", "graphology", "graphology-layout-forceatlas2", "graphology-communities-louvain", "d3-contour"] },
  resolve: {
    dedupe: ["react", "react-dom"],
    alias: [{ find: "@oaw/plugin-api", replacement: fileURLToPath(new URL("./src/plugins/sdk.ts", import.meta.url)) }],
  },
  plugins: [localControlPlaneProxy(), ...(mode === "test" ? [] : [sourcePackArtifacts()]), react()],
  server: { proxy: { "/api": localManagementProxy(backendHttpUrl), "/auth": { target: backendHttpUrl }, "/ws": localManagementProxy(backendWsUrl, true) } },
  test: { setupFiles: ["./src/test/setup.ts"] },
}));
