/**
 * Zustand store for global application state.
 *
 * Centralizes:
 * - User authentication state
 * - Organization data
 * - Integrations (data sources)
 * - UI state (sidebar, current view)
 * - Per-conversation chat state (messages, streaming, active tasks)
 *
 * Architecture: All server data lives in Zustand, updated via WebSocket events
 * or explicit fetch calls. No polling - event-driven updates only.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import { API_BASE, apiRequest } from "../lib/api";

// =============================================================================
// Types
// =============================================================================

export interface UserProfile {
  id: string;
  email: string;
  name: string | null;
  avatarUrl: string | null;
  agentGlobalCommands: string | null;
  phoneNumber: string | null;
  jobTitle: string | null;
  roles: string[]; // Global roles like ['global_admin']
}

// Masquerade state for admin impersonation
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
}

// Integration types (data sources)
export interface TeamConnection {
  userId: string;
  userName: string;
}

export interface SyncStats {
  accounts?: number;
  deals?: number;
  contacts?: number;
  activities?: number;
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
  scope: "organization" | "user";
  isActive: boolean;
  lastSyncAt: string | null;
  lastError: string | null;
  connectedAt: string | null;
  connectedBy: string | null;
  currentUserConnected: boolean;
  teamConnections: TeamConnection[];
  teamTotal: number;
  syncStats: SyncStats | null;
}

export interface ChatSummary {
  id: string;
  title: string;
  lastMessageAt: Date;
  previewText: string;
  type?: "agent" | "workflow"; // 'agent' for interactive, 'workflow' for automated
  workflowId?: string; // ID of the workflow that triggered this conversation
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
  };
}

export interface AttachmentBlock {
  type: "attachment";
  filename: string;
  mimeType: string;
  size: number;
}

export type ContentBlock = TextBlock | ToolUseBlock | ErrorBlock | ArtifactBlock | AppBlock | AttachmentBlock;

// Legacy type for streaming compatibility
export interface ToolCallData {
  toolName: string;
  toolId: string;
  input: Record<string, unknown>;
  result?: Record<string, unknown>;
  status: "running" | "complete" | "error";
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  contentBlocks: ContentBlock[];
  timestamp: Date;
  isStreaming?: boolean;
}

export type View =
  | "home"
  | "chat"
  | "data-sources"
  | "data"
  | "search"
  | "workflows"
  | "apps"
  | "app-view"
  | "admin"
  | "pending-changes";

// Pending chunk for out-of-order handling
export interface PendingChunk {
  index: number;
  content: string;
}

// Per-conversation state
export interface ConversationState {
  messages: ChatMessage[];
  title: string;
  isThinking: boolean;
  streamingMessageId: string | null;
  activeTaskId: string | null;
  lastChunkIndex: number;
  pendingChunks: PendingChunk[]; // Buffer for out-of-order chunks
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
// Store Interface
// =============================================================================

export interface UserOrganization {
  id: string;
  name: string;
  logoUrl: string | null;
  role: string;
  isActive: boolean;
}

interface AppState {
  // Auth
  user: UserProfile | null;
  organization: OrganizationInfo | null;
  organizations: UserOrganization[]; // All orgs the user belongs to
  isAuthenticated: boolean;

  // Masquerade (admin impersonation)
  masquerade: MasqueradeState | null;

  // UI State
  sidebarCollapsed: boolean;
  currentView: View;
  currentChatId: string | null;
  currentAppId: string | null;
  recentChats: ChatSummary[];
  pinnedChatIds: string[];
  pendingChatInput: string | null; // Pre-filled input for new chats
  pendingChatAutoSend: boolean; // Auto-send pending input when chat opens

  // Per-conversation state (keyed by conversation ID)
  conversations: Record<string, ConversationState>;

  // Active task tracking (for quick lookups)
  activeTasksByConversation: Record<string, string>; // conversation_id -> task_id

  // Integrations (data sources)
  integrations: Integration[];
  integrationsLoading: boolean;
  integrationsError: string | null;

  // Legacy global state (for backwards compatibility during migration)
  messages: ChatMessage[];
  chatTitle: string;
  isThinking: boolean;
  streamingMessageId: string | null;
  conversationId: string | null;

  // Actions - Auth
  setUser: (user: UserProfile | null) => void;
  setOrganization: (org: OrganizationInfo | null) => void;
  setOrganizations: (orgs: UserOrganization[]) => void;
  fetchUserOrganizations: () => Promise<void>;
  switchActiveOrganization: (orgId: string) => Promise<void>;
  logout: () => void;

  // Actions - Masquerade
  startMasquerade: (
    targetUser: UserProfile,
    targetOrg: OrganizationInfo | null,
  ) => void;
  exitMasquerade: () => void;

  // Actions - Integrations
  fetchIntegrations: () => Promise<void>;
  setIntegrations: (integrations: Integration[]) => void;
  updateIntegration: (id: string, updates: Partial<Integration>) => void;

  // Actions - UI
  setSidebarCollapsed: (collapsed: boolean) => void;
  setCurrentView: (view: View) => void;
  setCurrentChatId: (id: string | null) => void;
  setCurrentAppId: (id: string | null) => void;
  startNewChat: () => void;
  setPendingChatInput: (input: string | null) => void;
  setPendingChatAutoSend: (autoSend: boolean) => void;
  togglePinChat: (id: string) => void;

  // Actions - Conversations
  addConversation: (id: string, title: string) => void;
  fetchConversations: () => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;

  // Actions - Per-conversation state
  getConversationState: (conversationId: string) => ConversationState;
  setConversationMessages: (
    conversationId: string,
    messages: ChatMessage[],
  ) => void;
  addConversationMessage: (
    conversationId: string,
    message: ChatMessage,
  ) => void;
  appendToConversationStreaming: (
    conversationId: string,
    content: string,
    chunkIndex: number,
  ) => void;
  startConversationStreaming: (
    conversationId: string,
    messageId: string,
    initialContent: string,
    chunkIndex?: number,
  ) => void;
  markConversationMessageComplete: (conversationId: string) => void;
  setConversationTitle: (conversationId: string, title: string) => void;
  setConversationThinking: (conversationId: string, thinking: boolean) => void;
  setConversationActiveTask: (
    conversationId: string,
    taskId: string | null,
  ) => void;
  updateConversationToolMessage: (
    conversationId: string,
    toolId: string,
    updates: Partial<ToolCallData>,
  ) => void;
  addConversationArtifactBlock: (
    conversationId: string,
    artifact: ArtifactBlock["artifact"],
  ) => void;
  addConversationAppBlock: (
    conversationId: string,
    app: AppBlock["app"],
  ) => void;
  clearConversation: (conversationId: string) => void;

  // Actions - Active tasks
  setActiveTasks: (tasks: ActiveTask[]) => void;
  hasActiveTask: (conversationId: string) => boolean;

  // Actions - Legacy chat messages (for backwards compatibility)
  setMessages: (messages: ChatMessage[]) => void;
  addMessage: (message: ChatMessage) => void;
  appendToStreamingMessage: (content: string) => void;
  startStreamingMessage: (id: string, initialContent: string) => void;
  markMessageComplete: () => void;
  setChatTitle: (title: string) => void;
  setIsThinking: (thinking: boolean) => void;
  setConversationId: (id: string | null) => void;
  clearChat: () => void;
  updateToolMessage: (toolId: string, updates: Partial<ToolCallData>) => void;

  // Actions - Sync user to backend
  syncUserToBackend: () => Promise<string | null>; // Returns user status or null on error
}

// =============================================================================
// Helper: Default conversation state
// =============================================================================

const defaultConversationState: ConversationState = {
  messages: [],
  title: "New Chat",
  isThinking: false,
  streamingMessageId: null,
  activeTaskId: null,
  lastChunkIndex: -1, // -1 means no chunks received yet, first chunk should be 0
  pendingChunks: [],
};

// =============================================================================
// Store Implementation
// =============================================================================

export const useAppStore = create<AppState>()(
  persist(
    (set, get) => ({
      // Initial state
      user: null,
      organization: null,
      organizations: [],
      isAuthenticated: false,
      masquerade: null,
      sidebarCollapsed: false,
      currentView: "home",
      currentChatId: null,
      currentAppId: null,
      recentChats: [],
      pinnedChatIds: [],
      pendingChatInput: null,
      pendingChatAutoSend: false,

      // Per-conversation state
      conversations: {},
      activeTasksByConversation: {},

      // Integrations
      integrations: [],
      integrationsLoading: false,
      integrationsError: null,

      // Legacy chat state (for backwards compatibility)
      messages: [],
      chatTitle: "New Chat",
      isThinking: false,
      streamingMessageId: null,
      conversationId: null,

      // Auth actions
      setUser: (user) =>
        set({
          user,
          isAuthenticated: user !== null,
        }),

      setOrganization: (organization) => set({ organization }),

      setOrganizations: (organizations) => set({ organizations }),

      fetchUserOrganizations: async () => {
        const { user } = get();
        if (!user) return;

        try {
          const response = await fetch(
            `${API_BASE}/auth/users/me/organizations?user_id=${user.id}`,
          );
          if (!response.ok) {
            console.error("[Store] Failed to fetch user organizations:", response.status);
            return;
          }

          interface OrgApiResponse {
            id: string;
            name: string;
            logo_url: string | null;
            role: string;
            is_active: boolean;
          }

          const data = (await response.json()) as {
            organizations: OrgApiResponse[];
          };

          const organizations: UserOrganization[] = data.organizations.map((o) => ({
            id: o.id,
            name: o.name,
            logoUrl: o.logo_url,
            role: o.role,
            isActive: o.is_active,
          }));

          console.log("[Store] Fetched", organizations.length, "user organizations");
          set({ organizations });
        } catch (error) {
          console.error("[Store] Error fetching user organizations:", error);
        }
      },

      switchActiveOrganization: async (orgId: string) => {
        const { user, organizations } = get();
        if (!user) return;

        try {
          const response = await fetch(
            `${API_BASE}/auth/users/me/active-organization?user_id=${user.id}`,
            {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ organization_id: orgId }),
            },
          );

          if (!response.ok) {
            const errData = (await response.json().catch(() => ({}))) as { detail?: string };
            console.error("[Store] Failed to switch org:", errData.detail ?? response.status);
            return;
          }

          const data = (await response.json()) as {
            organization: { id: string; name: string; logo_url: string | null } | null;
          };

          if (data.organization) {
            set({
              organization: {
                id: data.organization.id,
                name: data.organization.name,
                logoUrl: data.organization.logo_url,
              },
              organizations: organizations.map((o) => ({
                ...o,
                isActive: o.id === orgId,
              })),
              // Clear org-scoped state when switching
              currentChatId: null,
              recentChats: [],
              conversations: {},
              activeTasksByConversation: {},
              integrations: [],
            });
          }

          console.log("[Store] Switched active organization to:", orgId);
        } catch (error) {
          console.error("[Store] Error switching organization:", error);
        }
      },

      logout: () =>
        set({
          user: null,
          organization: null,
          organizations: [],
          isAuthenticated: false,
          masquerade: null,
          currentChatId: null,
          recentChats: [],
          pinnedChatIds: [],
          conversations: {},
          activeTasksByConversation: {},
          integrations: [],
          integrationsLoading: false,
          integrationsError: null,
          pendingChatInput: null,
          pendingChatAutoSend: false,
          // Clear legacy chat state
          messages: [],
          chatTitle: "New Chat",
          isThinking: false,
          streamingMessageId: null,
          conversationId: null,
        }),

      // Masquerade actions
      startMasquerade: (targetUser, targetOrg) => {
        const { user, organization } = get();
        if (!user) return;

        console.log("[Store] Starting masquerade as:", targetUser.email);
        set({
          masquerade: {
            originalUser: user,
            originalOrganization: organization,
            masqueradingAs: targetUser,
            masqueradeOrganization: targetOrg,
          },
          user: targetUser,
          organization: targetOrg,
          // Clear chat state when switching users
          currentChatId: null,
          recentChats: [],
          pinnedChatIds: [],
          conversations: {},
          activeTasksByConversation: {},
        });
      },

      exitMasquerade: () => {
        const { masquerade } = get();
        if (!masquerade) return;

        console.log(
          "[Store] Exiting masquerade, returning to:",
          masquerade.originalUser.email,
        );
        set({
          user: masquerade.originalUser,
          organization: masquerade.originalOrganization,
          masquerade: null,
          // Clear chat state when switching back
          currentChatId: null,
          recentChats: [],
          pinnedChatIds: [],
          conversations: {},
          activeTasksByConversation: {},
        });
      },

      // Integrations actions
      fetchIntegrations: async () => {
        const { user, organization } = get();
        if (!user || !organization) {
          console.log("[Store] No user/org, skipping integrations fetch");
          return;
        }

        set({ integrationsLoading: true, integrationsError: null });

        try {
          console.log(
            "[Store] Fetching integrations for org:",
            organization.id,
          );
          const response = await fetch(
            `${API_BASE}/auth/integrations?organization_id=${organization.id}&user_id=${user.id}`,
          );

          if (!response.ok) {
            throw new Error(`Failed to fetch integrations: ${response.status}`);
          }

          interface IntegrationApiResponse {
            id: string;
            provider: string;
            scope: string;
            is_active: boolean;
            last_sync_at: string | null;
            last_error: string | null;
            connected_at: string | null;
            connected_by: string | null;
            current_user_connected: boolean;
            team_connections: Array<{ user_id: string; user_name: string }>;
            team_total: number;
            sync_stats: SyncStats | null;
          }

          const data = (await response.json()) as {
            integrations: IntegrationApiResponse[];
          };

          const integrations: Integration[] = data.integrations.map((i) => ({
            id: i.id,
            provider: i.provider,
            scope: i.scope as "organization" | "user",
            isActive: i.is_active,
            lastSyncAt: i.last_sync_at,
            lastError: i.last_error,
            connectedAt: i.connected_at,
            connectedBy: i.connected_by,
            currentUserConnected: i.current_user_connected,
            teamConnections: i.team_connections.map((tc) => ({
              userId: tc.user_id,
              userName: tc.user_name,
            })),
            teamTotal: i.team_total,
            syncStats: i.sync_stats,
          }));

          console.log("[Store] Fetched", integrations.length, "integrations");
          set({ integrations, integrationsLoading: false });
        } catch (error) {
          console.error("[Store] Error fetching integrations:", error);
          set({
            integrationsError:
              error instanceof Error ? error.message : "Unknown error",
            integrationsLoading: false,
          });
        }
      },

      setIntegrations: (integrations) => set({ integrations }),

      updateIntegration: (id, updates) => {
        const { integrations } = get();
        set({
          integrations: integrations.map((i) =>
            i.id === id ? { ...i, ...updates } : i,
          ),
        });
      },

      // UI actions
      setSidebarCollapsed: (sidebarCollapsed) => set({ sidebarCollapsed }),
      setCurrentView: (currentView) =>
        set({
          currentView,
          // Clear chat selection when navigating away from chat view
          ...(currentView !== "chat" ? { currentChatId: null } : {}),
        }),
      setCurrentChatId: (currentChatId) => set({ currentChatId }),
      setCurrentAppId: (currentAppId) => set({ currentAppId }),
      startNewChat: () => set({ currentChatId: null, currentView: "chat" }),
      setPendingChatInput: (pendingChatInput) => set({ pendingChatInput }),
      setPendingChatAutoSend: (pendingChatAutoSend) =>
        set({ pendingChatAutoSend }),
      togglePinChat: (id) => {
        const { pinnedChatIds } = get();
        const isPinned = pinnedChatIds.includes(id);
        const updated = isPinned
          ? pinnedChatIds.filter((chatId) => chatId !== id)
          : [id, ...pinnedChatIds];
        console.log(
          "[Store] Toggling chat pin:",
          id,
          "Pinned:",
          !isPinned,
        );
        set({ pinnedChatIds: updated });
      },

      // Conversation actions
      addConversation: (id, title) => {
        const { recentChats } = get();
        if (recentChats.some((chat) => chat.id === id)) {
          console.log("[Store] Conversation already exists:", id);
          return;
        }
        console.log("[Store] Adding conversation:", id, title);
        set({
          recentChats: [
            { id, title, lastMessageAt: new Date(), previewText: "" },
            ...recentChats.slice(0, 9),
          ],
        });
      },

      fetchConversations: async () => {
        const { user } = get();
        if (!user) {
          console.log("[Store] No user, skipping conversations fetch");
          return;
        }

        try {
          console.log("[Store] Fetching conversations for user:", user.id);
          // Use apiRequest for authenticated requests (JWT in Authorization header)
          const { data, error } = await apiRequest<{
            conversations: Array<{
              id: string;
              title: string | null;
              updated_at: string;
              last_message_preview: string | null;
              type?: string;
              workflow_id?: string;
            }>;
            total: number;
          }>(`/chat/conversations?limit=20`);

          if (error || !data) {
            console.error("[Store] Failed to fetch conversations:", error);
            return;
          }

          console.log(
            "[Store] Conversations response:",
            data.conversations.length,
            "conversations",
          );
          const slackPreviewGaps = data.conversations.filter(
            (conv) =>
              (conv.title ?? "").toLowerCase().includes("slack") &&
              !conv.last_message_preview,
          );
          if (slackPreviewGaps.length > 0) {
            console.debug(
              "[Store] Slack conversations missing previews:",
              slackPreviewGaps.map((conv) => ({
                id: conv.id,
                title: conv.title,
                updated_at: conv.updated_at,
              })),
            );
          }

          const recentChats: ChatSummary[] = data.conversations.map((conv) => ({
            id: conv.id,
            title: conv.title ?? "New Chat",
            lastMessageAt: new Date(conv.updated_at),
            previewText: conv.last_message_preview ?? "",
            type: (conv.type ?? "agent") as "agent" | "workflow",
            workflowId: conv.workflow_id,
          }));

          set({ recentChats });
        } catch (error) {
          console.error("[Store] Error fetching conversations:", error);
        }
      },

      deleteConversation: async (id) => {
        const {
          user,
          recentChats,
          pinnedChatIds,
          currentChatId,
          conversationId,
          conversations,
        } = get();
        if (!user) return;

        if (!recentChats.some((chat) => chat.id === id)) {
          console.log(
            "[Store] Conversation already removed, skipping delete:",
            id,
          );
          return;
        }

        const updated = recentChats.filter((chat) => chat.id !== id);
        const shouldClearChat = currentChatId === id || conversationId === id;

        // Remove from conversations state
        const remainingConversations = { ...conversations };
        delete remainingConversations[id];

        const filteredPinnedChats = pinnedChatIds.filter(
          (chatId) => chatId !== id,
        );
        set({
          recentChats: updated,
          pinnedChatIds: filteredPinnedChats,
          conversations: remainingConversations,
          ...(shouldClearChat
            ? {
                currentChatId: null,
                conversationId: null,
                messages: [],
                chatTitle: "New Chat",
                isThinking: false,
                streamingMessageId: null,
              }
            : {}),
        });

        try {
          console.log("[Store] Deleting conversation:", id);
          // Use apiRequest for authenticated requests (JWT in Authorization header)
          const { error } = await apiRequest<{ success: boolean }>(
            `/chat/conversations/${id}`,
            { method: "DELETE" },
          );

          if (error) {
            console.error("[Store] Failed to delete conversation:", error);
          } else {
            console.log("[Store] Conversation deleted");
          }
        } catch (error) {
          console.error("[Store] Error deleting conversation:", error);
        }
      },

      // Per-conversation state actions
      getConversationState: (conversationId) => {
        const { conversations } = get();
        return conversations[conversationId] ?? { ...defaultConversationState };
      },

      setConversationMessages: (conversationId, messages) => {
        const { conversations } = get();
        const current = conversations[conversationId] ?? {
          ...defaultConversationState,
        };
        set({
          conversations: {
            ...conversations,
            [conversationId]: { ...current, messages },
          },
        });
      },

      addConversationMessage: (conversationId, message) => {
        const { conversations } = get();
        const current = conversations[conversationId] ?? {
          ...defaultConversationState,
        };
        console.log(
          "[Store] Adding message to conversation:",
          conversationId,
          message.role,
        );
        set({
          conversations: {
            ...conversations,
            [conversationId]: {
              ...current,
              messages: [...current.messages, message],
            },
          },
        });
      },

      appendToConversationStreaming: (conversationId, content, chunkIndex) => {
        const { conversations } = get();
        const current = conversations[conversationId];
        if (!current?.streamingMessageId) {
          console.warn(
            "[Store] No streaming message for conversation:",
            conversationId,
          );
          return;
        }

        // Helper to apply content to messages
        const applyContent = (
          messages: ChatMessage[],
          streamingId: string,
          text: string,
        ): ChatMessage[] => {
          return messages.map((msg) => {
            if (msg.id !== streamingId) return msg;
            const blocks = [...(msg.contentBlocks ?? [])];
            const lastBlock = blocks[blocks.length - 1];
            if (lastBlock && lastBlock.type === "text") {
              blocks[blocks.length - 1] = {
                ...lastBlock,
                text: lastBlock.text + text,
              };
            } else {
              blocks.push({ type: "text", text });
            }
            return { ...msg, contentBlocks: blocks };
          });
        };

        const expectedIndex = current.lastChunkIndex + 1;

        // If this is the expected chunk, apply it immediately
        if (chunkIndex === expectedIndex) {
          let updated = applyContent(
            current.messages,
            current.streamingMessageId,
            content,
          );
          let newLastIndex = chunkIndex;
          const newPendingChunks = [...current.pendingChunks];

          // Apply any buffered chunks that are now in sequence
          newPendingChunks.sort((a, b) => a.index - b.index);
          while (newPendingChunks.length > 0) {
            const nextPending = newPendingChunks[0];
            if (!nextPending || nextPending.index !== newLastIndex + 1) {
              break;
            }
            updated = applyContent(
              updated,
              current.streamingMessageId,
              nextPending.content,
            );
            newLastIndex = nextPending.index;
            newPendingChunks.shift();
          }

          set({
            conversations: {
              ...conversations,
              [conversationId]: {
                ...current,
                messages: updated,
                lastChunkIndex: newLastIndex,
                pendingChunks: newPendingChunks,
              },
            },
          });
        } else if (chunkIndex > expectedIndex) {
          // Chunk arrived out of order - buffer it
          const newPendingChunks = [
            ...current.pendingChunks,
            { index: chunkIndex, content },
          ];

          // If we've buffered too many chunks, the expected one is likely lost.
          // Skip ahead: treat the lowest buffered chunk as the next expected one.
          const MAX_BUFFER_SIZE: number = 5;
          if (newPendingChunks.length >= MAX_BUFFER_SIZE) {
            console.warn(
              `[Store] Skipping lost chunk(s) ${expectedIndex}-${chunkIndex - 1} for conversation:`,
              conversationId,
            );
            newPendingChunks.sort((a, b) => a.index - b.index);
            let updated = current.messages;
            let newLastIndex = current.lastChunkIndex;
            const remaining: typeof newPendingChunks = [];

            for (const pending of newPendingChunks) {
              updated = applyContent(
                updated,
                current.streamingMessageId,
                pending.content,
              );
              newLastIndex = pending.index;
            }

            set({
              conversations: {
                ...conversations,
                [conversationId]: {
                  ...current,
                  messages: updated,
                  lastChunkIndex: newLastIndex,
                  pendingChunks: remaining,
                },
              },
            });
          } else {
            console.log(
              `[Store] Buffering out-of-order chunk ${chunkIndex} (expected ${expectedIndex}) for conversation:`,
              conversationId,
            );
            set({
              conversations: {
                ...conversations,
                [conversationId]: {
                  ...current,
                  pendingChunks: newPendingChunks,
                },
              },
            });
          }
        }
        // If chunkIndex < expectedIndex, it's a duplicate - ignore it
      },

      startConversationStreaming: (
        conversationId,
        messageId,
        initialContent,
        chunkIndex,
      ) => {
        const { conversations } = get();
        const current = conversations[conversationId] ?? {
          ...defaultConversationState,
        };
        console.log(
          "[Store] Starting streaming for conversation:",
          conversationId,
          messageId,
          "at chunk index:",
          chunkIndex,
        );
        const newMessage: ChatMessage = {
          id: messageId,
          role: "assistant",
          contentBlocks: initialContent
            ? [{ type: "text", text: initialContent }]
            : [],
          timestamp: new Date(),
          isStreaming: true,
        };
        set({
          conversations: {
            ...conversations,
            [conversationId]: {
              ...current,
              messages: [...current.messages, newMessage],
              streamingMessageId: messageId,
              isThinking: false,
              // Update lastChunkIndex if provided, clear pending chunks for new stream
              lastChunkIndex: chunkIndex ?? current.lastChunkIndex,
              pendingChunks: [],
            },
          },
        });
      },

      markConversationMessageComplete: (conversationId) => {
        const { conversations } = get();
        const current = conversations[conversationId];
        if (!current) return;

        // Check if any messages are still streaming
        const hasStreamingMessages = current.messages.some(
          (msg) => msg.isStreaming,
        );
        if (!hasStreamingMessages && !current.streamingMessageId) return;

        console.log(
          "[Store] Marking complete for conversation:",
          conversationId,
        );

        // Mark ALL streaming messages as complete (not just streamingMessageId)
        // This handles cases where streamingMessageId was already cleared
        const updated = current.messages.map((msg) =>
          msg.isStreaming ? { ...msg, isStreaming: false } : msg,
        );
        set({
          conversations: {
            ...conversations,
            [conversationId]: {
              ...current,
              messages: updated,
              streamingMessageId: null,
            },
          },
        });
      },

      setConversationTitle: (conversationId, title) => {
        const { conversations } = get();
        const current = conversations[conversationId] ?? {
          ...defaultConversationState,
        };
        set({
          conversations: {
            ...conversations,
            [conversationId]: { ...current, title },
          },
        });
      },

      setConversationThinking: (conversationId, thinking) => {
        const { conversations } = get();
        const current = conversations[conversationId] ?? {
          ...defaultConversationState,
        };
        set({
          conversations: {
            ...conversations,
            [conversationId]: { ...current, isThinking: thinking },
          },
        });
      },

      setConversationActiveTask: (conversationId, taskId) => {
        const { conversations, activeTasksByConversation } = get();
        const current = conversations[conversationId] ?? {
          ...defaultConversationState,
        };

        const updatedActiveTasks = { ...activeTasksByConversation };
        if (taskId) {
          updatedActiveTasks[conversationId] = taskId;
        } else {
          delete updatedActiveTasks[conversationId];
        }

        set({
          conversations: {
            ...conversations,
            [conversationId]: {
              ...current,
              activeTaskId: taskId,
              // Reset chunk tracking when a new task starts
              ...(taskId
                ? { lastChunkIndex: -1, pendingChunks: [] }
                : {}),
            },
          },
          activeTasksByConversation: updatedActiveTasks,
        });
      },

      updateConversationToolMessage: (conversationId, toolId, updates) => {
        const { conversations } = get();
        const current = conversations[conversationId];
        const defaultToolBlock = {
          type: "tool_use" as const,
          id: toolId,
          name: updates.toolName ?? "workflow_tool",
          input: updates.input ?? {},
          result: (updates.result as Record<string, unknown>) ?? {},
          status: (updates.status as "pending" | "running" | "complete" | undefined) ?? "running",
        };

        if (!current) {
          const message = {
            id: `tool-progress-${toolId}-${Date.now()}`,
            role: "assistant" as const,
            contentBlocks: [defaultToolBlock],
            timestamp: new Date(),
          };
          set({
            conversations: {
              ...conversations,
              [conversationId]: {
                ...defaultConversationState,
                messages: [message],
              },
            },
          });
          return;
        }

        let foundMatchingTool = false;

        const updated = current.messages.map((msg) => {
          const blocks = msg.contentBlocks ?? [];
          const hasMatchingTool = blocks.some(
            (block) => block.type === "tool_use" && block.id === toolId,
          );
          if (!hasMatchingTool) return msg;
          foundMatchingTool = true;

          const updatedBlocks = blocks.map((block) => {
            if (block.type === "tool_use" && block.id === toolId) {
              // Merge result updates instead of replacing entirely (for progress updates)
              const currentResult = (block.result as Record<string, unknown>) || {};
              const newResult = updates.result 
                ? { ...currentResult, ...updates.result }
                : currentResult;
              return {
                ...block,
                result: newResult,
                status:
                  (updates.status as
                    | "pending"
                    | "running"
                    | "complete"
                    | undefined) ?? block.status,
              };
            }
            return block;
          });
          return { ...msg, contentBlocks: updatedBlocks };
        });

        if (!foundMatchingTool) {
          const lastMsg = updated[updated.length - 1];
          if (lastMsg && lastMsg.role === "assistant") {
            updated[updated.length - 1] = {
              ...lastMsg,
              contentBlocks: [...(lastMsg.contentBlocks ?? []), defaultToolBlock],
            };
          } else {
            updated.push({
              id: `tool-progress-${toolId}-${Date.now()}`,
              role: "assistant",
              contentBlocks: [defaultToolBlock],
              timestamp: new Date(),
            });
          }
        }

        set({
          conversations: {
            ...conversations,
            [conversationId]: { ...current, messages: updated },
          },
        });
      },

      addConversationArtifactBlock: (conversationId, artifact) => {
        const { conversations } = get();
        const current = conversations[conversationId];
        if (!current) return;

        // Add artifact block to the last assistant message
        const updated: ChatMessage[] = current.messages.map((msg, idx, arr) => {
          // Find the last assistant message
          const isLastAssistant =
            msg.role === "assistant" &&
            !arr.slice(idx + 1).some((m) => m.role === "assistant");

          if (isLastAssistant) {
            const blocks = msg.contentBlocks ?? [];
            return {
              ...msg,
              contentBlocks: [...blocks, { type: "artifact" as const, artifact }],
            };
          }
          return msg;
        });

        set({
          conversations: {
            ...conversations,
            [conversationId]: { ...current, messages: updated },
          },
        });
      },

      addConversationAppBlock: (conversationId, app) => {
        const { conversations } = get();
        const current: ConversationState | undefined = conversations[conversationId];
        if (!current) return;

        const updated: ChatMessage[] = current.messages.map((msg, idx, arr) => {
          const isLastAssistant: boolean =
            msg.role === "assistant" &&
            !arr.slice(idx + 1).some((m) => m.role === "assistant");

          if (isLastAssistant) {
            const blocks: ContentBlock[] = msg.contentBlocks ?? [];
            return {
              ...msg,
              contentBlocks: [...blocks, { type: "app" as const, app }],
            };
          }
          return msg;
        });

        set({
          conversations: {
            ...conversations,
            [conversationId]: { ...current, messages: updated },
          },
        });
      },

      clearConversation: (conversationId) => {
        const { conversations, activeTasksByConversation } = get();
        const remaining = { ...conversations };
        delete remaining[conversationId];
        const remainingTasks = { ...activeTasksByConversation };
        delete remainingTasks[conversationId];
        set({
          conversations: remaining,
          activeTasksByConversation: remainingTasks,
        });
      },

      // Active tasks actions
      setActiveTasks: (tasks) => {
        const { conversations } = get();
        const activeTasksByConversation: Record<string, string> = {};
        const updatedConversations: Record<string, ConversationState> = {
          ...conversations,
        };

        for (const task of tasks) {
          if (task.status === "running") {
            activeTasksByConversation[task.conversation_id] = task.id;

            // Initialize conversation state if needed
            const existing = updatedConversations[task.conversation_id] ?? {
              ...defaultConversationState,
            };
            updatedConversations[task.conversation_id] = {
              ...existing,
              activeTaskId: task.id,
            };
          }
        }

        console.log(
          "[Store] Set active tasks:",
          Object.keys(activeTasksByConversation).length,
        );
        set({ activeTasksByConversation, conversations: updatedConversations });
      },

      hasActiveTask: (conversationId) => {
        const { activeTasksByConversation } = get();
        return conversationId in activeTasksByConversation;
      },

      // Legacy chat message actions (for backwards compatibility)
      setMessages: (messages) => set({ messages }),

      addMessage: (message) => {
        const { messages } = get();
        console.log(
          "[Store] Adding message (legacy):",
          message.role,
          message.id,
        );
        set({ messages: [...messages, message] });
      },

      appendToStreamingMessage: (content) => {
        const { messages, streamingMessageId } = get();
        if (!streamingMessageId) {
          console.warn("[Store] No streaming message to append to");
          return;
        }
        const updated = messages.map((msg) => {
          if (msg.id !== streamingMessageId) return msg;
          const blocks = [...(msg.contentBlocks ?? [])];
          const lastBlock = blocks[blocks.length - 1];
          if (lastBlock && lastBlock.type === "text") {
            blocks[blocks.length - 1] = {
              ...lastBlock,
              text: lastBlock.text + content,
            };
          } else {
            blocks.push({ type: "text", text: content });
          }
          return { ...msg, contentBlocks: blocks };
        });
        set({ messages: updated });
      },

      startStreamingMessage: (id, initialContent) => {
        const { messages } = get();
        console.log("[Store] Starting streaming message (legacy):", id);
        const newMessage: ChatMessage = {
          id,
          role: "assistant",
          contentBlocks: initialContent
            ? [{ type: "text", text: initialContent }]
            : [],
          timestamp: new Date(),
          isStreaming: true,
        };
        set({
          messages: [...messages, newMessage],
          streamingMessageId: id,
          isThinking: false,
        });
      },

      markMessageComplete: () => {
        const { messages, streamingMessageId } = get();
        console.log(
          "[Store] Marking message complete (legacy):",
          streamingMessageId,
        );
        if (!streamingMessageId) return;
        const updated = messages.map((msg) =>
          msg.id === streamingMessageId ? { ...msg, isStreaming: false } : msg,
        );
        set({ messages: updated, streamingMessageId: null });
      },

      setChatTitle: (chatTitle) => set({ chatTitle }),
      setIsThinking: (isThinking) => set({ isThinking }),
      setConversationId: (conversationId) => set({ conversationId }),

      clearChat: () =>
        set({
          messages: [],
          chatTitle: "New Chat",
          isThinking: false,
          streamingMessageId: null,
          conversationId: null,
        }),

      updateToolMessage: (toolId, updates) => {
        const { messages } = get();
        const updated = messages.map((msg) => {
          const blocks = msg.contentBlocks ?? [];
          const hasMatchingTool = blocks.some(
            (block) => block.type === "tool_use" && block.id === toolId,
          );
          if (!hasMatchingTool) return msg;

          const updatedBlocks = blocks.map((block) => {
            if (block.type === "tool_use" && block.id === toolId) {
              return {
                ...block,
                result: updates.result ?? block.result,
                status:
                  (updates.status as
                    | "pending"
                    | "running"
                    | "complete"
                    | undefined) ?? block.status,
              };
            }
            return block;
          });
          return { ...msg, contentBlocks: updatedBlocks };
        });
        set({ messages: updated });
      },

      // Sync user to backend
      syncUserToBackend: async (): Promise<string | null> => {
        const { user, organization, setUser } = get();
        if (!user) return null;

        try {
          console.log(
            "[Store] Syncing user to backend:",
            user.id,
            user.email,
            organization?.id,
          );
          const response = await fetch(`${API_BASE}/auth/users/sync`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              id: user.id,
              email: user.email,
              name: user.name,
              avatar_url: user.avatarUrl,
              agent_global_commands: user.agentGlobalCommands,
              organization_id: organization?.id,
            }),
          });

          if (!response.ok) {
            if (response.status === 403) {
              console.log("[Store] User not on waitlist");
              return "not_registered";
            }
            const errorData = (await response.json().catch(() => ({}))) as {
              detail?: string;
            };
            throw new Error(errorData.detail ?? `HTTP ${response.status}`);
          }

          const data = (await response.json()) as {
            id: string;
            status: string;
            avatar_url: string | null;
            name: string | null;
            agent_global_commands: string | null;
            phone_number: string | null;
            job_title: string | null;
            roles: string[];
            organization: {
              id: string;
              name: string;
              logo_url: string | null;
            } | null;
          };
          console.log("[Store] User synced successfully, status:", data.status);

          const newRoles = data.roles ?? [];
          if (
            data.id !== user.id ||
            data.avatar_url !== user.avatarUrl ||
            data.name !== user.name ||
            data.agent_global_commands !== user.agentGlobalCommands ||
            data.phone_number !== user.phoneNumber ||
            data.job_title !== user.jobTitle ||
            JSON.stringify(newRoles) !== JSON.stringify(user.roles)
          ) {
            setUser({
              ...user,
              id: data.id,
              name: data.name ?? user.name,
              avatarUrl: data.avatar_url ?? user.avatarUrl,
              agentGlobalCommands: data.agent_global_commands ?? user.agentGlobalCommands,
              phoneNumber: data.phone_number ?? user.phoneNumber,
              jobTitle: data.job_title ?? user.jobTitle,
              roles: newRoles,
            });
          }

          if (data.organization) {
            const { setOrganization } = get();
            setOrganization({
              id: data.organization.id,
              name: data.organization.name,
              logoUrl: data.organization.logo_url,
            });
          }

          return data.status;
        } catch (error) {
          console.error("[Store] Failed to sync user to backend:", error);
          return null;
        }
      },
    }),
    {
      name: "revtops-store",
      partialize: (state) => ({
        user: state.user,
        organization: state.organization,
        organizations: state.organizations,
        isAuthenticated: state.isAuthenticated,
        sidebarCollapsed: state.sidebarCollapsed,
        pinnedChatIds: state.pinnedChatIds,
        masquerade: state.masquerade, // Persist masquerade state so admin can exit after reload
      }),
    },
  ),
);

// =============================================================================
// Selector Hooks (for convenience)
// =============================================================================

export const useUser = () => useAppStore((state) => state.user);
export const useOrganization = () => useAppStore((state) => state.organization);
export const useOrganizations = () => useAppStore((state) => state.organizations);
export const useIsAuthenticated = () =>
  useAppStore((state) => state.isAuthenticated);
export const useSidebarCollapsed = () =>
  useAppStore((state) => state.sidebarCollapsed);
export const useCurrentView = () => useAppStore((state) => state.currentView);
export const useIsGlobalAdmin = () =>
  useAppStore((state) => state.user?.roles?.includes("global_admin") ?? false);
export const useMasquerade = () => useAppStore((state) => state.masquerade);
export const useIsMasquerading = () =>
  useAppStore((state) => state.masquerade !== null);
// Get the real admin user ID when masquerading (for API headers)
export const getAdminUserId = (): string | null => {
  const state = useAppStore.getState();
  return state.masquerade?.originalUser.id ?? null;
};

// Get the target user ID when masquerading (for API impersonation headers)
export const getMasqueradeUserId = (): string | null => {
  const state = useAppStore.getState();
  return state.masquerade?.masqueradingAs.id ?? null;
};

// Legacy chat selectors (for backwards compatibility)
export const useMessages = () => useAppStore((state) => state.messages);
export const useChatTitle = () => useAppStore((state) => state.chatTitle);
export const useIsThinking = () => useAppStore((state) => state.isThinking);
export const useConversationId = () =>
  useAppStore((state) => state.conversationId);

// Per-conversation selectors
export const useConversationState = (conversationId: string | null) =>
  useAppStore((state) =>
    conversationId ? state.conversations[conversationId] ?? null : null,
  );
export const useConversationMessages = (conversationId: string | null) =>
  useAppStore((state) =>
    conversationId ? state.conversations[conversationId]?.messages ?? [] : [],
  );
export const useActiveTasksByConversation = () =>
  useAppStore((state) => state.activeTasksByConversation);
export const useHasActiveTask = (conversationId: string | null) =>
  useAppStore((state) =>
    conversationId ? conversationId in state.activeTasksByConversation : false,
  );

// Integration selectors
export const useIntegrations = () => useAppStore((state) => state.integrations);
export const useIntegrationsLoading = () =>
  useAppStore((state) => state.integrationsLoading);
export const useIntegrationsError = () =>
  useAppStore((state) => state.integrationsError);
export const useIntegration = (provider: string) =>
  useAppStore(
    (state) => state.integrations.find((i) => i.provider === provider) ?? null,
  );
export const useConnectedIntegrations = () =>
  useAppStore((state) => state.integrations.filter((i) => i.isActive));
