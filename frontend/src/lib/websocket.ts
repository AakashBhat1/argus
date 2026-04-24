import { useEffect, useRef, useState, useCallback } from "react";
import { getToken } from "./auth";
import type { FeedMessage } from "./api";

const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws";

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;

export function useWebSocket(channel: string) {
  const [lastMessage, setLastMessage] = useState<FeedMessage | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const token = getToken();
    if (!token) return;

    const ws = new WebSocket(`${WS_BASE}/${channel}?token=${encodeURIComponent(token)}`);

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
    connect();
    return () => {
      if (reconnectTimeout.current) clearTimeout(reconnectTimeout.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { lastMessage, isConnected };
}
