/**
 * API client for backend communication.
 * 
 * Uses centralized API configuration from lib/api.ts
 */

import { API_BASE, apiRequest, type ApiResponse } from '../lib/api';

// Re-export for backwards compatibility
export { API_BASE, apiRequest };
export type { ApiResponse };

// =============================================================================
// Chat Types
// =============================================================================

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  tool_calls?: Array<{
    name: string;
    input: Record<string, unknown>;
  }>;
}

export interface ChatHistoryResponse {
  messages: ChatMessage[];
  total: number;
}

// =============================================================================
// Integration Types
// =============================================================================

export interface IntegrationStatus {
  id: string;
  provider: string;
  is_active: boolean;
  last_sync_at: string | null;
  last_error: string | null;
  connected_at: string | null;
}

export interface IntegrationsListResponse {
  integrations: IntegrationStatus[];
}

export interface ConnectUrlResponse {
  connect_url: string;
  provider: string;
}

// =============================================================================
// Sync Types
// =============================================================================

export interface SyncStatusResponse {
  status: 'idle' | 'syncing' | 'completed' | 'failed';
  provider: string;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  records_synced: number;
}

// =============================================================================
// API Functions
// =============================================================================

/**
 * Get chat history for a user
 */
export async function getChatHistory(
  userId: string,
  limit = 50,
  offset = 0
): Promise<ApiResponse<ChatHistoryResponse>> {
  return apiRequest<ChatHistoryResponse>(
    `/chat/history?user_id=${userId}&limit=${limit}&offset=${offset}`
  );
}

/**
 * Get list of integrations for an organization
 */
export async function getIntegrations(
  organizationId: string
): Promise<ApiResponse<IntegrationsListResponse>> {
  return apiRequest<IntegrationsListResponse>(
    `/auth/integrations?organization_id=${organizationId}`
  );
}

/**
 * Get OAuth connect URL for a provider
 */
export async function getConnectUrl(
  provider: string,
  organizationId: string
): Promise<ApiResponse<ConnectUrlResponse>> {
  return apiRequest<ConnectUrlResponse>(
    `/auth/connect/${provider}?organization_id=${organizationId}`
  );
}

/**
 * Disconnect an integration
 */
export async function disconnectIntegration(
  provider: string,
  organizationId: string
): Promise<ApiResponse<{ success: boolean }>> {
  return apiRequest<{ success: boolean }>(
    `/auth/integrations/${provider}?organization_id=${organizationId}`,
    { method: 'DELETE' }
  );
}

/**
 * Trigger a sync for a provider
 */
export async function triggerSync(
  organizationId: string,
  provider: string
): Promise<ApiResponse<SyncStatusResponse>> {
  return apiRequest<SyncStatusResponse>(
    `/sync/${organizationId}/${provider}`,
    { method: 'POST' }
  );
}

/**
 * Get sync status for a provider
 */
export async function getSyncStatus(
  organizationId: string,
  provider: string
): Promise<ApiResponse<SyncStatusResponse>> {
  return apiRequest<SyncStatusResponse>(
    `/sync/${organizationId}/${provider}/status`
  );
}
