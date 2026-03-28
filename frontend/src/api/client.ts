/**
 * API client for backend communication.
 *
 * Uses centralized API configuration from lib/api.ts
 */

import { API_BASE, apiRequest, getAuthenticatedRequestHeaders, type ApiResponse } from "../lib/api";

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
  user_id?: string | null;
  sender_name?: string | null;
  sender_email?: string | null;
  sender_avatar_url?: string | null;
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
  scope?: "private" | "shared";
  participants?: Array<{ id: string; name: string | null; email: string; avatar_url?: string | null }>;
  match_snippet?: string | null;
}

export interface ConversationListResponse {
  conversations: ConversationSummary[];
  total: number;
  search_term?: string | null;
}

export interface ConversationDetailResponse {
  id: string;
  user_id: string;
  title: string | null;
  summary: string | null;
  created_at: string;
  updated_at: string;
  type: "chat" | "workflow" | null;
  scope: "private" | "shared";
  agent_responding?: boolean;
  participants: Array<{
    id: string;
    name: string | null;
    email: string;
    avatar_url?: string | null;
  }>;
  messages: ChatMessage[];
  has_more: boolean;
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

/** Matches GET /sync/{organization_id}/{provider}/status */
export interface SyncStatusResponse {
  organization_id: string;
  provider: string;
  status: "syncing" | "failed" | "completed" | "never_synced";
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  counts: Record<string, number> | null;
}

/** Matches POST /sync/{organization_id}/{provider} */
export interface SyncTriggerResponse {
  status: string;
  organization_id: string;
  provider: string;
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
  scope?: 'shared' | 'private' | 'mine',
  search?: string,
): Promise<ApiResponse<ConversationListResponse>> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (scope === 'shared' || scope === 'private') {
    params.set('scope', scope);
  }
  if (scope === 'mine') {
    params.set('mine', 'true');
  }
  if (search?.trim()) {
    params.set('search', search.trim());
  }
  return apiRequest<ConversationListResponse>(
    `/chat/conversations?${params.toString()}`,
  );
}

/**
 * Get a conversation with its messages (paginated, most recent first).
 * SECURITY: User is identified from JWT token.
 *
 * @param conversationId - The conversation to fetch.
 * @param options.limit  - Max messages to return (default 30).
 * @param options.before - ISO timestamp cursor — fetch messages older than this.
 */
export async function getConversation(
  conversationId: string,
  options?: { limit?: number; before?: string },
): Promise<ApiResponse<ConversationDetailResponse>> {
  const params = new URLSearchParams();
  if (options?.limit !== undefined) {
    params.set("limit", options.limit.toString());
  }
  if (options?.before) {
    params.set("before", options.before);
  }
  const qs = params.toString();
  return apiRequest<ConversationDetailResponse>(
    `/chat/conversations/${conversationId}${qs ? `?${qs}` : ""}`,
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
): Promise<ApiResponse<SyncTriggerResponse>> {
  return apiRequest<SyncTriggerResponse>(`/sync/${organizationId}/${provider}`, {
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
// File Upload
// =============================================================================

export interface UploadResponse {
  upload_id: string;
  filename: string;
  mime_type: string;
  size: number;
}

/**
 * Upload a file attachment for chat context.
 * Uses multipart/form-data (not JSON) so we bypass apiRequest.
 */
export async function uploadChatFile(
  file: File,
): Promise<ApiResponse<UploadResponse>> {
  const authHeaders = await getAuthenticatedRequestHeaders();
  if (!authHeaders.Authorization) {
    return { data: null, error: "Not authenticated" };
  }

  const formData = new FormData();
  formData.append("file", file);

  // Remove Content-Type so browser sets multipart boundary automatically
  const { "Content-Type": _contentType, ...uploadHeaders } = authHeaders as Record<string, string>;
  void _contentType;

  try {
    const response = await fetch(`${API_BASE}/chat/upload`, {
      method: "POST",
      headers: uploadHeaders,
      body: formData,
    });

    if (!response.ok) {
      const err = (await response.json().catch(() => ({}))) as {
        detail?: string;
      };
      return { data: null, error: err.detail ?? `HTTP ${response.status}` };
    }

    const data = (await response.json()) as UploadResponse;
    return { data, error: null };
  } catch (error) {
    return {
      data: null,
      error: error instanceof Error ? error.message : "Upload failed",
    };
  }
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
