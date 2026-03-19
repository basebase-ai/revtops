/**
 * Shared type definitions for the store layer.
 *
 * Extracted into a separate module so that sub-stores can import types
 * without circular dependencies.
 */

// =============================================================================
// Auth types
// =============================================================================

export interface UserProfile {
  id: string;
  email: string;
  name: string | null;
  avatarUrl: string | null;
  phoneNumber: string | null;
  jobTitle: string | null;
  roles: string[]; // Global roles like ['global_admin']
  smsConsent: boolean;
  whatsappConsent: boolean;
  phoneNumberVerified: boolean;
}

export interface MasqueradeState {
  originalUser: UserProfile;
  originalOrganization: OrganizationInfo | null;
  masqueradingAs: UserProfile;
  masqueradeOrganization: OrganizationInfo | null;
}

export interface OrganizationInfo {
  id: string;
  name: string;
  logoUrl: string | null;
  handle?: string | null; // Optional for backwards compat with persisted state
}

export interface UserOrganization {
  id: string;
  name: string;
  logoUrl: string | null;
  handle?: string | null;
  role: string;
  isActive: boolean;
}

// =============================================================================
// Integration types (data sources)
// =============================================================================

export interface TeamConnection {
  userId: string;
  userName: string;
}

export interface SyncStats {
  accounts?: number;
  deals?: number;
  contacts?: number;
  activities?: number;
  channels?: number;
  pipelines?: number;
  goals?: number;
  repositories?: number;
  commits?: number;
  pull_requests?: number;
  total_files?: number;
  docs?: number;
  sheets?: number;
  slides?: number;
  folders?: number;
  // Issue tracker providers (Linear, Jira, Asana)
  teams?: number;
  projects?: number;
  issues?: number;
}

export interface Integration {
  id: string;
  provider: string;
  userId: string | null;
  isActive: boolean;
  lastSyncAt: string | null;
  lastError: string | null;
  connectedAt: string | null;
  connectedBy: string | null;
  scope: "organization" | "user";
  // Sharing settings
  shareSyncedData: boolean;
  shareQueryAccess: boolean;
  shareWriteAccess: boolean;
  pendingSharingConfig: boolean;
  // Ownership
  isOwner: boolean;
  currentUserConnected: boolean;
  teamConnections: TeamConnection[];
  teamTotal: number;
  syncStats: SyncStats | null;
  displayName: string | null;
}

// =============================================================================
// Chat / conversation types
// =============================================================================

export interface Participant {
  id: string;
  name: string | null;
  email: string;
  avatarUrl?: string | null;
}

export interface ChatSummary {
  id: string;
  title: string;
  lastMessageAt: Date;
  previewText: string;
  type?: "agent" | "workflow";
  workflowId?: string;
  scope: "private" | "shared";
  userId?: string; // Creator's user ID
  participants?: Participant[];
}

// Workstream (semantic Home) types
export interface WorkstreamParticipant {
  id: string;
  name: string | null;
  avatar_url: string | null;
  message_count_in_window: number;
}

export interface WorkstreamConversation {
  id: string;
  title: string | null;
  message_count: number;
  messages_in_window: number;
  last_message_at: string;
  participants: WorkstreamParticipant[];
  position: [number, number] | null;
}

export interface WorkstreamItem {
  id: string;
  label: string;
  description: string;
  position: [number, number];
  conversations: WorkstreamConversation[];
}

export interface WorkstreamsResponse {
  workstreams: WorkstreamItem[];
  unclustered: WorkstreamConversation[];
  computed_at: string;
}

// Content block types (matches API)
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
  status?: "pending" | "running" | "complete" | "streaming";
  /** Human-friendly status from registry; set from stream events. */
  statusText?: string;
}

export interface ErrorBlock {
  type: "error";
  message: string;
}

export interface ArtifactBlock {
  type: "artifact";
  artifact: {
    id: string;
    title: string;
    filename: string;
    contentType: "text" | "markdown" | "pdf" | "chart";
    mimeType: string;
  };
}

export interface AppBlock {
  type: "app";
  app: {
    id: string;
    title: string;
    description: string | null;
    frontendCode: string;
    frontendCodeCompiled?: string | null;
  };
}

export interface ThinkingBlock {
  type: "thinking";
  text: string;
  isStreaming?: boolean;
}

export interface AttachmentBlock {
  type: "attachment";
  filename: string;
  mimeType: string;
  size: number;
  /** Set when attachment was persisted (sent message); enables click-to-view. API may return attachment_id. */
  attachmentId?: string;
}

export type ContentBlock =
  | TextBlock
  | ToolUseBlock
  | ErrorBlock
  | ArtifactBlock
  | AppBlock
  | ThinkingBlock
  | AttachmentBlock;

// Legacy type for streaming compatibility
export interface ToolCallData {
  toolName: string;
  toolId: string;
  input: Record<string, unknown>;
  result?: Record<string, unknown>;
  status: "running" | "complete" | "error";
  /** Human-friendly status from registry (e.g. "Querying your database"); set from stream events. */
  statusText?: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  contentBlocks: ContentBlock[];
  timestamp: Date;
  isStreaming?: boolean;
  userId?: string;
  senderName?: string | null;
  senderEmail?: string | null;
  senderAvatarUrl?: string | null;
}

// Pending chunk for out-of-order handling
export interface PendingChunk {
  index: number;
  content: string;
}

// Per-conversation summary (generated by backend)
export interface ConversationSummaryData {
  overall: string;
  recent: string;
  message_count_at_generation: number;
  updated_at: string;
}

// Per-conversation state
export interface ConversationState {
  messages: ChatMessage[];
  title: string;
  isThinking: boolean;
  streamingMessageId: string | null;
  activeTaskId: string | null;
  lastChunkIndex: number;
  pendingChunks: PendingChunk[];
  summary: ConversationSummaryData | null;
  hasMore: boolean;
  contextTokens: number | null;
}

// Task state from backend
export interface ActiveTask {
  id: string;
  conversation_id: string;
  status: string;
  output_chunks: Array<{
    index: number;
    type: string;
    data: unknown;
    timestamp: string;
  }>;
}

// =============================================================================
// UI types
// =============================================================================

export type View =
  | "home"
  | "chat"
  | "chats"
  | "data-sources"
  | "data"
  | "workflows"
  | "memory"
  | "apps"
  | "app-view"
  | "documents"
  | "artifact-view"
  | "admin"
  | "pending-changes";
