/**
 * Custom hook for WebSocket connection.
 *
 * Handles connection, reconnection, and message streaming.
 * Uses centralized API configuration.
 * 
 * Uses a callback pattern to handle messages immediately as they arrive,
 * avoiding React state batching issues.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { WS_BASE, isProduction } from '../lib/api';

interface UseWebSocketOptions {
  /** Callback called immediately for each message received */
  onMessage?: (message: string) => void;
}

interface UseWebSocketReturn {
  sendMessage: (message: string) => void;
  isConnected: boolean;
  connectionState: 'connecting' | 'connected' | 'disconnected' | 'error';
}

export function useWebSocket(url: string, options?: UseWebSocketOptions): UseWebSocketReturn {
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [connectionState, setConnectionState] = useState<
    'connecting' | 'connected' | 'disconnected' | 'error'
  >('disconnected');
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const reconnectAttemptsRef = useRef<number>(0);
  const onMessageRef = useRef(options?.onMessage);
  const maxReconnectAttempts = 5;

  // Keep onMessage ref updated
  useEffect(() => {
    onMessageRef.current = options?.onMessage;
  }, [options?.onMessage]);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    setConnectionState('connecting');

    // Build WebSocket URL using centralized config
    const wsUrl = `${WS_BASE}${url}`;
    console.log('[WebSocket] isProduction:', isProduction, 'wsUrl:', wsUrl);
    
    const ws = new WebSocket(wsUrl);

    ws.onopen = (): void => {
      console.log('WebSocket connected');
      setIsConnected(true);
      setConnectionState('connected');
      reconnectAttemptsRef.current = 0;
    };

    ws.onmessage = (event: MessageEvent<string>): void => {
      // Call the callback immediately (outside of React's render cycle)
      onMessageRef.current?.(event.data);
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

  return { sendMessage, isConnected, connectionState };
}
