import { useEffect } from "react";
import { normalizeRuntimeEvent, runtimeWebSocketUrl } from "../api/client";
import { useWorldStore } from "../state/worldStore";

export function RuntimeConnection() {
  const ingestEvent = useWorldStore((state) => state.ingestEvent);
  const setSocketState = useWorldStore((state) => state.setSocketState);
  const refreshWorld = useWorldStore((state) => state.refreshWorld);

  useEffect(() => {
    let active = true;
    let socket: WebSocket | undefined;
    let retryTimer: number | undefined;
    let attempts = 0;

    const connect = () => {
      if (!active) return;
      setSocketState("connecting");
      socket = new WebSocket(runtimeWebSocketUrl());
      socket.addEventListener("open", () => {
        attempts = 0;
        setSocketState("live");
        void refreshWorld();
      });
      socket.addEventListener("message", (event) => {
        try {
          const payload = typeof event.data === "string" ? JSON.parse(event.data) : event.data;
          if (Array.isArray(payload)) payload.forEach((item) => ingestEvent(normalizeRuntimeEvent(item)));
          else ingestEvent(normalizeRuntimeEvent(payload));
        } catch {
          // Malformed events are ignored; the typed stream remains operational.
        }
      });
      socket.addEventListener("close", () => {
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
      if (retryTimer) window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, [ingestEvent, refreshWorld, setSocketState]);

  return null;
}
