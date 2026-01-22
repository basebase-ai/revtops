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

import { useState, useEffect } from 'react';
import { Sidebar } from './Sidebar';
import { DataSources } from './DataSources';
import { ChatsList } from './ChatsList';
import { Chat } from './Chat';
import { OrganizationPanel } from './OrganizationPanel';
import { ProfilePanel } from './ProfilePanel';
import { useAppStore, type ChatSummary } from '../store';

// Re-export types from store for backwards compatibility
export type { UserProfile, OrganizationInfo, ChatSummary, View } from '../store';

// Props
interface AppLayoutProps {
  onLogout: () => void;
}

export function AppLayout({ onLogout }: AppLayoutProps): JSX.Element {
  // Get state from Zustand store
  const {
    user,
    organization,
    sidebarCollapsed,
    setSidebarCollapsed,
    currentView,
    setCurrentView,
    currentChatId,
    setCurrentChatId,
    startNewChat,
    connectedIntegrationsCount,
    fetchIntegrations,
  } = useAppStore();

  const [recentChats, setRecentChats] = useState<ChatSummary[]>([]);
  
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

  // Fetch integrations on mount (if not already loaded)
  useEffect(() => {
    if (organization) {
      void fetchIntegrations();
    }
  }, [organization, fetchIntegrations]);

  const handleSelectChat = (chatId: string): void => {
    setCurrentChatId(chatId);
    setCurrentView('chat');
  };

  // Guard against missing user/org (shouldn't happen, but be safe)
  if (!user || !organization) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-surface-400">Loading...</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex bg-surface-950">
      {/* Sidebar */}
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed(!sidebarCollapsed)}
        currentView={currentView}
        onViewChange={setCurrentView}
        connectedSourcesCount={connectedIntegrationsCount}
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
          <DataSources />
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
  );
}
