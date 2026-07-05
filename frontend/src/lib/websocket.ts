import { useEffect, useRef, useState, useCallback } from "react";
import { getToken } from "./auth";
import type { FeedMessage } from "./api";

const WS_ENV = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws";

/** A path-only base (e.g. "/ws", used behind the nginx proxy) must be
 *  resolved against the page origin at connect time, picking wss: on
 *  https pages. The WebSocket constructor needs an absolute ws/wss URL. */
function resolveWsBase(): string {
  if (!WS_ENV.startsWith("/")) return WS_ENV;
  if (typeof window === "undefined") return WS_ENV;
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}${WS_ENV}`;
}

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;

export function useWebSocket(channel: string) {
  const [lastMessage, setLastMessage] = useState<FeedMessage | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  // Guards against zombie reconnects: closing the socket on unmount fires
  // onclose, which would otherwise schedule a new connection forever.
  const shouldReconnectRef = useRef(true);

  const connect = useCallback(() => {
    if (!shouldReconnectRef.current) return;
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const token = getToken();
    if (!token) return;

    const ws = new WebSocket(`${resolveWsBase()}/${channel}?token=${encodeURIComponent(token)}`);

    ws.onopen = () => {
      setIsConnected(true);
      attemptRef.current = 0;
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as FeedMessage;
        setLastMessage(msg);
      } catch {
        // ignore parse errors
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      if (!shouldReconnectRef.current) return;
      const delay = Math.min(
        RECONNECT_BASE_MS * Math.pow(2, attemptRef.current),
        RECONNECT_MAX_MS,
      );
      attemptRef.current += 1;
      reconnectTimeout.current = setTimeout(connect, delay);
    };

    ws.onerror = () => {
      ws.close();
    };

    wsRef.current = ws;
  }, [channel]);

  useEffect(() => {
    shouldReconnectRef.current = true;
    connect();
    return () => {
      shouldReconnectRef.current = false;
      if (reconnectTimeout.current) clearTimeout(reconnectTimeout.current);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [connect]);

  return { lastMessage, isConnected };
}
