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
  conversation_id: string | null;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  tool_calls?: Array<{
    name: string;
    input: Record<string, unknown>;
  }>;
}

export interface ConversationSummary {
  id: string;
  user_id: string;
  title: string | null;
  summary: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message_preview: string | null;
}

export interface ConversationListResponse {
  conversations: ConversationSummary[];
  total: number;
}

export interface ConversationDetailResponse {
  id: string;
  user_id: string;
  title: string | null;
  summary: string | null;
  created_at: string;
  updated_at: string;
  messages: ChatMessage[];
}

export interface ChatHistoryResponse {
  messages: ChatMessage[];
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
 * List conversations for a user
 */
export async function listConversations(
  userId: string,
  limit = 50,
  offset = 0
): Promise<ApiResponse<ConversationListResponse>> {
  return apiRequest<ConversationListResponse>(
    `/chat/conversations?user_id=${userId}&limit=${limit}&offset=${offset}`
  );
}

/**
 * Get a conversation with all its messages
 */
export async function getConversation(
  conversationId: string,
  userId: string
): Promise<ApiResponse<ConversationDetailResponse>> {
  return apiRequest<ConversationDetailResponse>(
    `/chat/conversations/${conversationId}?user_id=${userId}`
  );
}

/**
 * Create a new conversation
 */
export async function createConversation(
  userId: string,
  title?: string
): Promise<ApiResponse<ConversationSummary>> {
  return apiRequest<ConversationSummary>('/chat/conversations', {
    method: 'POST',
    body: JSON.stringify({ user_id: userId, title }),
  });
}

/**
 * Update a conversation (title, etc.)
 */
export async function updateConversation(
  conversationId: string,
  userId: string,
  title: string
): Promise<ApiResponse<ConversationSummary>> {
  return apiRequest<ConversationSummary>(
    `/chat/conversations/${conversationId}?user_id=${userId}`,
    {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    }
  );
}

/**
 * Delete a conversation
 */
export async function deleteConversation(
  conversationId: string,
  userId: string
): Promise<ApiResponse<{ success: boolean }>> {
  return apiRequest<{ success: boolean }>(
    `/chat/conversations/${conversationId}?user_id=${userId}`,
    { method: 'DELETE' }
  );
}

/**
 * Get chat history for a user (legacy - use getConversation instead)
 */
export async function getChatHistory(
  userId: string,
  conversationId?: string,
  limit = 50,
  offset = 0
): Promise<ApiResponse<ChatHistoryResponse>> {
  const params = new URLSearchParams({
    user_id: userId,
    limit: limit.toString(),
    offset: offset.toString(),
  });
  if (conversationId) {
    params.append('conversation_id', conversationId);
  }
  return apiRequest<ChatHistoryResponse>(`/chat/history?${params.toString()}`);
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
