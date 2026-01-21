/**
 * API client for backend communication.
 */

// In production, VITE_API_URL points to the backend service
// In development, we use '/api' which is proxied by Vite to localhost:8000
const API_BASE = import.meta.env.VITE_API_URL 
  ? `${import.meta.env.VITE_API_URL}/api`
  : '/api';

interface ApiResponse<T> {
  data: T | null;
  error: string | null;
}

async function request<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<ApiResponse<T>> {
  const userId = localStorage.getItem('user_id');

  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...options.headers,
  };

  try {
    const url = new URL(`${API_BASE}${endpoint}`, window.location.origin);
    if (userId && !url.searchParams.has('user_id')) {
      url.searchParams.set('user_id', userId);
    }

    const response = await fetch(url.toString(), {
      ...options,
      headers,
    });

    if (!response.ok) {
      const errorData = (await response.json().catch(() => ({}))) as {
        detail?: string;
      };
      return {
        data: null,
        error: errorData.detail ?? `HTTP ${response.status}`,
      };
    }

    const data = (await response.json()) as T;
    return { data, error: null };
  } catch (error) {
    return {
      data: null,
      error: error instanceof Error ? error.message : 'Unknown error',
    };
  }
}

// =============================================================================
// Auth & Integration API
// =============================================================================

export interface UserInfo {
  id: string;
  email: string;
  name: string | null;
  role: string | null;
  customer_id: string | null;
}

export interface Integration {
  id: string;
  provider: string;
  is_active: boolean;
  last_sync_at: string | null;
  last_error: string | null;
  connected_at: string | null;
}

export interface AvailableIntegration {
  id: string;
  name: string;
  description: string;
}

export interface ConnectUrlResponse {
  connect_url: string;
  provider: string;
}

export async function getCurrentUser(): Promise<ApiResponse<UserInfo>> {
  return request<UserInfo>('/auth/me');
}

export async function registerUser(
  email: string,
  name?: string,
  companyName?: string
): Promise<ApiResponse<{ user_id: string; customer_id: string }>> {
  return request<{ user_id: string; customer_id: string }>('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email, name, company_name: companyName }),
  });
}

export async function logout(): Promise<ApiResponse<{ status: string }>> {
  return request<{ status: string }>('/auth/logout', {
    method: 'POST',
  });
}

export async function getAvailableIntegrations(): Promise<
  ApiResponse<{ integrations: AvailableIntegration[] }>
> {
  return request<{ integrations: AvailableIntegration[] }>(
    '/auth/available-integrations'
  );
}

export async function getConnectedIntegrations(): Promise<
  ApiResponse<{ integrations: Integration[] }>
> {
  return request<{ integrations: Integration[] }>('/auth/integrations');
}

export async function getConnectUrl(
  provider: string
): Promise<ApiResponse<ConnectUrlResponse>> {
  return request<ConnectUrlResponse>(`/auth/connect/${provider}`);
}

export async function recordIntegrationCallback(
  provider: string,
  connectionId: string
): Promise<ApiResponse<{ status: string; provider: string }>> {
  const userId = localStorage.getItem('user_id');
  return request<{ status: string; provider: string }>(
    `/auth/callback?provider=${provider}&connection_id=${connectionId}&user_id=${userId}`,
    { method: 'POST' }
  );
}

export async function disconnectIntegration(
  provider: string
): Promise<ApiResponse<{ status: string; provider: string }>> {
  return request<{ status: string; provider: string }>(
    `/auth/integrations/${provider}`,
    { method: 'DELETE' }
  );
}

// =============================================================================
// Chat API
// =============================================================================

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
}

export interface ChatHistoryResponse {
  messages: ChatMessage[];
}

export async function getChatHistory(
  userId: string,
  limit = 50,
  offset = 0
): Promise<ApiResponse<ChatHistoryResponse>> {
  return request<ChatHistoryResponse>(`/chat/history?user_id=${userId}&limit=${limit}&offset=${offset}`);
}

// =============================================================================
// Sync API
// =============================================================================

export interface SyncStatus {
  customer_id: string;
  provider: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  counts: Record<string, number> | null;
}

export async function triggerSync(
  customerId: string,
  provider: string
): Promise<ApiResponse<{ status: string; customer_id: string; provider: string }>> {
  return request<{ status: string; customer_id: string; provider: string }>(
    `/sync/${customerId}/${provider}`,
    { method: 'POST' }
  );
}

export async function triggerSyncAll(
  customerId: string
): Promise<ApiResponse<{ status: string; customer_id: string; integrations: string[] }>> {
  return request<{ status: string; customer_id: string; integrations: string[] }>(
    `/sync/${customerId}/all`,
    { method: 'POST' }
  );
}

export async function getSyncStatus(
  customerId: string,
  provider: string
): Promise<ApiResponse<SyncStatus>> {
  return request<SyncStatus>(`/sync/${customerId}/${provider}/status`);
}
