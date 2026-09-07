import { createServer as createHttpServer, request as httpRequest } from "node:http";
import { networkInterfaces } from "node:os";
import { describe, expect, it } from "vitest";
import { createServer as createViteServer } from "vite";
import { isLocalManagementRequest, localControlPlaneProxy, localManagementProxy } from "../../server/control-plane.mjs";

const request = (peer, headers = {}) => ({
  socket: { remoteAddress: peer }, headers,
});

describe("local development proxy control-plane ingress", () => {
  it.each(["127.0.0.1", "127.0.0.2", "::1", "::ffff:127.0.0.1"])("accepts local peer %s", (peer) => {
    expect(isLocalManagementRequest(request(peer))).toBe(true);
  });

  it.each([undefined, "localhost", "testclient", "192.168.1.5", "10.0.2.2", "10.0.2.3", "8.8.8.8", "::ffff:192.168.1.5", "2001:4860:4860::8888", "127.example.com"])("rejects untrusted peer %s regardless of Host", (peer) => {
    expect(isLocalManagementRequest(request(peer, { host: "localhost:5173" }))).toBe(false);
  });

  it.each(["forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip"])("rejects %s rather than laundering forwarded requests", (name) => {
    expect(isLocalManagementRequest(request("127.0.0.1", { [name]: "127.0.0.1" }))).toBe(false);
    expect(isLocalManagementRequest(request("192.168.1.5", { [name]: "127.0.0.1" }))).toBe(false);
  });

  it("rejects real remote HTTP and WebSocket sockets before proxying", async (context) => {
    const address = Object.values(networkInterfaces()).flat().find((item) => item?.family === "IPv4" && !item.internal)?.address;
    if (!address) return context.skip();
    let apiCalls = 0;
    let upgrades = 0;
    const upstream = createHttpServer((_request, response) => {
      apiCalls += 1;
      response.end("ok");
    });
    upstream.on("upgrade", (_request, socket) => {
      upgrades += 1;
      socket.destroy();
    });
    await new Promise((resolve) => upstream.listen(0, "127.0.0.1", resolve));
    const upstreamUrl = `http://127.0.0.1:${upstream.address().port}`;
    const reservation = createHttpServer();
    await new Promise((resolve) => reservation.listen(0, "0.0.0.0", resolve));
    const listenPort = reservation.address().port;
    await new Promise((resolve) => reservation.close(resolve));
    const vite = await createViteServer({
      configFile: false, appType: "custom", plugins: [localControlPlaneProxy()],
      optimizeDeps: { noDiscovery: true, include: [] },
      server: { host: "0.0.0.0", port: listenPort, strictPort: true, proxy: {
        "/api": localManagementProxy(upstreamUrl), "/ws": localManagementProxy(upstreamUrl, true),
      } },
    });
    const status = (url, headers = {}) => new Promise((resolve, reject) => {
      const outgoing = httpRequest(url, { headers }, (incoming) => {
        incoming.resume();
        resolve(incoming.statusCode ?? 0);
      });
      outgoing.on("error", reject);
      outgoing.end();
    });
    try {
      await vite.listen();
      const port = vite.httpServer.address().port;
      expect(await status(upstreamUrl)).toBe(200);
      expect(vite.config.server.proxy["/api"].target).toBe(upstreamUrl);
      expect(await status(`http://127.0.0.1:${port}/api/test`)).toBe(200);
      expect(apiCalls).toBe(2);
      expect(await status(`http://${address}:${port}/api/test`)).toBe(403);
      expect(await status(`http://${address}:${port}/api/test`, { "X-Forwarded-For": "127.0.0.1" })).toBe(403);
      expect(await status(`http://${address}:${port}/ws/events`, {
        Connection: "Upgrade", Upgrade: "websocket", "Sec-WebSocket-Version": "13", "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
      })).toBe(403);
      expect(apiCalls).toBe(2);
      expect(upgrades).toBe(0);
    } finally {
      await vite.close();
      await new Promise((resolve, reject) => upstream.close((error) => error ? reject(error) : resolve()));
    }
  }, 15_000);
});
