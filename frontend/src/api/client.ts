/**
 * API client for backend communication.
 *
 * Uses centralized API configuration from lib/api.ts
 */

import { API_BASE, apiRequest, type ApiResponse } from "../lib/api";

// Re-export for backwards compatibility
export { API_BASE, apiRequest };
export type { ApiResponse };

// =============================================================================
// Chat Types
// =============================================================================

// Content block types following Anthropic API pattern
export interface TextBlock {
  type: "text";
  text: string;
}

export interface ToolUseBlock {
  type: "tool_use";
  id: string;
  name: string;
  input: Record<string, unknown>;
  result?: Record<string, unknown>;
  status?: "pending" | "running" | "complete";
}

export type ContentBlock = TextBlock | ToolUseBlock;

export interface ChatMessage {
  id: string;
  conversation_id: string | null;
  role: "user" | "assistant";
  content_blocks: ContentBlock[];
  created_at: string;
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
  access_tier?: "me" | "team" | "org" | "global";
  access_level?: "read" | "edit";
  can_edit?: boolean;
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
  type: "chat" | "workflow" | null;
  access_tier?: "me" | "team" | "org" | "global";
  access_level?: "read" | "edit";
  can_edit?: boolean;
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
  status: "idle" | "syncing" | "completed" | "failed";
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
 * List conversations for authenticated user.
 * SECURITY: User is identified from JWT token, not from parameters.
 */
export async function listConversations(
  limit = 50,
  offset = 0,
): Promise<ApiResponse<ConversationListResponse>> {
  return apiRequest<ConversationListResponse>(
    `/chat/conversations?limit=${limit}&offset=${offset}`,
  );
}

/**
 * Get a conversation with all its messages.
 * SECURITY: User is identified from JWT token.
 */
export async function getConversation(
  conversationId: string,
): Promise<ApiResponse<ConversationDetailResponse>> {
  return apiRequest<ConversationDetailResponse>(
    `/chat/conversations/${conversationId}`,
  );
}

/**
 * Create a new conversation.
 * SECURITY: User is identified from JWT token.
 */
export async function createConversation(
  title?: string,
): Promise<ApiResponse<ConversationSummary>> {
  return apiRequest<ConversationSummary>("/chat/conversations", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

/**
 * Update a conversation (title, etc.)
 * SECURITY: User is identified from JWT token.
 */
export async function updateConversation(
  conversationId: string,
  title: string,
): Promise<ApiResponse<ConversationSummary>> {
  return apiRequest<ConversationSummary>(
    `/chat/conversations/${conversationId}`,
    {
      method: "PATCH",
      body: JSON.stringify({ title }),
    },
  );
}

/**
 * Delete a conversation.
 * SECURITY: User is identified from JWT token.
 */
export async function deleteConversation(
  conversationId: string,
): Promise<ApiResponse<{ success: boolean }>> {
  return apiRequest<{ success: boolean }>(
    `/chat/conversations/${conversationId}`,
    { method: "DELETE" },
  );
}

/**
 * Get chat history for authenticated user (legacy - use getConversation instead).
 * SECURITY: User is identified from JWT token.
 */
export async function getChatHistory(
  conversationId?: string,
  limit = 50,
  offset = 0,
): Promise<ApiResponse<ChatHistoryResponse>> {
  const params = new URLSearchParams({
    limit: limit.toString(),
    offset: offset.toString(),
  });
  if (conversationId) {
    params.append("conversation_id", conversationId);
  }
  return apiRequest<ChatHistoryResponse>(`/chat/history?${params.toString()}`);
}

/**
 * Get list of integrations for an organization
 */
export async function getIntegrations(
  organizationId: string,
): Promise<ApiResponse<IntegrationsListResponse>> {
  return apiRequest<IntegrationsListResponse>(
    `/auth/integrations?organization_id=${organizationId}`,
  );
}

/**
 * Get OAuth connect URL for a provider
 */
export async function getConnectUrl(
  provider: string,
  organizationId: string,
): Promise<ApiResponse<ConnectUrlResponse>> {
  return apiRequest<ConnectUrlResponse>(
    `/auth/connect/${provider}?organization_id=${organizationId}`,
  );
}

/**
 * Disconnect an integration
 */
export async function disconnectIntegration(
  provider: string,
  organizationId: string,
  userId?: string,
): Promise<ApiResponse<{ success: boolean }>> {
  const params = new URLSearchParams({ organization_id: organizationId });
  if (userId) {
    params.set("user_id", userId);
  }
  return apiRequest<{ success: boolean }>(
    `/auth/integrations/${provider}?${params.toString()}`,
    { method: "DELETE" },
  );
}

/**
 * Trigger a sync for a provider
 */
export async function triggerSync(
  organizationId: string,
  provider: string,
): Promise<ApiResponse<SyncStatusResponse>> {
  return apiRequest<SyncStatusResponse>(`/sync/${organizationId}/${provider}`, {
    method: "POST",
  });
}

/**
 * Get sync status for a provider
 */
export async function getSyncStatus(
  organizationId: string,
  provider: string,
): Promise<ApiResponse<SyncStatusResponse>> {
  return apiRequest<SyncStatusResponse>(
    `/sync/${organizationId}/${provider}/status`,
  );
}

// =============================================================================
// Search Types
// =============================================================================

export interface DealSearchResult {
  type: "deal";
  id: string;
  name: string;
  amount: number | null;
  stage: string | null;
  close_date: string | null;
  account_name: string | null;
  owner_name: string | null;
}

export interface AccountSearchResult {
  type: "account";
  id: string;
  name: string;
  domain: string | null;
  industry: string | null;
  annual_revenue: number | null;
  deal_count: number;
}

export interface SearchResponse {
  query: string;
  deals: DealSearchResult[];
  accounts: AccountSearchResult[];
  total_deals: number;
  total_accounts: number;
}

// =============================================================================
// Search API
// =============================================================================

/**
 * Search deals and accounts
 */
export async function searchData(
  query: string,
  organizationId: string,
  limit = 10,
): Promise<ApiResponse<SearchResponse>> {
  const params = new URLSearchParams({
    q: query,
    organization_id: organizationId,
    limit: limit.toString(),
  });
  return apiRequest<SearchResponse>(`/search?${params.toString()}`);
}


export async function copyConversation(conversationId: string): Promise<ApiResponse<ConversationSummary>> {
  return apiRequest<ConversationSummary>(`/chat/conversations/${conversationId}/copy`, { method: "POST" });
}

export async function updateConversationSharing(
  conversationId: string,
  accessTier: "me" | "team" | "org" | "global",
  accessLevel: "read" | "edit",
): Promise<ApiResponse<ConversationSummary>> {
  return apiRequest<ConversationSummary>(`/chat/conversations/${conversationId}`, {
    method: "PATCH",
    body: JSON.stringify({ access_tier: accessTier, access_level: accessLevel }),
  });
}
