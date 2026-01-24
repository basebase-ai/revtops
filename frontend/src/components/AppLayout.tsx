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

import { useState, useEffect, useCallback } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { Sidebar } from './Sidebar';
import { DataSources } from './DataSources';
import { ChatsList } from './ChatsList';
import { Chat } from './Chat';
import { AdminPanel } from './AdminPanel';
import { OrganizationPanel } from './OrganizationPanel';
import { ProfilePanel } from './ProfilePanel';
import { useAppStore } from '../store';
import { useIntegrations, useTeamMembers } from '../hooks';

// Re-export types from store for backwards compatibility
export type { UserProfile, OrganizationInfo, ChatSummary, View } from '../store';

// Props
interface AppLayoutProps {
  onLogout: () => void;
}

export function AppLayout({ onLogout }: AppLayoutProps): JSX.Element {
  // Get state from Zustand store using shallow comparison to prevent unnecessary re-renders
  const {
    user,
    organization,
    sidebarCollapsed,
    currentView,
    currentChatId,
    recentChats,
  } = useAppStore(
    useShallow((state) => ({
      user: state.user,
      organization: state.organization,
      sidebarCollapsed: state.sidebarCollapsed,
      currentView: state.currentView,
      currentChatId: state.currentChatId,
      recentChats: state.recentChats,
    }))
  );

  // React Query: Get integrations for connected count badge
  const { data: integrations = [] } = useIntegrations(
    organization?.id ?? null, 
    user?.id ?? null
  );
  const connectedIntegrationsCount = integrations.filter((i) => i.isActive).length;

  // React Query: Get team members for member count (single source of truth)
  const { data: teamMembers = [] } = useTeamMembers(
    organization?.id ?? null,
    user?.id ?? null
  );

  // Get actions separately (they're stable and don't need shallow comparison)
  const setSidebarCollapsed = useAppStore((state) => state.setSidebarCollapsed);
  const setCurrentView = useAppStore((state) => state.setCurrentView);
  const setCurrentChatId = useAppStore((state) => state.setCurrentChatId);
  const startNewChat = useAppStore((state) => state.startNewChat);
  const fetchConversations = useAppStore((state) => state.fetchConversations);
  const deleteConversation = useAppStore((state) => state.deleteConversation);
  const setUser = useAppStore((state) => state.setUser);
  
  // Panels
  const [showOrgPanel, setShowOrgPanel] = useState(false);
  const [showProfilePanel, setShowProfilePanel] = useState(false);

  // Fetch conversations on mount
  useEffect(() => {
    if (user) {
      void fetchConversations();
    }
  }, [user, fetchConversations]);

  const handleSelectChat = useCallback((chatId: string): void => {
    setCurrentChatId(chatId);
    setCurrentView('chat');
  }, [setCurrentChatId, setCurrentView]);

  const handleDeleteChat = useCallback((chatId: string): void => {
    void deleteConversation(chatId);
  }, [deleteConversation]);

  // Guard against missing user/org (shouldn't happen, but be safe)
  if (!user || !organization) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-surface-400">Loading...</p>
      </div>
    );
  }

  return (
    <div className="h-screen flex bg-surface-950 overflow-hidden">
      {/* Sidebar */}
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed(!sidebarCollapsed)}
        currentView={currentView}
        onViewChange={setCurrentView}
        connectedSourcesCount={connectedIntegrationsCount}
        recentChats={recentChats.slice(0, 10)}
        onSelectChat={handleSelectChat}
        onDeleteChat={handleDeleteChat}
        currentChatId={currentChatId}
        onNewChat={startNewChat}
        organization={organization}
        memberCount={teamMembers.length}
        onOpenOrgPanel={() => setShowOrgPanel(true)}
        onOpenProfilePanel={() => setShowProfilePanel(true)}
      />

      {/* Main Content */}
      <main className="flex-1 flex flex-col min-w-0 min-h-0 overflow-hidden">
        {currentView === 'chat' && (
          <Chat
            userId={user.id}
            organizationId={organization.id}
            chatId={currentChatId}
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
        {currentView === 'admin' && (
          <AdminPanel />
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
          onUpdateUser={(updates) => setUser({ ...user, ...updates })}
        />
      )}
    </div>
  );
}
