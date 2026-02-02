/**
 * Collapsible sidebar navigation.
 * 
 * Features:
 * - Expand/collapse toggle
 * - New Chat button
 * - Data Sources tab with badge
 * - Chats tab
 * - Recent chats list
 * - Organization section
 * - Profile section
 */

import { useState } from 'react';
import type { View, ChatSummary, OrganizationInfo } from './AppLayout';
import { useAppStore, useIsGlobalAdmin, useActiveTasksByConversation, type UserProfile } from '../store';

/** Avatar component with error fallback */
function UserAvatar({ user }: { user: UserProfile }): JSX.Element {
  const [imgError, setImgError] = useState(false);
  
  if (user.avatarUrl && !imgError) {
    return (
      <img
        src={user.avatarUrl}
        alt={user.name ?? user.email}
        className="w-8 h-8 rounded-full object-cover"
        referrerPolicy="no-referrer"
        onError={() => setImgError(true)}
      />
    );
  }
  
  return (
    <div className="w-8 h-8 rounded-full bg-primary-600 flex items-center justify-center text-white font-medium text-sm">
      {(user.name ?? user.email).charAt(0).toUpperCase()}
    </div>
  );
}

interface SidebarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
  currentView: View;
  onViewChange: (view: View) => void;
  connectedSourcesCount: number;
  recentChats: ChatSummary[];
  onSelectChat: (id: string) => void;
  onDeleteChat: (id: string) => void;
  currentChatId: string | null;
  onNewChat: () => void;
  organization: OrganizationInfo;
  memberCount: number;
  onOpenOrgPanel: () => void;
  onOpenProfilePanel: () => void;
  isMobile?: boolean;
  onCloseMobile?: () => void;
}

export function Sidebar({
  collapsed,
  onToggleCollapse,
  currentView,
  onViewChange,
  connectedSourcesCount,
  recentChats,
  onSelectChat,
  onDeleteChat,
  currentChatId,
  onNewChat,
  organization,
  memberCount,
  onOpenOrgPanel,
  onOpenProfilePanel,
  isMobile = false,
  onCloseMobile,
}: SidebarProps): JSX.Element {
  // Read user directly from store to ensure we always have the latest value
  const user = useAppStore((state) => state.user);
  const isGlobalAdmin = useIsGlobalAdmin();
  const activeTasksByConversation = useActiveTasksByConversation();
  const sidebarWidth = collapsed ? 'w-16' : 'w-64';

  return (
    <aside
      className={`${sidebarWidth} h-full bg-surface-900 border-r border-surface-800 flex flex-col transition-all duration-200 ease-in-out`}
    >
      {/* Header with logo and collapse toggle */}
      <div className={`border-b border-surface-800 ${collapsed ? 'py-3' : 'h-14 flex items-center justify-between px-3'}`}>
        {!collapsed && (
          <>
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center">
                <img src="/logo.svg" alt="Revtops" className="w-5 h-5 invert" />
              </div>
              <span className="font-semibold text-surface-100">Revtops</span>
              <span className="px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide bg-primary-500/20 text-primary-400 rounded">
                Beta
              </span>
            </div>
            {isMobile ? (
              <button
                onClick={onCloseMobile}
                className="p-1.5 rounded-md text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors"
                title="Close menu"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            ) : (
              <button
                onClick={onToggleCollapse}
                className="p-1.5 rounded-md text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors"
                title="Collapse sidebar"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
                </svg>
              </button>
            )}
          </>
        )}
        {collapsed && (
          <div className="flex flex-col items-center gap-2">
            <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center">
              <img src="/logo.svg" alt="Revtops" className="w-6 h-6 invert" />
            </div>
            <button
              onClick={onToggleCollapse}
              className="p-1.5 rounded-md text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors"
              title="Expand sidebar"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 5l7 7-7 7M5 5l7 7-7 7" />
              </svg>
            </button>
          </div>
        )}
      </div>

      {/* New Chat Button */}
      <div className="p-3">
        <button
          onClick={onNewChat}
          className={`w-full flex items-center gap-2 px-3 py-2.5 rounded-lg bg-primary-600 hover:bg-primary-700 text-white font-medium transition-colors ${collapsed ? 'justify-center' : ''}`}
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          {!collapsed && <span>New Chat</span>}
        </button>
      </div>

      {/* Navigation Tabs */}
      <nav className="px-3 space-y-1">
        {/* Home */}
        <button
          onClick={() => onViewChange('home')}
          className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors ${
            currentView === 'home'
              ? 'bg-surface-800 text-surface-100'
              : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800/50'
          } ${collapsed ? 'justify-center' : ''}`}
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
          </svg>
          {!collapsed && <span>Home</span>}
        </button>

        {/* Data Sources */}
        <button
          onClick={() => onViewChange('data-sources')}
          className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors ${
            currentView === 'data-sources'
              ? 'bg-surface-800 text-surface-100'
              : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800/50'
          } ${collapsed ? 'justify-center' : ''}`}
        >
          <div className="relative">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4" />
            </svg>
            {connectedSourcesCount > 0 && (
              <span className="absolute -top-1.5 -right-1.5 w-4 h-4 bg-primary-500 rounded-full text-[10px] font-bold text-white flex items-center justify-center">
                {connectedSourcesCount}
              </span>
            )}
          </div>
          {!collapsed && <span>Sources</span>}
        </button>

        {/* Data Inspector */}
        <button
          onClick={() => onViewChange('data')}
          className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors ${
            currentView === 'data'
              ? 'bg-surface-800 text-surface-100'
              : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800/50'
          } ${collapsed ? 'justify-center' : ''}`}
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 10h18M3 14h18m-9-4v8m-7 0h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
          </svg>
          {!collapsed && <span>Data</span>}
        </button>

        {/* Search */}
        <button
          onClick={() => onViewChange('search')}
          className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors ${
            currentView === 'search'
              ? 'bg-surface-800 text-surface-100'
              : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800/50'
          } ${collapsed ? 'justify-center' : ''}`}
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          {!collapsed && <span>Search</span>}
        </button>

        {/* Automations */}
        <button
          onClick={() => onViewChange('automations')}
          className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors ${
            currentView === 'automations'
              ? 'bg-surface-800 text-surface-100'
              : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800/50'
          } ${collapsed ? 'justify-center' : ''}`}
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
          {!collapsed && <span>Automations</span>}
        </button>

        {/* Admin - only visible to global admins */}
        {isGlobalAdmin && (
          <button
            onClick={() => onViewChange('admin')}
            className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors ${
              currentView === 'admin'
                ? 'bg-surface-800 text-surface-100'
                : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800/50'
            } ${collapsed ? 'justify-center' : ''}`}
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            {!collapsed && <span>Admin</span>}
          </button>
        )}
      </nav>

      {/* Divider */}
      <div className="mx-3 my-3 border-t border-surface-800" />

      {/* Recent Chats */}
      {!collapsed && (
        <div className="flex-1 overflow-y-auto px-3">
          <h3 className="text-xs font-medium text-surface-500 uppercase tracking-wider mb-2 px-3">
            Recent
          </h3>
          <div className="space-y-0.5">
            {recentChats.map((chat) => {
              const hasActiveTask = chat.id in activeTasksByConversation;
              return (
                <div
                  key={chat.id}
                  className={`relative w-full text-left px-3 py-2 rounded-lg transition-colors group cursor-pointer ${
                    currentChatId === chat.id
                      ? 'bg-surface-800 text-surface-100'
                      : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800/50'
                  }`}
                  onClick={() => onSelectChat(chat.id)}
                >
                  <div className="flex items-center gap-1.5 pr-6">
                    <div className="truncate text-sm">{chat.title}</div>
                    {hasActiveTask && (
                      <svg className="w-3 h-3 text-primary-400 flex-shrink-0 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                      </svg>
                    )}
                  </div>
                  <div className="text-xs text-surface-500 truncate mt-0.5">
                    {formatRelativeTime(chat.lastMessageAt)}
                  </div>
                  {/* Delete button - appears on hover */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteChat(chat.id);
                    }}
                    className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-surface-700 text-surface-500 hover:text-surface-300 transition-all"
                    title="Delete conversation"
                  >
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {collapsed && <div className="flex-1" />}

      {/* Bottom Section */}
      <div className="mt-auto border-t border-surface-800">
        {/* Organization */}
        <button
          onClick={onOpenOrgPanel}
          className={`w-full flex items-center gap-3 px-3 py-3 hover:bg-surface-800/50 transition-colors ${collapsed ? 'justify-center' : ''}`}
        >
          {organization.logoUrl ? (
            <img
              src={organization.logoUrl}
              alt={organization.name}
              className="w-8 h-8 rounded-lg object-cover"
            />
          ) : (
            <div className="w-8 h-8 rounded-lg bg-surface-700 flex items-center justify-center text-surface-300 font-medium text-sm">
              {organization.name.charAt(0).toUpperCase()}
            </div>
          )}
          {!collapsed && (
            <div className="flex-1 min-w-0 text-left">
              <div className="text-sm font-medium text-surface-200 truncate">
                {organization.name}
              </div>
              <div className="text-xs text-surface-500">
                {memberCount} member{memberCount !== 1 ? 's' : ''}
              </div>
            </div>
          )}
        </button>

        {/* User Profile */}
        {user && (
          <button
            onClick={onOpenProfilePanel}
            className={`w-full flex items-center gap-3 px-3 py-3 hover:bg-surface-800/50 transition-colors border-t border-surface-800 ${collapsed ? 'justify-center' : ''}`}
          >
            <UserAvatar user={user} />
            {!collapsed && (
              <div className="flex-1 min-w-0 text-left">
                <div className="text-sm font-medium text-surface-200 truncate">
                  {user.name ?? 'User'}
                </div>
                <div className="text-xs text-surface-500 truncate">{user.email}</div>
              </div>
            )}
          </button>
        )}
      </div>
    </aside>
  );
}

function formatRelativeTime(date: Date): string {
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / (1000 * 60));
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}
