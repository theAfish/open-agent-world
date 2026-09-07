import { isIP } from "node:net";

export function isLocalManagementRequest(request) {
  const peer = request.socket.remoteAddress ?? "";
  const ipv4 = peer.startsWith("::ffff:") ? peer.slice(7) : peer;
  const loopback = peer === "::1" || (isIP(ipv4) === 4 && ipv4.startsWith("127."));
  const forwarded = Object.keys(request.headers).some((name) => (
    name.toLowerCase() === "forwarded" || name.toLowerCase() === "x-real-ip"
    || name.toLowerCase().startsWith("x-forwarded-")
  ));
  return loopback && !forwarded;
}

export function localManagementProxy(target, ws = false) {
  return {
    target, ws, changeOrigin: !ws,
    // Upgrade events have multiple listeners. Refuse in the proxy itself as
    // well as closing the incoming socket in our first upgrade listener.
    bypass(request) {
      return isLocalManagementRequest(request) ? undefined : false;
    },
  };
}

/** The local Vite proxy must not turn remote traffic into trusted loopback API calls. */
export function localControlPlaneProxy() {
  function configure(server) {
    server.middlewares.use((request, response, next) => {
      if (isLocalManagementRequest(request)) return next();
      response.statusCode = 403;
      response.setHeader("Content-Type", "application/json");
      response.end(JSON.stringify({ error: {
        code: "control_plane_access_denied",
        message: "The development server requires a local host connection.",
      } }));
    });
    // HTTP middleware does not run for WebSocket upgrades.
    server.httpServer?.prependListener("upgrade", (request, socket) => {
      if (!isLocalManagementRequest(request)) {
        socket.write("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n");
        socket.destroy();
      }
    });
  }
  return {
    name: "oaw-local-control-plane",
    configureServer: configure,
    configurePreviewServer: configure,
  };
}
