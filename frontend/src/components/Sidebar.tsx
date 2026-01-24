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
import { useAppStore, useIsGlobalAdmin, type UserProfile } from '../store';

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
}: SidebarProps): JSX.Element {
  // Read user directly from store to ensure we always have the latest value
  const user = useAppStore((state) => state.user);
  const isGlobalAdmin = useIsGlobalAdmin();
  const sidebarWidth = collapsed ? 'w-16' : 'w-64';

  return (
    <aside
      className={`${sidebarWidth} bg-surface-900 border-r border-surface-800 flex flex-col transition-all duration-200 ease-in-out`}
    >
      {/* Header with logo and collapse toggle */}
      <div className="h-14 flex items-center justify-between px-3 border-b border-surface-800">
        {!collapsed && (
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center">
              <img src="/logo.svg" alt="Revtops" className="w-5 h-5 invert" />
            </div>
            <span className="font-semibold text-surface-100">Revtops</span>
            <span className="px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide bg-primary-500/20 text-primary-400 rounded">
              Beta
            </span>
          </div>
        )}
        {collapsed && (
          <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center mx-auto">
            <img src="/logo.svg" alt="Revtops" className="w-6 h-6 invert" />
          </div>
        )}
        <button
          onClick={onToggleCollapse}
          className={`p-1.5 rounded-md text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors ${collapsed ? 'mx-auto mt-2' : ''}`}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            {collapsed ? (
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 5l7 7-7 7M5 5l7 7-7 7" />
            ) : (
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
            )}
          </svg>
        </button>
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
          {!collapsed && <span>Data Sources</span>}
        </button>

        {/* Chats */}
        <button
          onClick={() => onViewChange('chats-list')}
          className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors ${
            currentView === 'chats-list'
              ? 'bg-surface-800 text-surface-100'
              : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800/50'
          } ${collapsed ? 'justify-center' : ''}`}
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
          </svg>
          {!collapsed && <span>Chats</span>}
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
            {recentChats.map((chat) => (
              <div
                key={chat.id}
                className={`relative w-full text-left px-3 py-2 rounded-lg transition-colors group cursor-pointer ${
                  currentChatId === chat.id
                    ? 'bg-surface-800 text-surface-100'
                    : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800/50'
                }`}
                onClick={() => onSelectChat(chat.id)}
              >
                <div className="truncate text-sm pr-6">{chat.title}</div>
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
            ))}
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
