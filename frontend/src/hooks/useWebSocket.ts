/**
 * Custom hook for WebSocket connection.
 *
 * Handles connection, reconnection, and message streaming.
 * Uses centralized API configuration.
 * 
 * Uses a message queue to ensure no messages are lost when multiple
 * arrive in the same React render cycle.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { WS_BASE, isProduction } from '../lib/api';

interface UseWebSocketReturn {
  sendMessage: (message: string) => void;
  lastMessage: string | null;
  isConnected: boolean;
  connectionState: 'connecting' | 'connected' | 'disconnected' | 'error';
  /** All messages received since last clear, for processing multiple messages per render */
  messageQueue: string[];
  /** Clear the message queue after processing */
  clearMessageQueue: () => void;
}

export function useWebSocket(url: string): UseWebSocketReturn {
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [connectionState, setConnectionState] = useState<
    'connecting' | 'connected' | 'disconnected' | 'error'
  >('disconnected');
  const [lastMessage, setLastMessage] = useState<string | null>(null);
  const [messageQueue, setMessageQueue] = useState<string[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const reconnectAttemptsRef = useRef<number>(0);
  const maxReconnectAttempts = 5;

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
      // Add to queue AND set lastMessage for backwards compatibility
      setMessageQueue((prev) => [...prev, event.data]);
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

  const clearMessageQueue = useCallback((): void => {
    setMessageQueue([]);
  }, []);

  return { sendMessage, lastMessage, isConnected, connectionState, messageQueue, clearMessageQueue };
}
