import { useEffect } from "react";
import { normalizeRuntimeEvent, runtimeWebSocketUrl } from "../api/client";
import { useWorldStore } from "../state/worldStore";

// The stream stays honest about liveness: a half-open TCP connection is
// detected through the ping/pong heartbeat instead of stalling silently.
const HEARTBEAT_INTERVAL_MS = 15_000;
const STALE_CONNECTION_MS = 45_000;

export function RuntimeConnection() {
  const ingestEvent = useWorldStore((state) => state.ingestEvent);
  const setSocketState = useWorldStore((state) => state.setSocketState);
  const refreshWorld = useWorldStore((state) => state.refreshWorld);
  const restoreModelConnection = useWorldStore((state) => state.restoreModelConnection);

  useEffect(() => {
    let active = true;
    let socket: WebSocket | undefined;
    let retryTimer: number | undefined;
    let heartbeatTimer: number | undefined;
    let attempts = 0;
    let lastMessageAt = 0;

    const stopHeartbeat = () => {
      if (heartbeatTimer) window.clearInterval(heartbeatTimer);
      heartbeatTimer = undefined;
    };

    const connect = () => {
      if (!active) return;
      setSocketState("connecting");
      socket = new WebSocket(runtimeWebSocketUrl());
      socket.addEventListener("open", () => {
        attempts = 0;
        lastMessageAt = Date.now();
        setSocketState("live");
        void refreshWorld();
        void restoreModelConnection();
        stopHeartbeat();
        heartbeatTimer = window.setInterval(() => {
          if (!socket || socket.readyState !== WebSocket.OPEN) return;
          if (Date.now() - lastMessageAt > STALE_CONNECTION_MS) {
            // No traffic despite pings: the connection is dead. Close it so the
            // regular retry path reconnects and resynchronizes state.
            socket.close();
            return;
          }
          socket.send("ping");
        }, HEARTBEAT_INTERVAL_MS);
      });
      socket.addEventListener("message", (event) => {
        lastMessageAt = Date.now();
        try {
          const payload = typeof event.data === "string" ? JSON.parse(event.data) : event.data;
          const items = Array.isArray(payload) ? payload : [payload];
          for (const item of items) {
            const normalized = normalizeRuntimeEvent(item);
            // Heartbeat pongs only prove liveness; keep them out of the event log.
            if (normalized.type === "connection_ready" && normalized.payload.message === "pong") continue;
            ingestEvent(normalized);
          }
        } catch {
          // Malformed events are ignored; the typed stream remains operational.
        }
      });
      socket.addEventListener("close", () => {
        stopHeartbeat();
        if (!active) return;
        setSocketState("closed");
        attempts += 1;
        retryTimer = window.setTimeout(connect, Math.min(1000 * 2 ** attempts, 12000));
      });
      socket.addEventListener("error", () => socket?.close());
    };

    connect();
    return () => {
      active = false;
      stopHeartbeat();
      if (retryTimer) window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, [ingestEvent, refreshWorld, restoreModelConnection, setSocketState]);

  return null;
}
