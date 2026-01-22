/**
 * Zustand store for global application state.
 * 
 * Centralizes:
 * - User authentication state
 * - Organization data
 * - Connected integrations
 * - UI state (sidebar, current view)
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { API_BASE } from '../lib/api';

// =============================================================================
// Types
// =============================================================================

export interface UserProfile {
  id: string;
  email: string;
  name: string | null;
  avatarUrl: string | null;
}

export interface OrganizationInfo {
  id: string;
  name: string;
  logoUrl: string | null;
  memberCount: number;
}

export interface Integration {
  id: string;
  provider: string;
  name: string;
  description: string;
  connected: boolean;
  lastSyncAt: string | null;
  lastError: string | null;
  icon: string; // Icon identifier, not JSX
  color: string;
}

export interface ChatSummary {
  id: string;
  title: string;
  lastMessageAt: Date;
  previewText: string;
}

export interface ToolCallData {
  toolName: string;
  toolId: string;
  input: Record<string, unknown>;
  result?: Record<string, unknown>;
  status: 'running' | 'complete' | 'error';
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'tool';
  content: string;
  timestamp: Date;
  isStreaming?: boolean;
  toolName?: string;
  toolCall?: ToolCallData;
}

export type View = 'chat' | 'data-sources' | 'chats-list';

// =============================================================================
// Store Interface
// =============================================================================

interface AppState {
  // Auth
  user: UserProfile | null;
  organization: OrganizationInfo | null;
  isAuthenticated: boolean;
  
  // Integrations
  integrations: Integration[];
  integrationsLoading: boolean;
  
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
  
  // Computed
  connectedIntegrationsCount: number;
  
  // Actions - Auth
  setUser: (user: UserProfile | null) => void;
  setOrganization: (org: OrganizationInfo | null) => void;
  logout: () => void;
  
  // Actions - Integrations
  fetchIntegrations: () => Promise<void>;
  setIntegrations: (integrations: Integration[]) => void;
  
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
  syncUserToBackend: () => Promise<void>;
}

// =============================================================================
// Available Integrations (static config)
// =============================================================================

const AVAILABLE_INTEGRATIONS: Omit<Integration, 'connected' | 'lastSyncAt' | 'lastError'>[] = [
  {
    id: 'hubspot',
    provider: 'hubspot',
    name: 'HubSpot',
    description: 'CRM data including deals, contacts, and companies',
    icon: 'hubspot',
    color: 'from-orange-500 to-orange-600',
  },
  {
    id: 'salesforce',
    provider: 'salesforce',
    name: 'Salesforce',
    description: 'Opportunities, accounts, contacts, and activities',
    icon: 'salesforce',
    color: 'from-blue-500 to-blue-600',
  },
  {
    id: 'slack',
    provider: 'slack',
    name: 'Slack',
    description: 'Team messages and communication history',
    icon: 'slack',
    color: 'from-purple-500 to-purple-600',
  },
  {
    id: 'google-calendar',
    provider: 'google-calendar',
    name: 'Google Calendar',
    description: 'Meetings, events, and scheduling data',
    icon: 'google-calendar',
    color: 'from-green-500 to-green-600',
  },
  {
    id: 'microsoft_calendar',
    provider: 'microsoft_calendar',
    name: 'Microsoft Calendar',
    description: 'Outlook calendar events and meetings',
    icon: 'microsoft_calendar',
    color: 'from-sky-500 to-sky-600',
  },
  {
    id: 'microsoft_mail',
    provider: 'microsoft_mail',
    name: 'Microsoft Mail',
    description: 'Outlook emails and communications',
    icon: 'microsoft_mail',
    color: 'from-blue-500 to-blue-600',
  },
];

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
      integrations: [],
      integrationsLoading: false,
      sidebarCollapsed: false,
      currentView: 'chat',
      currentChatId: null,
      recentChats: [],
      connectedIntegrationsCount: 0,
      
      // Chat state
      messages: [],
      chatTitle: 'New Chat',
      isThinking: false,
      streamingMessageId: null,
      conversationId: null,

      // Auth actions
      setUser: (user) => set({ 
        user, 
        isAuthenticated: user !== null 
      }),
      
      setOrganization: (organization) => set({ organization }),
      
      logout: () => set({
        user: null,
        organization: null,
        isAuthenticated: false,
        integrations: [],
        currentChatId: null,
        recentChats: [],
        connectedIntegrationsCount: 0,
        // Clear chat state
        messages: [],
        chatTitle: 'New Chat',
        isThinking: false,
        streamingMessageId: null,
        conversationId: null,
      }),

      // Integrations actions
      fetchIntegrations: async () => {
        const { organization } = get();
        if (!organization) {
          console.log('[Store] No organization, skipping integrations fetch');
          return;
        }

        set({ integrationsLoading: true });
        
        try {
          console.log('[Store] Fetching integrations for org:', organization.id);
          const response = await fetch(
            `${API_BASE}/auth/integrations?organization_id=${organization.id}`
          );
          
          if (!response.ok) {
            console.error('[Store] Failed to fetch integrations:', response.status);
            set({ integrationsLoading: false });
            return;
          }

          const data = await response.json() as { 
            integrations: { 
              provider: string; 
              last_sync_at: string | null;
              last_error: string | null;
            }[] 
          };
          
          console.log('[Store] Integrations response:', data);

          // Build connected map
          const connectedMap: Record<string, { lastSyncAt: string | null; lastError: string | null }> = {};
          for (const integration of data.integrations || []) {
            connectedMap[integration.provider] = {
              lastSyncAt: integration.last_sync_at,
              lastError: integration.last_error,
            };
          }

          // Merge with available integrations
          const integrations: Integration[] = AVAILABLE_INTEGRATIONS.map((i) => ({
            ...i,
            connected: i.provider in connectedMap,
            lastSyncAt: connectedMap[i.provider]?.lastSyncAt ?? null,
            lastError: connectedMap[i.provider]?.lastError ?? null,
          }));

          const connectedCount = integrations.filter(i => i.connected).length;
          console.log('[Store] Connected count:', connectedCount);

          set({ 
            integrations, 
            integrationsLoading: false,
            connectedIntegrationsCount: connectedCount,
          });
        } catch (error) {
          console.error('[Store] Error fetching integrations:', error);
          set({ integrationsLoading: false });
        }
      },

      setIntegrations: (integrations) => set({ 
        integrations,
        connectedIntegrationsCount: integrations.filter(i => i.connected).length,
      }),

      // UI actions
      setSidebarCollapsed: (sidebarCollapsed) => set({ sidebarCollapsed }),
      setCurrentView: (currentView) => set({ currentView }),
      setCurrentChatId: (currentChatId) => set({ currentChatId }),
      startNewChat: () => set({ currentChatId: null, currentView: 'chat' }),

      // Conversation actions
      addConversation: (id, title) => {
        const { recentChats } = get();
        // Avoid duplicates
        if (recentChats.some((chat) => chat.id === id)) {
          console.log('[Store] Conversation already exists:', id);
          return;
        }
        console.log('[Store] Adding conversation:', id, title);
        // Only update recentChats - don't change currentChatId
        // The Chat component tracks the conversation internally via conversationIdRef
        // Changing currentChatId mid-stream can cause the chatId prop to change
        // and trigger unwanted re-renders/effects
        set({
          recentChats: [
            { id, title, lastMessageAt: new Date(), previewText: '' },
            ...recentChats.slice(0, 9),
          ],
        });
      },

      fetchConversations: async () => {
        const { user } = get();
        if (!user) {
          console.log('[Store] No user, skipping conversations fetch');
          return;
        }

        try {
          console.log('[Store] Fetching conversations for user:', user.id);
          const response = await fetch(
            `${API_BASE}/chat/conversations?user_id=${user.id}&limit=20`
          );

          if (!response.ok) {
            console.error('[Store] Failed to fetch conversations:', response.status);
            return;
          }

          const data = await response.json() as {
            conversations: Array<{
              id: string;
              title: string | null;
              updated_at: string;
              last_message_preview: string | null;
            }>;
            total: number;
          };

          console.log('[Store] Conversations response:', data.conversations.length, 'conversations');

          const recentChats: ChatSummary[] = data.conversations.map((conv) => ({
            id: conv.id,
            title: conv.title ?? 'New Chat',
            lastMessageAt: new Date(conv.updated_at),
            previewText: conv.last_message_preview ?? '',
          }));

          set({ recentChats });
        } catch (error) {
          console.error('[Store] Error fetching conversations:', error);
        }
      },

      deleteConversation: async (id) => {
        const { user, recentChats, currentChatId, conversationId } = get();
        if (!user) return;

        // Check if conversation exists in our list (prevent double-delete)
        if (!recentChats.some((chat) => chat.id === id)) {
          console.log('[Store] Conversation already removed, skipping delete:', id);
          return;
        }

        // Optimistically remove from UI first
        const updated = recentChats.filter((chat) => chat.id !== id);
        const shouldClearChat = currentChatId === id || conversationId === id;
        
        set({
          recentChats: updated,
          ...(shouldClearChat ? {
            currentChatId: null,
            conversationId: null,
            messages: [],
            chatTitle: 'New Chat',
            isThinking: false,
            streamingMessageId: null,
          } : {}),
        });

        try {
          console.log('[Store] Deleting conversation:', id);
          const response = await fetch(
            `${API_BASE}/chat/conversations/${id}?user_id=${user.id}`,
            { method: 'DELETE' }
          );

          if (!response.ok && response.status !== 404) {
            // 404 is fine - already deleted
            console.error('[Store] Failed to delete conversation:', response.status);
            // Could restore the chat here if needed, but usually not worth it
          }
          
          console.log('[Store] Conversation deleted');
        } catch (error) {
          console.error('[Store] Error deleting conversation:', error);
        }
      },

      // Chat message actions
      setMessages: (messages) => set({ messages }),
      
      addMessage: (message) => {
        const { messages } = get();
        console.log('[Store] Adding message:', message.role, message.id);
        set({ messages: [...messages, message] });
      },
      
      appendToStreamingMessage: (content) => {
        const { messages, streamingMessageId } = get();
        if (!streamingMessageId) {
          console.warn('[Store] No streaming message to append to');
          return;
        }
        const updated = messages.map((msg) =>
          msg.id === streamingMessageId
            ? { ...msg, content: msg.content + content }
            : msg
        );
        set({ messages: updated });
      },
      
      startStreamingMessage: (id, initialContent) => {
        const { messages } = get();
        console.log('[Store] Starting streaming message:', id);
        const newMessage: ChatMessage = {
          id,
          role: 'assistant',
          content: initialContent,
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
        console.log('[Store] Marking message complete:', streamingMessageId);
        if (!streamingMessageId) return;
        const updated = messages.map((msg) =>
          msg.id === streamingMessageId
            ? { ...msg, isStreaming: false }
            : msg
        );
        set({ messages: updated, streamingMessageId: null });
      },
      
      setChatTitle: (chatTitle) => set({ chatTitle }),
      setIsThinking: (isThinking) => set({ isThinking }),
      setConversationId: (conversationId) => set({ conversationId }),
      
      clearChat: () => set({
        messages: [],
        chatTitle: 'New Chat',
        isThinking: false,
        streamingMessageId: null,
        conversationId: null,
      }),
      
      updateToolMessage: (toolId, updates) => {
        const { messages } = get();
        const updated = messages.map((msg) => {
          if (msg.toolCall?.toolId === toolId) {
            return {
              ...msg,
              toolCall: { ...msg.toolCall, ...updates },
            };
          }
          return msg;
        });
        set({ messages: updated });
      },

      // Sync user to backend
      syncUserToBackend: async () => {
        const { user, organization } = get();
        if (!user) return;

        try {
          console.log('[Store] Syncing user to backend:', user.id, user.email, organization?.id);
          const response = await fetch(`${API_BASE}/auth/users/sync`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              id: user.id,
              email: user.email,
              name: user.name,
              avatar_url: user.avatarUrl,
              organization_id: organization?.id,
            }),
          });

          if (!response.ok) {
            const errorData = await response.json().catch(() => ({})) as { detail?: string };
            throw new Error(errorData.detail ?? `HTTP ${response.status}`);
          }

          console.log('[Store] User synced successfully');
        } catch (error) {
          console.error('[Store] Failed to sync user to backend:', error);
        }
      },
    }),
    {
      name: 'revtops-store',
      // Persist user/org and UI state to survive tab switches
      partialize: (state) => ({
        user: state.user,
        organization: state.organization,
        isAuthenticated: state.isAuthenticated,
        sidebarCollapsed: state.sidebarCollapsed,
      }),
    }
  )
);

// =============================================================================
// Selector Hooks (for convenience)
// =============================================================================

export const useUser = () => useAppStore((state) => state.user);
export const useOrganization = () => useAppStore((state) => state.organization);
export const useIsAuthenticated = () => useAppStore((state) => state.isAuthenticated);
export const useIntegrations = () => useAppStore((state) => state.integrations);
export const useConnectedCount = () => useAppStore((state) => state.connectedIntegrationsCount);
export const useSidebarCollapsed = () => useAppStore((state) => state.sidebarCollapsed);
export const useCurrentView = () => useAppStore((state) => state.currentView);

// Chat selectors
export const useMessages = () => useAppStore((state) => state.messages);
export const useChatTitle = () => useAppStore((state) => state.chatTitle);
export const useIsThinking = () => useAppStore((state) => state.isThinking);
export const useConversationId = () => useAppStore((state) => state.conversationId);
