/**
 * Custom hook for WebSocket connection.
 *
 * Handles connection, reconnection, and message streaming.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

interface UseWebSocketReturn {
  sendMessage: (message: string) => void;
  lastMessage: string | null;
  isConnected: boolean;
  connectionState: 'connecting' | 'connected' | 'disconnected' | 'error';
}

export function useWebSocket(url: string): UseWebSocketReturn {
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [connectionState, setConnectionState] = useState<
    'connecting' | 'connected' | 'disconnected' | 'error'
  >('disconnected');
  const [lastMessage, setLastMessage] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const reconnectAttemptsRef = useRef<number>(0);
  const maxReconnectAttempts = 5;

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    setConnectionState('connecting');

    // In production, use the API URL for WebSocket connection
    // In development, use current host (proxied by Vite)
    let wsUrl: string;
    const apiUrl = import.meta.env.VITE_API_URL as string | undefined;
    if (apiUrl) {
      // Convert http(s) to ws(s)
      const wsBase = apiUrl.replace(/^http/, 'ws');
      wsUrl = `${wsBase}${url}`;
    } else {
      wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}${url}`;
    }
    const ws = new WebSocket(wsUrl);

    ws.onopen = (): void => {
      console.log('WebSocket connected');
      setIsConnected(true);
      setConnectionState('connected');
      reconnectAttemptsRef.current = 0;
    };

    ws.onmessage = (event: MessageEvent<string>): void => {
      setLastMessage(event.data);
    };

    ws.onerror = (error): void => {
      console.error('WebSocket error:', error);
      setConnectionState('error');
    };

    ws.onclose = (): void => {
      console.log('WebSocket disconnected');
      setIsConnected(false);
      setConnectionState('disconnected');

      // Attempt reconnection with exponential backoff
      if (reconnectAttemptsRef.current < maxReconnectAttempts) {
        const delay = Math.min(1000 * Math.pow(2, reconnectAttemptsRef.current), 30000);
        reconnectAttemptsRef.current += 1;

        reconnectTimeoutRef.current = window.setTimeout(() => {
          console.log(`Reconnecting... (attempt ${reconnectAttemptsRef.current})`);
          connect();
        }, delay);
      }
    };

    wsRef.current = ws;
  }, [url]);

  useEffect(() => {
    connect();

    return (): void => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      wsRef.current?.close();
    };
  }, [connect]);

  const sendMessage = useCallback((message: string): void => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(message);
    } else {
      console.warn('WebSocket is not connected');
    }
  }, []);

  return { sendMessage, lastMessage, isConnected, connectionState };
}
