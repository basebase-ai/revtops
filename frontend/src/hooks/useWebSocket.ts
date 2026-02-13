/**
 * Custom hook for WebSocket connection.
 *
 * Handles connection, reconnection, and message streaming.
 * Uses centralized API configuration.
 * 
 * SECURITY: Authenticates WebSocket connections using JWT token from
 * Supabase session, passed as a query parameter. The backend verifies
 * this token to authenticate the user.
 * 
 * Uses a callback pattern to handle messages immediately as they arrive,
 * avoiding React state batching issues.
 * 
 * Supports the new subscription-based protocol for background tasks:
 * - On connect: receives active_tasks with all running tasks
 * - task_started, task_chunk, task_complete for task lifecycle
 * - catchup for reconnection
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { WS_BASE, isProduction } from '../lib/api';
import { supabase } from '../lib/supabase';

interface UseWebSocketOptions {
  /** Callback called immediately for each message received */
  onMessage?: (message: string) => void;
  /** Callback called when connection is established */
  onConnect?: () => void;
  /** Callback called when connection is lost */
  onDisconnect?: () => void;
}

interface UseWebSocketReturn {
  sendMessage: (message: string) => void;
  sendJson: (data: Record<string, unknown>) => void;
  isConnected: boolean;
  connectionState: 'connecting' | 'connected' | 'disconnected' | 'error';
  reconnect: () => void;
}

export function useWebSocket(path: string, options?: UseWebSocketOptions, reconnectKey?: string): UseWebSocketReturn {
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [connectionState, setConnectionState] = useState<
    'connecting' | 'connected' | 'disconnected' | 'error'
  >('disconnected');
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const reconnectAttemptsRef = useRef<number>(0);
  const onMessageRef = useRef(options?.onMessage);
  const onConnectRef = useRef(options?.onConnect);
  const onDisconnectRef = useRef(options?.onDisconnect);
  const maxReconnectAttempts = 10; // Increased for persistent connections

  // Keep refs updated
  useEffect(() => {
    onMessageRef.current = options?.onMessage;
    onConnectRef.current = options?.onConnect;
    onDisconnectRef.current = options?.onDisconnect;
  }, [options?.onMessage, options?.onConnect, options?.onDisconnect]);

  const connect = useCallback(async () => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    // Close any existing connection
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    setConnectionState('connecting');

    // Get authentication token from Supabase session
    const { data: { session } } = await supabase.auth.getSession();
    const token = session?.access_token;
    
    if (!token) {
      console.error('[WebSocket] No authentication token available');
      setConnectionState('error');
      return;
    }

    // Build WebSocket URL with authentication token
    const wsUrl = `${WS_BASE}${path}?token=${encodeURIComponent(token)}`;
    console.log('[WebSocket] Connecting:', isProduction ? 'production' : 'dev', `${WS_BASE}${path}`);
    
    const ws = new WebSocket(wsUrl);

    ws.onopen = (): void => {
      console.log('[WebSocket] Connected');
      setIsConnected(true);
      setConnectionState('connected');
      reconnectAttemptsRef.current = 0;
      onConnectRef.current?.();
    };

    ws.onmessage = (event: MessageEvent<string>): void => {
      // Call the callback immediately (outside of React's render cycle)
      onMessageRef.current?.(event.data);
    };

    ws.onerror = (error): void => {
      console.error('[WebSocket] Error:', error);
      setConnectionState('error');
    };

    ws.onclose = (event): void => {
      console.log('[WebSocket] Disconnected:', event.code, event.reason);
      setIsConnected(false);
      setConnectionState('disconnected');
      wsRef.current = null;
      onDisconnectRef.current?.();

      // Attempt reconnection with exponential backoff
      // Skip reconnection on auth errors (4001)
      if (event.code === 4001) {
        console.error('[WebSocket] Authentication failed, not reconnecting');
        return;
      }
      
      if (reconnectAttemptsRef.current < maxReconnectAttempts) {
        const delay = Math.min(1000 * Math.pow(2, reconnectAttemptsRef.current), 30000);
        reconnectAttemptsRef.current += 1;

        console.log(`[WebSocket] Reconnecting in ${delay}ms (attempt ${reconnectAttemptsRef.current})`);
        reconnectTimeoutRef.current = window.setTimeout(() => {
          void connect();
        }, delay);
      } else {
        console.error('[WebSocket] Max reconnection attempts reached');
      }
    };

    wsRef.current = ws;
  }, [path, reconnectKey]);

  const reconnect = useCallback(() => {
    reconnectAttemptsRef.current = 0;
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    void connect();
  }, [connect]);

  useEffect(() => {
    void connect();

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
      console.warn('[WebSocket] Not connected, cannot send message');
    }
  }, []);

  const sendJson = useCallback((data: Record<string, unknown>): void => {
    sendMessage(JSON.stringify(data));
  }, [sendMessage]);

  return { sendMessage, sendJson, isConnected, connectionState, reconnect };
}
