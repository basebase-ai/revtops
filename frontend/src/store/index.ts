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
      // Only persist certain fields
      partialize: (state) => ({
        sidebarCollapsed: state.sidebarCollapsed,
        // Don't persist user/org - let Supabase be the source of truth
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
