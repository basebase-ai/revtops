/**
 * Main application layout with collapsible sidebar.
 * 
 * Modeled after Claude's UX with:
 * - Collapsible left sidebar (icons when collapsed)
 * - New Chat button
 * - Data Sources tab with badge
 * - Chats tab with recent conversations
 * - Organization & Profile sections at bottom
 */

import { useState, useEffect, createContext, useContext, useCallback } from 'react';
import { Sidebar } from './Sidebar';
import { DataSources } from './DataSources';
import { ChatsList } from './ChatsList';
import { Chat } from './Chat';
import { OrganizationPanel } from './OrganizationPanel';
import { ProfilePanel } from './ProfilePanel';

// API base URL
const PRODUCTION_BACKEND = 'https://revtops-backend-production.up.railway.app';
const isProduction = typeof window !== 'undefined' && 
  (window.location.hostname.includes('railway.app') || 
   window.location.hostname.includes('revtops'));
const API_BASE = isProduction ? `${PRODUCTION_BACKEND}/api` : '/api';

// Types
export type View = 'chat' | 'data-sources' | 'chats-list';

export interface ChatSummary {
  id: string;
  title: string;
  lastMessageAt: Date;
  previewText: string;
}

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

export interface IntegrationStatus {
  id: string;
  provider: string;
  name: string;
  description: string;
  connected: boolean;
  lastSyncAt: string | null;
  icon: string;
}

// Context for sharing state across components
interface AppContextValue {
  currentView: View;
  setCurrentView: (view: View) => void;
  currentChatId: string | null;
  setCurrentChatId: (id: string | null) => void;
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (collapsed: boolean) => void;
  startNewChat: () => void;
}

const AppContext = createContext<AppContextValue | null>(null);

export function useAppContext(): AppContextValue {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error('useAppContext must be used within AppLayout');
  }
  return context;
}

// Props
interface AppLayoutProps {
  user: UserProfile;
  organization: OrganizationInfo;
  onLogout: () => void;
}

export function AppLayout({ user, organization, onLogout }: AppLayoutProps): JSX.Element {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [currentView, setCurrentView] = useState<View>('chat');
  const [currentChatId, setCurrentChatId] = useState<string | null>(null);
  const [recentChats, setRecentChats] = useState<ChatSummary[]>([]);
  const [connectedSourcesCount, setConnectedSourcesCount] = useState(0);
  
  // Panels
  const [showOrgPanel, setShowOrgPanel] = useState(false);
  const [showProfilePanel, setShowProfilePanel] = useState(false);

  // Load recent chats
  useEffect(() => {
    // TODO: Fetch from API
    // For now, using mock data
    setRecentChats([
      {
        id: '1',
        title: 'Q4 Pipeline Analysis',
        lastMessageAt: new Date(Date.now() - 1000 * 60 * 30),
        previewText: 'Show me deals closing this quarter...',
      },
      {
        id: '2',
        title: 'Enterprise Account Review',
        lastMessageAt: new Date(Date.now() - 1000 * 60 * 60 * 2),
        previewText: 'Which enterprise accounts need attention?',
      },
      {
        id: '3',
        title: 'Sales Team Performance',
        lastMessageAt: new Date(Date.now() - 1000 * 60 * 60 * 24),
        previewText: 'Compare rep performance this month...',
      },
    ]);
  }, []);

  // Load connected sources count from API
  const loadConnectedSourcesCount = useCallback(async () => {
    console.log('[AppLayout] Loading connected sources for org:', organization.id);
    try {
      const response = await fetch(`${API_BASE}/auth/integrations?organization_id=${organization.id}`);
      console.log('[AppLayout] Integrations response status:', response.status);
      if (response.ok) {
        const data = await response.json() as { integrations: { provider: string }[] };
        console.log('[AppLayout] Integrations data:', data);
        // Count integrations that exist (same logic as DataSources.tsx)
        const connectedCount = data.integrations?.length ?? 0;
        console.log('[AppLayout] Connected count:', connectedCount);
        setConnectedSourcesCount(connectedCount);
      }
    } catch (error) {
      console.error('[AppLayout] Failed to load connected sources count:', error);
    }
  }, [organization.id]);

  useEffect(() => {
    void loadConnectedSourcesCount();
  }, [loadConnectedSourcesCount]);

  const startNewChat = (): void => {
    setCurrentChatId(null);
    setCurrentView('chat');
  };

  const handleSelectChat = (chatId: string): void => {
    setCurrentChatId(chatId);
    setCurrentView('chat');
  };

  const contextValue: AppContextValue = {
    currentView,
    setCurrentView,
    currentChatId,
    setCurrentChatId,
    sidebarCollapsed,
    setSidebarCollapsed,
    startNewChat,
  };

  return (
    <AppContext.Provider value={contextValue}>
      <div className="min-h-screen flex bg-surface-950">
        {/* Sidebar */}
        <Sidebar
          collapsed={sidebarCollapsed}
          onToggleCollapse={() => setSidebarCollapsed(!sidebarCollapsed)}
          currentView={currentView}
          onViewChange={setCurrentView}
          connectedSourcesCount={connectedSourcesCount}
          recentChats={recentChats.slice(0, 10)}
          onSelectChat={handleSelectChat}
          currentChatId={currentChatId}
          onNewChat={startNewChat}
          user={user}
          organization={organization}
          onOpenOrgPanel={() => setShowOrgPanel(true)}
          onOpenProfilePanel={() => setShowProfilePanel(true)}
        />

        {/* Main Content */}
        <main className="flex-1 flex flex-col min-w-0">
          {currentView === 'chat' && (
            <Chat
              userId={user.id}
              organizationId={organization.id}
              chatId={currentChatId}
              onChatCreated={(id, title) => {
                setCurrentChatId(id);
                // Add to recent chats
                setRecentChats((prev) => [
                  { id, title, lastMessageAt: new Date(), previewText: '' },
                  ...prev.slice(0, 9),
                ]);
              }}
            />
          )}
          {currentView === 'data-sources' && (
            <DataSources organizationId={organization.id} />
          )}
          {currentView === 'chats-list' && (
            <ChatsList
              chats={recentChats}
              onSelectChat={handleSelectChat}
              onNewChat={startNewChat}
            />
          )}
        </main>

        {/* Organization Panel */}
        {showOrgPanel && (
          <OrganizationPanel
            organization={organization}
            currentUser={user}
            onClose={() => setShowOrgPanel(false)}
          />
        )}

        {/* Profile Panel */}
        {showProfilePanel && (
          <ProfilePanel
            user={user}
            onClose={() => setShowProfilePanel(false)}
            onLogout={onLogout}
          />
        )}
      </div>
    </AppContext.Provider>
  );
}
