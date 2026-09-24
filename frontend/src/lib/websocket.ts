import { useEffect, useRef, useState, useCallback } from "react";
import { ensureFreshSession } from "./session";
import type { FeedMessage } from "./api";

const WS_ENV = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws";
// Parking channels ("parking", "parking/<cameraId>") are served by the
// parking service. Behind the gateway both share the same origin and path
// prefix, so this only differs in local development without the gateway.
const PARKING_WS_ENV = process.env.NEXT_PUBLIC_PARKING_WS_URL || WS_ENV;

/** A path-only base (e.g. "/ws", used behind the nginx proxy) must be
 *  resolved against the page origin at connect time, picking wss: on
 *  https pages. The WebSocket constructor needs an absolute ws/wss URL. */
function resolveWsBase(channel: string): string {
  const base = channel === "parking" || channel.startsWith("parking/") ? PARKING_WS_ENV : WS_ENV;
  if (!base.startsWith("/")) return base;
  if (typeof window === "undefined") return base;
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}${base}`;
}

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
/** Server close code meaning "authenticate again" (expired or invalid session). */
const AUTH_CLOSE_CODE = 4001;

/**
 * Subscribe to an authenticated Argus WebSocket channel.
 *
 * The handshake is authenticated by the httpOnly session cookie (the page
 * never sees a token). The server closes the socket with 4001 when the
 * access token expires; the hook then refreshes the session and reconnects.
 *
 * Prefer `onMessage` for reacting to events: it runs in the socket callback,
 * so consumers update state there instead of mirroring `lastMessage` into
 * state from an effect (which costs an extra render per message).
 */
export function useWebSocket(
  channel: string | null,
  onMessage?: (message: FeedMessage) => void,
) {
  const [lastMessage, setLastMessage] = useState<FeedMessage | null>(null);
  const onMessageRef = useRef(onMessage);
  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  const connectRef = useRef<() => Promise<void>>(async () => undefined);
  // Guards against zombie reconnects: closing the socket on unmount fires
  // onclose, which would otherwise schedule a new connection forever.
  const shouldReconnectRef = useRef(true);

  const connect = useCallback(async () => {
    if (!channel) return;
    if (!shouldReconnectRef.current) return;
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    if (!(await ensureFreshSession())) return;
    // The component may have unmounted, or another attempt connected, while
    // the session was being refreshed.
    if (!shouldReconnectRef.current) return;
    const current = wsRef.current;
    if (current && (current.readyState === WebSocket.CONNECTING || current.readyState === WebSocket.OPEN)) return;

    const ws = new WebSocket(`${resolveWsBase(channel)}/${channel}`);

    ws.onopen = () => {
      setIsConnected(true);
      attemptRef.current = 0;
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as FeedMessage;
        setLastMessage(msg);
        onMessageRef.current?.(msg);
      } catch {
        // ignore parse errors
      }
    };

    ws.onclose = (event) => {
      setIsConnected(false);
      if (!shouldReconnectRef.current) return;
      const schedule = () => {
        const delay = Math.min(
          RECONNECT_BASE_MS * Math.pow(2, attemptRef.current),
          RECONNECT_MAX_MS,
        );
        attemptRef.current += 1;
        reconnectTimeout.current = setTimeout(() => connectRef.current(), delay);
      };
      if (event.code === AUTH_CLOSE_CODE) {
        // The token that opened the socket expired: reconnect once a fresh
        // one exists (another request may already have refreshed it).
        void ensureFreshSession().then((ok) => {
          if (ok && shouldReconnectRef.current) schedule();
        });
        return;
      }
      schedule();
    };

    ws.onerror = () => {
      ws.close();
    };

    wsRef.current = ws;
  }, [channel]);

  useEffect(() => {
    connectRef.current = connect;
    shouldReconnectRef.current = true;
    void connect();
    return () => {
      shouldReconnectRef.current = false;
      if (reconnectTimeout.current) clearTimeout(reconnectTimeout.current);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [connect]);

  return { lastMessage, isConnected };
}
