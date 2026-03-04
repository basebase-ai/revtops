/**
 * Chat store — conversations, messages, streaming state, integrations.
 *
 * Split from the monolithic AppState store for performance: only components
 * that read chat-related fields re-render when chat state changes.
 */

import { create } from "zustand";
import { API_BASE, apiRequest } from "../lib/api";
import type {
  ChatSummary,
  ChatMessage,
  ConversationState,
  ConversationSummaryData,
  ActiveTask,
  ToolCallData,
  ArtifactBlock,
  AppBlock,
  ContentBlock,
  Integration,
  SyncStats,
} from "./types";
import { useAuthStore } from "./authStore";
import { useUIStore } from "./uiStore";

// ---------------------------------------------------------------------------
// Helper: Default conversation state
// ---------------------------------------------------------------------------

const defaultConversationState: ConversationState = {
  messages: [],
  title: "New Chat",
  isThinking: false,
  streamingMessageId: null,
  activeTaskId: null,
  lastChunkIndex: -1,
  pendingChunks: [],
  summary: null,
  hasMore: false,
  contextTokens: null,
};

// ---------------------------------------------------------------------------
// Store interface
// ---------------------------------------------------------------------------

export interface ChatState {
  // Conversation list
  recentChats: ChatSummary[];
  currentChatId: string | null;
  pendingChatInput: string | null;
  pendingChatAutoSend: boolean;

  // Per-conversation state (keyed by conversation ID)
  conversations: Record<string, ConversationState>;

  // Active task tracking (for quick lookups)
  activeTasksByConversation: Record<string, string>;

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

  // Actions - Conversations
  setCurrentChatId: (id: string | null) => void;
  setPendingChatInput: (input: string | null) => void;
  setPendingChatAutoSend: (autoSend: boolean) => void;
  addConversation: (
    id: string,
    title: string,
    scope?: "private" | "shared",
  ) => void;
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
  setConversationSummary: (conversationId: string, summary: ConversationSummaryData) => void;
  setConversationContextTokens: (conversationId: string, tokens: number) => void;
  setConversationHasMore: (conversationId: string, hasMore: boolean) => void;
  fetchOlderMessages: (conversationId: string) => Promise<boolean>;
  setConversationThinking: (
    conversationId: string,
    thinking: boolean,
  ) => void;
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

  // Actions - Integrations
  fetchIntegrations: () => Promise<void>;
  setIntegrations: (integrations: Integration[]) => void;
  updateIntegration: (id: string, updates: Partial<Integration>) => void;

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
  updateToolMessage: (
    toolId: string,
    updates: Partial<ToolCallData>,
  ) => void;
}

// ---------------------------------------------------------------------------
// Store implementation
// ---------------------------------------------------------------------------

export const useChatStore = create<ChatState>()(
  (set, get) => ({
    // Initial state
    recentChats: [],
    currentChatId: null,
    pendingChatInput: null,
    pendingChatAutoSend: false,
    conversations: {},
    activeTasksByConversation: {},
    integrations: [],
    integrationsLoading: false,
    integrationsError: null,
    messages: [],
    chatTitle: "New Chat",
    isThinking: false,
    streamingMessageId: null,
    conversationId: null,

    // Conversation actions
    setCurrentChatId: (currentChatId) => set({ currentChatId }),
    setPendingChatInput: (pendingChatInput) => set({ pendingChatInput }),
    setPendingChatAutoSend: (pendingChatAutoSend) =>
      set({ pendingChatAutoSend }),

    addConversation: (id, title, scope?: "private" | "shared") => {
      const { recentChats } = get();
      if (recentChats.some((chat) => chat.id === id)) {
        console.log("[Store] Conversation already exists:", id);
        return;
      }
      console.log("[Store] Adding conversation:", id, title, scope);
      set({
        recentChats: [
          {
            id,
            title,
            lastMessageAt: new Date(),
            previewText: "",
            scope: scope ?? "shared",
          },
          ...recentChats.slice(0, 9),
        ],
      });
    },

    fetchConversations: async () => {
      // Read user from authStore
      const user = useAuthStore.getState().user;
      if (!user) {
        console.log("[Store] No user, skipping conversations fetch");
        return;
      }

      try {
        console.log("[Store] Fetching conversations for user:", user.id);

        type ConversationApiResponse = {
          conversations: Array<{
            id: string;
            title: string | null;
            updated_at: string;
            last_message_preview: string | null;
            type?: string;
            workflow_id?: string;
            scope?: "private" | "shared";
            participants?: Array<{
              id: string;
              name: string | null;
              email: string;
              avatar_url?: string | null;
            }>;
          }>;
          total: number;
        };

        const requestStart = performance.now();
        const { data, error } = await apiRequest<ConversationApiResponse>(
          `/chat/conversations?limit=40`,
        );

        if (error) {
          console.error("[Store] Failed to fetch conversations:", error);
          return;
        }
        const conversations = data?.conversations ?? [];

        console.log(
          "[Store] Conversations fetched:",
          conversations.length,
          "in",
          Math.round(performance.now() - requestStart),
          "ms",
        );

        const mapConversation = (
          conv: ConversationApiResponse["conversations"][0],
        ): ChatSummary => ({
          id: conv.id,
          title: conv.title ?? "New Chat",
          lastMessageAt: new Date(conv.updated_at),
          previewText: conv.last_message_preview ?? "",
          type: (conv.type ?? "agent") as "agent" | "workflow",
          workflowId: conv.workflow_id,
          scope: (conv.scope ?? "shared") as "private" | "shared",
          participants: conv.participants?.map((p) => ({
            id: p.id,
            name: p.name,
            email: p.email,
            avatarUrl: p.avatar_url,
          })),
        });

        const recentChats: ChatSummary[] = conversations
          .map(mapConversation)
          .sort(
            (a, b) => b.lastMessageAt.getTime() - a.lastMessageAt.getTime(),
          );

        set({ recentChats });
      } catch (error) {
        console.error("[Store] Error fetching conversations:", error);
      }
    },

    deleteConversation: async (id) => {
      // Read user from authStore
      const user = useAuthStore.getState().user;
      if (!user) return;

      const {
        recentChats,
        currentChatId,
        conversationId,
        conversations,
      } = get();

      // Read pinnedChatIds from uiStore
      const pinnedChatIds = useUIStore.getState().pinnedChatIds;

      if (!recentChats.some((chat) => chat.id === id)) {
        console.log(
          "[Store] Conversation already removed, skipping delete:",
          id,
        );
        return;
      }

      const updated = recentChats.filter((chat) => chat.id !== id);
      const shouldClearChat = currentChatId === id || conversationId === id;

      const remainingConversations = { ...conversations };
      delete remainingConversations[id];

      const filteredPinnedChats = pinnedChatIds.filter(
        (chatId) => chatId !== id,
      );
      // Update pinnedChatIds in uiStore
      useUIStore.setState({ pinnedChatIds: filteredPinnedChats });

      set({
        recentChats: updated,
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

      if (chunkIndex === expectedIndex) {
        let updated = applyContent(
          current.messages,
          current.streamingMessageId,
          content,
        );
        let newLastIndex = chunkIndex;
        const newPendingChunks = [...current.pendingChunks];

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
        const newPendingChunks = [
          ...current.pendingChunks,
          { index: chunkIndex, content },
        ];

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

      const hasStreamingMessages = current.messages.some(
        (msg) => msg.isStreaming,
      );
      if (!hasStreamingMessages && !current.streamingMessageId) return;

      console.log(
        "[Store] Marking complete for conversation:",
        conversationId,
      );

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

    setConversationSummary: (conversationId, summary) => {
      const { conversations } = get();
      const current = conversations[conversationId] ?? {
        ...defaultConversationState,
      };
      set({
        conversations: {
          ...conversations,
          [conversationId]: { ...current, summary },
        },
      });
    },

    setConversationContextTokens: (conversationId, tokens) => {
      const { conversations } = get();
      const current = conversations[conversationId] ?? {
        ...defaultConversationState,
      };
      set({
        conversations: {
          ...conversations,
          [conversationId]: { ...current, contextTokens: tokens },
        },
      });
    },

    setConversationHasMore: (conversationId, hasMore) => {
      const { conversations } = get();
      const current = conversations[conversationId] ?? {
        ...defaultConversationState,
      };
      set({
        conversations: {
          ...conversations,
          [conversationId]: { ...current, hasMore },
        },
      });
    },

    fetchOlderMessages: async (conversationId) => {
      const { conversations } = get();
      const current = conversations[conversationId];
      if (!current || !current.hasMore || current.messages.length === 0) {
        return false;
      }

      const oldestMessage = current.messages[0] as ChatMessage;
      const before = oldestMessage.timestamp.toISOString();

      try {
        const { getConversation: getConv } = await import("../api/client");
        const { data, error } = await getConv(conversationId, { before });

        if (error || !data) {
          console.error("[Store] Failed to fetch older messages:", error);
          return false;
        }

        const olderMessages: ChatMessage[] = data.messages.map((msg) => ({
          id: msg.id,
          role: msg.role as "user" | "assistant",
          contentBlocks: msg.content_blocks,
          timestamp: new Date(msg.created_at),
          userId: msg.user_id ?? undefined,
          senderName: msg.sender_name ?? undefined,
          senderEmail: msg.sender_email ?? undefined,
          senderAvatarUrl: msg.sender_avatar_url ?? undefined,
        }));

        const updatedCurrent =
          get().conversations[conversationId] ?? current;
        set({
          conversations: {
            ...get().conversations,
            [conversationId]: {
              ...updatedCurrent,
              messages: [...olderMessages, ...updatedCurrent.messages],
              hasMore: data.has_more,
            },
          },
        });

        return data.has_more;
      } catch (err) {
        console.error("[Store] Error fetching older messages:", err);
        return false;
      }
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
            ...(taskId ? { lastChunkIndex: -1, pendingChunks: [] } : {}),
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
        status:
          (updates.status as
            | "pending"
            | "running"
            | "complete"
            | undefined) ?? "running",
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
            const currentResult =
              (block.result as Record<string, unknown>) || {};
            const newResult = updates.result
              ? { ...currentResult, ...updates.result }
              : currentResult;
            const newInput =
              updates.input != null &&
              Object.keys(updates.input).length > 0 &&
              Object.keys(block.input ?? {}).length === 0
                ? updates.input
                : (block.input ?? {});
            return {
              ...block,
              input: newInput,
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
            contentBlocks: [
              ...(lastMsg.contentBlocks ?? []),
              defaultToolBlock,
            ],
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

      const updated: ChatMessage[] = current.messages.map(
        (msg, idx, arr) => {
          const isLastAssistant =
            msg.role === "assistant" &&
            !arr.slice(idx + 1).some((m) => m.role === "assistant");

          if (isLastAssistant) {
            const blocks = msg.contentBlocks ?? [];
            return {
              ...msg,
              contentBlocks: [
                ...blocks,
                { type: "artifact" as const, artifact },
              ],
            };
          }
          return msg;
        },
      );

      set({
        conversations: {
          ...conversations,
          [conversationId]: { ...current, messages: updated },
        },
      });
    },

    addConversationAppBlock: (conversationId, app) => {
      const { conversations } = get();
      const current: ConversationState | undefined =
        conversations[conversationId];
      if (!current) return;

      const updated: ChatMessage[] = current.messages.map(
        (msg, idx, arr) => {
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
        },
      );

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
      set({
        activeTasksByConversation,
        conversations: updatedConversations,
      });
    },

    hasActiveTask: (conversationId) => {
      const { activeTasksByConversation } = get();
      return conversationId in activeTasksByConversation;
    },

    // Integrations actions
    fetchIntegrations: async () => {
      const { user, organization } = useAuthStore.getState();
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
          throw new Error(
            `Failed to fetch integrations: ${response.status}`,
          );
        }

        interface IntegrationApiResponse {
          id: string;
          provider: string;
          user_id: string | null;
          is_active: boolean;
          last_sync_at: string | null;
          last_error: string | null;
          connected_at: string | null;
          connected_by: string | null;
          scope: "organization" | "user";
          share_synced_data: boolean;
          share_query_access: boolean;
          share_write_access: boolean;
          pending_sharing_config: boolean;
          is_owner: boolean;
          current_user_connected: boolean;
          team_connections: Array<{
            user_id: string;
            user_name: string;
          }>;
          team_total: number;
          sync_stats: SyncStats | null;
        }

        const data = (await response.json()) as {
          integrations: IntegrationApiResponse[];
        };

        const integrations: Integration[] = data.integrations.map((i) => ({
          id: i.id,
          provider: i.provider,
          userId: i.user_id,
          isActive: i.is_active,
          lastSyncAt: i.last_sync_at,
          lastError: i.last_error,
          connectedAt: i.connected_at,
          connectedBy: i.connected_by,
          scope: i.scope ?? "user",
          shareSyncedData: i.share_synced_data,
          shareQueryAccess: i.share_query_access,
          shareWriteAccess: i.share_write_access,
          pendingSharingConfig: i.pending_sharing_config,
          isOwner: i.is_owner,
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
        msg.id === streamingMessageId
          ? { ...msg, isStreaming: false }
          : msg,
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
  }),
);
