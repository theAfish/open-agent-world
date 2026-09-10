import { defineConfig, transformWithEsbuild } from "vite";
import { readFile } from "node:fs/promises";
import { dirname } from "node:path";
import { compileModule } from "svelte/compiler";
import react from "@vitejs/plugin-react";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { fileURLToPath, URL } from "node:url";
import { localControlPlaneProxy, localManagementProxy } from "./server/control-plane.mjs";

const backendHttpUrl = process.env.OAW_DEV_BACKEND_HTTP_URL ?? "http://127.0.0.1:8000";
const backendWsUrl = process.env.OAW_DEV_BACKEND_WS_URL ?? "ws://127.0.0.1:8000";
const threlteMeasure = fileURLToPath(new URL("./src/plugins/threlteMeasure.svelte.ts", import.meta.url));
const isThrelteMeasure = (source: string, importer?: string) =>
  source.endsWith("/useMeasure.svelte.js") && !!importer?.replaceAll("\\", "/").includes("/@threlte/core/");

export default defineConfig({
  worker: { format: "es" },
  // PDF.js worker is a URL asset, not a dependency to prebundle as JavaScript.
  optimizeDeps: { exclude: ["pdfjs-dist"], esbuildOptions: { plugins: [{
    name: "threlte-local-canvas-dimensions",
    setup(build) {
      // MatterViz is prebundled in dev; nested imports bypass Vite's resolver.
      build.onResolve({ filter: /\/useMeasure\.svelte\.js$/ }, args => {
        if (isThrelteMeasure(args.path, args.importer)) return { path: threlteMeasure, namespace: "oaw-threlte-measure" };
      });
      build.onLoad({ filter: /.*/, namespace: "oaw-threlte-measure" }, async () => {
        const source = await transformWithEsbuild(await readFile(threlteMeasure, "utf8"), threlteMeasure, { loader: "ts" });
        return { contents: compileModule(source.code, { filename: threlteMeasure, generate: "client" }).js.code,
          loader: "js", resolveDir: dirname(threlteMeasure) };
      });
    },
  }] } },
  resolve: {
    dedupe: ["react", "react-dom", "svelte", "matterviz"],
    alias: {
      "@oaw/plugin-api": fileURLToPath(new URL("./src/plugins/sdk.ts", import.meta.url)),
      "pdfjs-dist": fileURLToPath(new URL("./node_modules/pdfjs-dist", import.meta.url)),
      "@xyflow/react": fileURLToPath(new URL("./node_modules/@xyflow/react", import.meta.url)),
      "react": fileURLToPath(new URL("./node_modules/react", import.meta.url)),
      "react-dom": fileURLToPath(new URL("./node_modules/react-dom", import.meta.url)),
    },
  },
  plugins: [localControlPlaneProxy(), {
    name: "threlte-local-canvas-dimensions",
    enforce: "pre",
    resolveId(source, importer) {
      // Scope the compatibility adapter to Threlte's DOM measurement helper.
      if (isThrelteMeasure(source, importer)) return threlteMeasure;
    },
  }, react(), svelte({ configFile: false })],
  server: {
    proxy: {
      "/api": localManagementProxy(backendHttpUrl),
      "/ws": localManagementProxy(backendWsUrl, true),
    },
  },
});
