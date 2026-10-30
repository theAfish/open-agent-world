import { useEffect } from "react";
import { normalizeRuntimeEvent, runtimeWebSocketUrl } from "../api/client";
import { useWorldStore } from "../state/worldStore";
import { useCardLibrary } from "../state/cardLibrary";
import type { RuntimeEvent } from "../types/world";

// The stream stays honest about liveness: a half-open TCP connection is
// detected through the ping/pong heartbeat instead of stalling silently.
const HEARTBEAT_INTERVAL_MS = 15_000;
const STALE_CONNECTION_MS = 45_000;

export function RuntimeConnection() {
  const ingestEvents = useWorldStore((state) => state.ingestEvents);
  const setSocketState = useWorldStore((state) => state.setSocketState);
  const refreshWorld = useWorldStore((state) => state.refreshWorld);

  useEffect(() => {
    let active = true;
    let socket: WebSocket | undefined;
    let retryTimer: number | undefined;
    let heartbeatTimer: number | undefined;
    let attempts = 0;
    let lastMessageAt = 0;
    let eventTimer: number | undefined;
    let pending: RuntimeEvent[] = [];
    const flushEvents = () => {
      if (eventTimer !== undefined) window.clearTimeout(eventTimer);
      eventTimer = undefined;
      if (!pending.length) return;
      const events = pending;
      pending = [];
      ingestEvents(events);
      if (events.some(event => event.type === "card_library_updated")) {
        void useCardLibrary.getState().refresh();
        void refreshWorld();
      }
    };

    const stopHeartbeat = () => {
      if (heartbeatTimer) window.clearInterval(heartbeatTimer);
      heartbeatTimer = undefined;
    };

    const connect = () => {
      if (!active) return;
      setSocketState("connecting");
      const connection = new WebSocket(runtimeWebSocketUrl());
      socket = connection;
      const isCurrent = () => active && socket === connection;
      connection.addEventListener("open", () => {
        if (!isCurrent()) return;
        attempts = 0;
        lastMessageAt = Date.now();
        setSocketState("live");
        void refreshWorld();
        void useCardLibrary.getState().refresh();
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
      connection.addEventListener("message", (event) => {
        if (!isCurrent()) return;
        lastMessageAt = Date.now();
        try {
          const payload = typeof event.data === "string" ? JSON.parse(event.data) : event.data;
          const items = Array.isArray(payload) ? payload : [payload];
          for (const item of items) {
            pending.push(normalizeRuntimeEvent(item));
            // Bound work per flush, including when a background tab throttles timers.
            if (pending.length >= 512) flushEvents();
          }
          // A timer also flushes in hidden tabs, where animation frames may stop.
          if (pending.length && eventTimer === undefined) eventTimer = window.setTimeout(flushEvents, 16);
        } catch {
          // Malformed events are ignored; the typed stream remains operational.
        }
      });
      connection.addEventListener("close", () => {
        if (!isCurrent()) return;
        flushEvents();
        stopHeartbeat();
        socket = undefined;
        setSocketState("closed");
        attempts += 1;
        retryTimer = window.setTimeout(connect, Math.min(1000 * 2 ** attempts, 12000));
      });
      connection.addEventListener("error", () => { if (isCurrent()) connection.close(); });
    };

    const online = () => {
      if (socket && socket.readyState <= WebSocket.OPEN) return;
      if (retryTimer) window.clearTimeout(retryTimer);
      connect();
    };
    const offline = () => socket?.close();
    window.addEventListener('online', online);
    window.addEventListener('offline', offline);
    connect();
    return () => {
      active = false;
      window.removeEventListener('online', online);
      window.removeEventListener('offline', offline);
      flushEvents();
      stopHeartbeat();
      if (retryTimer) window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, [ingestEvents, refreshWorld, setSocketState]);

  return null;
}
