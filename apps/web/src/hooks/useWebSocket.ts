import { useEffect, useRef, useState } from "react";
import type { LatestReading, RelayStatus } from "../api";

type WebSocketMessage =
  | { type: "latest"; data: LatestReading }
  | { type: "relays"; data: RelayStatus[] };

interface UseWebSocketOptions {
  onLatestReading?: (reading: LatestReading) => void;
  onRelayUpdate?: (relays: RelayStatus[]) => void;
  onError?: (error: Event) => void;
  reconnectInterval?: number;
}

export function useWebSocket(options: UseWebSocketOptions = {}) {
  const {
    onLatestReading,
    onRelayUpdate,
    onError,
    reconnectInterval = 3000,
  } = options;

  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const shouldConnectRef = useRef(true);
  const connectionTimeoutRef = useRef<number | null>(null);

  const connect = () => {
    if (!shouldConnectRef.current) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    console.log(`Attempting WebSocket connection to ${wsUrl}`);

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      // Set connection timeout (10 seconds)
      connectionTimeoutRef.current = window.setTimeout(() => {
        if (ws.readyState !== WebSocket.OPEN) {
          console.error("WebSocket connection timeout");
          ws.close();
        }
      }, 10000);

      ws.onopen = () => {
        console.log("WebSocket connected successfully");
        setIsConnected(true);
        if (connectionTimeoutRef.current) {
          window.clearTimeout(connectionTimeoutRef.current);
          connectionTimeoutRef.current = null;
        }
        if (reconnectTimeoutRef.current) {
          window.clearTimeout(reconnectTimeoutRef.current);
          reconnectTimeoutRef.current = null;
        }
      };

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data) as WebSocketMessage;

          if (message.type === "latest" && onLatestReading) {
            onLatestReading(message.data);
          } else if (message.type === "relays" && onRelayUpdate) {
            onRelayUpdate(message.data);
          }
        } catch (error) {
          console.error("Error parsing WebSocket message:", error);
        }
      };

      ws.onerror = (error) => {
        console.error("WebSocket error:", error);
        onError?.(error);
      };

      ws.onclose = () => {
        console.log("WebSocket disconnected");
        setIsConnected(false);
        wsRef.current = null;

        if (connectionTimeoutRef.current) {
          window.clearTimeout(connectionTimeoutRef.current);
          connectionTimeoutRef.current = null;
        }

        // Attempt to reconnect
        if (shouldConnectRef.current && !reconnectTimeoutRef.current) {
          console.log(`Reconnecting in ${reconnectInterval}ms...`);
          reconnectTimeoutRef.current = window.setTimeout(() => {
            reconnectTimeoutRef.current = null;
            connect();
          }, reconnectInterval);
        }
      };
    } catch (error) {
      console.error("Error creating WebSocket:", error);
      setIsConnected(false);
      if (connectionTimeoutRef.current) {
        window.clearTimeout(connectionTimeoutRef.current);
        connectionTimeoutRef.current = null;
      }
    }
  };

  useEffect(() => {
    shouldConnectRef.current = true;
    connect();

    return () => {
      shouldConnectRef.current = false;
      if (connectionTimeoutRef.current) {
        window.clearTimeout(connectionTimeoutRef.current);
        connectionTimeoutRef.current = null;
      }
      if (reconnectTimeoutRef.current) {
        window.clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, []);

  return { isConnected };
}
