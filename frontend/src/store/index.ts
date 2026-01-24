/**
 * Zustand store for global application state.
 *
 * Centralizes:
 * - User authentication state
 * - Organization data
 * - UI state (sidebar, current view)
 * - Chat state (messages, streaming)
 * 
 * Note: Integrations are managed via React Query (see hooks/useIntegrations.ts)
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import { API_BASE } from "../lib/api";

// =============================================================================
// Types
// =============================================================================

export interface UserProfile {
  id: string;
  email: string;
  name: string | null;
  avatarUrl: string | null;
  roles: string[]; // Global roles like ['global_admin']
}

export interface OrganizationInfo {
  id: string;
  name: string;
  logoUrl: string | null;
  memberCount: number;
}

export interface ChatSummary {
  id: string;
  title: string;
  lastMessageAt: Date;
  previewText: string;
}

// Content block types (matches API)
export interface TextBlock {
  type: 'text';
  text: string;
}

export interface ToolUseBlock {
  type: 'tool_use';
  id: string;
  name: string;
  input: Record<string, unknown>;
  result?: Record<string, unknown>;
  status?: 'pending' | 'running' | 'complete';
}

export type ContentBlock = TextBlock | ToolUseBlock;

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

export type View = "chat" | "data-sources" | "chats-list" | "admin";

// =============================================================================
// Store Interface
// =============================================================================

interface AppState {
  // Auth
  user: UserProfile | null;
  organization: OrganizationInfo | null;
  isAuthenticated: boolean;

  // UI State
  sidebarCollapsed: boolean;
  currentView: View;
  currentChatId: string | null;
  recentChats: ChatSummary[];

  // Chat State
  messages: ChatMessage[];
  chatTitle: string;
  isThinking: boolean;
  streamingMessageId: string | null;
  conversationId: string | null;

  // Actions - Auth
  setUser: (user: UserProfile | null) => void;
  setOrganization: (org: OrganizationInfo | null) => void;
  logout: () => void;

  // Actions - UI
  setSidebarCollapsed: (collapsed: boolean) => void;
  setCurrentView: (view: View) => void;
  setCurrentChatId: (id: string | null) => void;
  startNewChat: () => void;

  // Actions - Conversations
  addConversation: (id: string, title: string) => void;
  fetchConversations: () => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;

  // Actions - Chat Messages
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
// Store Implementation
// =============================================================================

export const useAppStore = create<AppState>()(
  persist(
    (set, get) => ({
      // Initial state
      user: null,
      organization: null,
      isAuthenticated: false,
      sidebarCollapsed: false,
      currentView: "chat",
      currentChatId: null,
      recentChats: [],

      // Chat state
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

      logout: () =>
        set({
          user: null,
          organization: null,
          isAuthenticated: false,
          currentChatId: null,
          recentChats: [],
          // Clear chat state
          messages: [],
          chatTitle: "New Chat",
          isThinking: false,
          streamingMessageId: null,
          conversationId: null,
        }),

      // UI actions
      setSidebarCollapsed: (sidebarCollapsed) => set({ sidebarCollapsed }),
      setCurrentView: (currentView) => set({ currentView }),
      setCurrentChatId: (currentChatId) => set({ currentChatId }),
      startNewChat: () => set({ currentChatId: null, currentView: "chat" }),

      // Conversation actions
      addConversation: (id, title) => {
        const { recentChats } = get();
        // Avoid duplicates
        if (recentChats.some((chat) => chat.id === id)) {
          console.log("[Store] Conversation already exists:", id);
          return;
        }
        console.log("[Store] Adding conversation:", id, title);
        // Only update recentChats - don't change currentChatId
        // The Chat component tracks the conversation internally via conversationIdRef
        // Changing currentChatId mid-stream can cause the chatId prop to change
        // and trigger unwanted re-renders/effects
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
          const response = await fetch(
            `${API_BASE}/chat/conversations?user_id=${user.id}&limit=20`,
          );

          if (!response.ok) {
            console.error(
              "[Store] Failed to fetch conversations:",
              response.status,
            );
            return;
          }

          const data = (await response.json()) as {
            conversations: Array<{
              id: string;
              title: string | null;
              updated_at: string;
              last_message_preview: string | null;
            }>;
            total: number;
          };

          console.log(
            "[Store] Conversations response:",
            data.conversations.length,
            "conversations",
          );

          const recentChats: ChatSummary[] = data.conversations.map((conv) => ({
            id: conv.id,
            title: conv.title ?? "New Chat",
            lastMessageAt: new Date(conv.updated_at),
            previewText: conv.last_message_preview ?? "",
          }));

          set({ recentChats });
        } catch (error) {
          console.error("[Store] Error fetching conversations:", error);
        }
      },

      deleteConversation: async (id) => {
        const { user, recentChats, currentChatId, conversationId } = get();
        if (!user) return;

        // Check if conversation exists in our list (prevent double-delete)
        if (!recentChats.some((chat) => chat.id === id)) {
          console.log(
            "[Store] Conversation already removed, skipping delete:",
            id,
          );
          return;
        }

        // Optimistically remove from UI first
        const updated = recentChats.filter((chat) => chat.id !== id);
        const shouldClearChat = currentChatId === id || conversationId === id;

        set({
          recentChats: updated,
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
          const response = await fetch(
            `${API_BASE}/chat/conversations/${id}?user_id=${user.id}`,
            { method: "DELETE" },
          );

          if (!response.ok && response.status !== 404) {
            // 404 is fine - already deleted
            console.error(
              "[Store] Failed to delete conversation:",
              response.status,
            );
            // Could restore the chat here if needed, but usually not worth it
          }

          console.log("[Store] Conversation deleted");
        } catch (error) {
          console.error("[Store] Error deleting conversation:", error);
        }
      },

      // Chat message actions
      setMessages: (messages) => set({ messages }),

      addMessage: (message) => {
        const { messages } = get();
        console.log("[Store] Adding message:", message.role, message.id);
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
          // Append to the last text block, or create one if needed
          const blocks = [...(msg.contentBlocks ?? [])];
          const lastBlock = blocks[blocks.length - 1];
          if (lastBlock && lastBlock.type === 'text') {
            blocks[blocks.length - 1] = { ...lastBlock, text: lastBlock.text + content };
          } else {
            blocks.push({ type: 'text', text: content });
          }
          return { ...msg, contentBlocks: blocks };
        });
        set({ messages: updated });
      },

      startStreamingMessage: (id, initialContent) => {
        const { messages } = get();
        console.log("[Store] Starting streaming message:", id);
        const newMessage: ChatMessage = {
          id,
          role: "assistant",
          contentBlocks: initialContent ? [{ type: 'text', text: initialContent }] : [],
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
        console.log("[Store] Marking message complete:", streamingMessageId);
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
          // Find tool_use blocks that match the toolId
          const hasMatchingTool = blocks.some(
            (block) => block.type === 'tool_use' && block.id === toolId
          );
          if (!hasMatchingTool) return msg;
          
          // Update the matching tool_use block
          const updatedBlocks = blocks.map((block) => {
            if (block.type === 'tool_use' && block.id === toolId) {
              return {
                ...block,
                result: updates.result ?? block.result,
                status: updates.status as 'pending' | 'running' | 'complete' | undefined ?? block.status,
              };
            }
            return block;
          });
          return { ...msg, contentBlocks: updatedBlocks };
        });
        set({ messages: updated });
      },

      // Sync user to backend - returns user status ('waitlist', 'invited', 'active') or error string
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
              organization_id: organization?.id,
            }),
          });

          if (!response.ok) {
            // 403 means user needs to join waitlist first
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
            id: string;  // Database user ID (may differ from Supabase ID for waitlist users)
            status: string;
            avatar_url: string | null;
            name: string | null;
            roles: string[];
            organization: {
              id: string;
              name: string;
              logo_url: string | null;
            } | null;
          };
          console.log("[Store] User synced successfully, status:", data.status);

          // Update user with data from backend (authoritative source)
          // Always use the database ID from backend - may differ from Supabase ID for waitlist users
          const newRoles = data.roles ?? [];
          if (
            data.id !== user.id ||
            data.avatar_url !== user.avatarUrl ||
            data.name !== user.name ||
            JSON.stringify(newRoles) !== JSON.stringify(user.roles)
          ) {
            setUser({
              ...user,
              id: data.id,
              name: data.name ?? user.name,
              avatarUrl: data.avatar_url ?? user.avatarUrl,
              roles: newRoles,
            });
          }

          // Update organization with data from backend (includes logo_url)
          if (data.organization) {
            const { setOrganization } = get();
            setOrganization({
              id: data.organization.id,
              name: data.organization.name,
              logoUrl: data.organization.logo_url,
              memberCount: organization?.memberCount ?? 1,
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
      // Persist user/org and UI state to survive tab switches
      partialize: (state) => ({
        user: state.user,
        organization: state.organization,
        isAuthenticated: state.isAuthenticated,
        sidebarCollapsed: state.sidebarCollapsed,
      }),
    },
  ),
);

// =============================================================================
// Selector Hooks (for convenience)
// =============================================================================

export const useUser = () => useAppStore((state) => state.user);
export const useOrganization = () => useAppStore((state) => state.organization);
export const useIsAuthenticated = () =>
  useAppStore((state) => state.isAuthenticated);
export const useSidebarCollapsed = () =>
  useAppStore((state) => state.sidebarCollapsed);
export const useCurrentView = () => useAppStore((state) => state.currentView);
export const useIsGlobalAdmin = () =>
  useAppStore((state) => state.user?.roles?.includes("global_admin") ?? false);

// Chat selectors
export const useMessages = () => useAppStore((state) => state.messages);
export const useChatTitle = () => useAppStore((state) => state.chatTitle);
export const useIsThinking = () => useAppStore((state) => state.isThinking);
export const useConversationId = () =>
  useAppStore((state) => state.conversationId);
