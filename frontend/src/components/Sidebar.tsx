/**
 * Collapsible sidebar navigation.
 * 
 * Features:
 * - Expand/collapse toggle
 * - New Chat button
 * - Connectors tab with badge
 * - Chats tab
 * - Recent chats list
 * - Organization section
 * - Profile section
 */

import { useMemo, useState, useRef, useEffect, useCallback } from 'react';
import type { View, ChatSummary, OrganizationInfo } from './AppLayout';
import { useAppStore, useIsGlobalAdmin, useActiveTasksByConversation, type UserOrganization } from '../store';
import { updateConversation } from '../api/client';
import { Avatar, type AvatarUser } from './Avatar';
import { APP_NAME, LOGO_PATH } from '../lib/brand';

/** Small SVG donut chart showing remaining credits as a ring. */
function CreditDonut({ balance, total }: { balance: number; total: number }): JSX.Element {
  const size = 22;
  const strokeWidth = 2.5;
  const radius: number = (size - strokeWidth) / 2;
  const circumference: number = 2 * Math.PI * radius;
  const pct: number = total > 0 ? Math.max(0, Math.min(1, balance / total)) : 0;
  const dashOffset: number = circumference * (1 - pct);

  const strokeColor = '#9ca3af';

  return (
    <svg width={size} height={size} className="flex-shrink-0">
      <circle
        cx={size / 2} cy={size / 2} r={radius}
        fill="none" stroke="currentColor" strokeWidth={strokeWidth}
        className="text-surface-700"
      />
      {pct > 0 && (
        <circle
          cx={size / 2} cy={size / 2} r={radius}
          fill="none" stroke={strokeColor} strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={dashOffset}
          strokeLinecap="round"
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      )}
    </svg>
  );
}

/** Organization switcher — displayed prominently at the top of the sidebar. */
function OrgSwitcherSection({
  organization,
  members,
  creditsDisplay,
  onOpenOrgPanel,
  onOpenBilling,
  onCreateNewOrg,
}: {
  organization: OrganizationInfo;
  members: AvatarUser[];
  creditsDisplay: { balance: number; included: number } | null;
  onOpenOrgPanel: () => void;
  onOpenBilling: () => void;
  onCreateNewOrg: () => void;
}): JSX.Element {
  const organizations: UserOrganization[] = useAppStore((state) => state.organizations);
  const switchActiveOrganization = useAppStore((state) => state.switchActiveOrganization);
  const fetchConversations = useAppStore((state) => state.fetchConversations);
  const fetchIntegrations = useAppStore((state) => state.fetchIntegrations);
  const [showDropdown, setShowDropdown] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent): void => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    };
    if (showDropdown) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showDropdown]);

  const handleSwitchOrg = async (orgId: string): Promise<void> => {
    setShowDropdown(false);
    await switchActiveOrganization(orgId);
    await Promise.all([fetchConversations(), fetchIntegrations()]);
  };

  return (
    <div className="relative" ref={dropdownRef}>
      {/* Org identity row */}
      <button
        onClick={() => setShowDropdown((prev) => !prev)}
        className="w-full flex items-center gap-3 px-3 pt-3 pb-1 hover:bg-surface-800/50 transition-colors"
      >
        {organization.logoUrl ? (
          <img
            src={organization.logoUrl}
            alt={organization.name}
            className="w-9 h-9 rounded-lg object-cover flex-shrink-0"
          />
        ) : (
          <div className="w-9 h-9 rounded-lg bg-surface-800 flex items-center justify-center flex-shrink-0">
            <img src={LOGO_PATH} alt={APP_NAME} className="w-6 h-6" />
          </div>
        )}
        <span className="text-lg font-semibold text-surface-100 truncate flex-1 text-left leading-tight">
          {organization.name}
        </span>
        <svg className="w-4 h-4 text-surface-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Team members row */}
      {members.length > 0 && (
        <button
          onClick={onOpenOrgPanel}
          className="w-full flex items-center px-3 py-1.5 hover:bg-surface-800/50 transition-colors"
        >
          <span className="text-xs text-surface-500">
            {members.length} {members.length !== 1 ? 'members' : 'member'}
          </span>
          <div className="flex -space-x-1.5 ml-auto">
            {members.filter((m) => m.avatarUrl && !m.isGuest).slice(0, 5).map((m, idx) => (
              <img
                key={m.id}
                src={m.avatarUrl!}
                alt={m.name ?? m.email ?? ''}
                referrerPolicy="no-referrer"
                className="w-6 h-6 rounded-full object-cover border border-surface-800"
                style={{ zIndex: 5 - idx }}
              />
            ))}
          </div>
        </button>
      )}

      {/* Credits row */}
      {creditsDisplay != null && (() => {
        const balance: number = creditsDisplay.balance;
        const isOut: boolean = balance <= 0;
        const pct: number = creditsDisplay.included > 0 ? balance / creditsDisplay.included : 1;
        const isDanger: boolean = !isOut && pct <= 0.05;
        const isWarning: boolean = !isOut && pct <= 0.25 && pct > 0.05;
        const textClass: string = isOut || isDanger ? 'text-red-400' : isWarning ? 'text-amber-400' : 'text-surface-500';
        return (
          <button
            onClick={onOpenBilling}
            className="w-full flex items-center px-3 py-1.5 hover:bg-surface-800/50 transition-colors"
          >
            <span className={`text-xs ${textClass}`}>
              {isOut ? (
                <span className="font-semibold animate-pulse">No credits remaining</span>
              ) : (
                <>{balance} / {creditsDisplay.included} credits</>
              )}
            </span>
            <div className="ml-auto">
              <CreditDonut balance={balance} total={creditsDisplay.included} />
            </div>
          </button>
        );
      })()}

      {/* Bottom padding for the header block */}
      <div className="pb-1" />

      {/* Org switcher dropdown */}
      {showDropdown && (
        <div className="absolute top-full left-0 right-0 mt-1 mx-2 bg-surface-800 border border-surface-700 rounded-lg shadow-xl overflow-hidden z-50">
          <div className="py-1">
            {organizations.map((org) => (
              <div
                key={org.id}
                className={`flex items-center transition-colors ${
                  org.isActive
                    ? 'bg-primary-500/10 text-primary-400'
                    : 'text-surface-300 hover:bg-surface-700'
                }`}
              >
                <button
                  onClick={() => void handleSwitchOrg(org.id)}
                  className="flex-1 min-w-0 flex items-center gap-3 px-3 py-2.5 text-left"
                >
                  {org.logoUrl ? (
                    <img src={org.logoUrl} alt={org.name} className="w-6 h-6 rounded object-cover flex-shrink-0" />
                  ) : (
                    <div className="w-6 h-6 rounded bg-surface-700 flex items-center justify-center flex-shrink-0">
                      <img src={LOGO_PATH} alt={APP_NAME} className="w-4 h-4" />
                    </div>
                  )}
                  <span className="text-sm truncate flex-1">{org.name}</span>
                  {org.isActive && (
                    <svg className="w-4 h-4 text-primary-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                </button>
                {org.isActive && (
                  <button
                    onClick={() => { setShowDropdown(false); onOpenOrgPanel(); }}
                    className="p-1.5 mr-1 rounded-md hover:bg-surface-700/60 transition-colors flex-shrink-0"
                    title="Team settings"
                  >
                    <svg className="w-4 h-4 text-amber-400" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 00.12-.61l-1.92-3.32a.49.49 0 00-.59-.22l-2.39.96c-.5-.38-1.03-.69-1.62-.89l-.36-2.54a.484.484 0 00-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.2-1.13.52-1.62.89l-2.39-.96a.49.49 0 00-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.07.62-.07.94s.02.64.07.94l-2.03 1.58a.49.49 0 00-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.69 1.62.89l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.2 1.13-.52 1.62-.89l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6A3.6 3.6 0 1115.6 12 3.611 3.611 0 0112 15.6z" />
                    </svg>
                  </button>
                )}
              </div>
            ))}
            <div className="border-t border-surface-700 my-1" />
            <button
              type="button"
              onClick={() => { setShowDropdown(false); onCreateNewOrg(); }}
              className="w-full flex items-center gap-3 px-3 py-2.5 text-left text-surface-400 hover:bg-surface-700 hover:text-surface-200 transition-colors"
            >
              <div className="w-6 h-6 rounded bg-surface-600 flex items-center justify-center text-surface-300 text-xs font-medium flex-shrink-0">
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
              </div>
              <span className="text-sm">Create new team</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

interface SidebarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
  currentView: View;
  onViewChange: (view: View) => void;
  connectedSourcesCount: number;
  workflowCount: number;
  pendingChangesCount: number;
  recentChats: ChatSummary[];
  onSelectChat: (id: string) => void;
  onDeleteChat: (id: string) => void;
  currentChatId: string | null;
  onNewChat: () => void;
  organization: OrganizationInfo;
  members: AvatarUser[];
  creditsDisplay: { balance: number; included: number } | null;
  onOpenOrgPanel: () => void;
  onOpenBilling: () => void;
  onCreateNewOrg: () => void;
  onOpenProfilePanel: () => void;
  isMobile?: boolean;
  onCloseMobile?: () => void;
}

/** Shared nav item used by the sidebar navigation. */
function NavItem({
  view,
  activeViews,
  label,
  icon,
  badge,
  badgeColor = 'primary',
  colorTheme = 'surface',
  fontWeight,
  collapsed,
  currentView,
  onViewChange,
}: {
  view: View;
  activeViews?: View[];
  label: string;
  icon: JSX.Element;
  badge?: number;
  badgeColor?: 'primary' | 'amber';
  colorTheme?: 'surface' | 'amber';
  fontWeight?: 'medium';
  collapsed: boolean;
  currentView: View;
  onViewChange: (view: View) => void;
}): JSX.Element {
  const isActive = (activeViews ?? [view]).includes(currentView);
  const activeClass = colorTheme === 'amber'
    ? 'bg-amber-500/20 text-amber-300'
    : 'bg-surface-800 text-surface-100';
  const inactiveClass = colorTheme === 'amber'
    ? 'text-amber-400 hover:text-amber-300 hover:bg-amber-500/10'
    : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800/50';
  const badgeBg = badgeColor === 'amber' ? 'bg-amber-500' : 'bg-primary-500';

  return (
    <button
      onClick={() => onViewChange(view)}
      title={collapsed ? label : undefined}
      className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors ${isActive ? activeClass : inactiveClass} ${collapsed ? 'justify-center' : ''}`}
    >
      {badge != null && badge > 0 ? (
        <div className="relative">
          {icon}
          <span className={`absolute -top-1.5 -right-1.5 min-w-[16px] h-4 px-1 ${badgeBg} rounded-full text-[10px] font-bold text-white flex items-center justify-center`}>
            {badge}
          </span>
        </div>
      ) : icon}
      {!collapsed && <span className={fontWeight === 'medium' ? 'font-medium' : ''}>{label}</span>}
    </button>
  );
}

export function Sidebar({
  collapsed,
  onToggleCollapse: _onToggleCollapse,
  currentView,
  onViewChange,
  connectedSourcesCount,
  workflowCount,
  pendingChangesCount,
  recentChats,
  onSelectChat,
  onDeleteChat,
  currentChatId,
  onNewChat,
  organization,
  members,
  creditsDisplay,
  onOpenOrgPanel,
  onOpenBilling,
  onCreateNewOrg,
  onOpenProfilePanel,
  isMobile = false,
  onCloseMobile,
}: SidebarProps): JSX.Element {
  // Read user directly from store to ensure we always have the latest value
  const user = useAppStore((state) => state.user);
  const pinnedChatIds = useAppStore((state) => state.pinnedChatIds);
  const togglePinChat = useAppStore((state) => state.togglePinChat);
  const isGlobalAdmin = useIsGlobalAdmin();
  const activeTasksByConversation = useActiveTasksByConversation();
  const storedWidth = useAppStore((state) => state.sidebarWidth);
  const widthPx = collapsed ? 64 : storedWidth;
  const orderedChats = useMemo(() => {
    if (pinnedChatIds.length === 0) {
      return recentChats;
    }
    const pinnedSet = new Set(pinnedChatIds);
    const pinned = recentChats.filter((chat) => pinnedSet.has(chat.id));
    const unpinned = recentChats.filter((chat) => !pinnedSet.has(chat.id));
    return [...pinned, ...unpinned];
  }, [pinnedChatIds, recentChats]);

  return (
    <aside
      style={{ width: widthPx }}
      className="h-full bg-surface-900 border-r border-surface-800 flex flex-col transition-all duration-200 ease-in-out flex-shrink-0"
    >
      {/* Header: Organization identity */}
      <div className="border-b border-surface-800 relative">
        {isMobile && (
          <button
            onClick={onCloseMobile}
            className="absolute right-2 top-3 z-10 p-1.5 rounded-md text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors"
            title="Close menu"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        )}
        <OrgSwitcherSection
          organization={organization}
          members={members}
          creditsDisplay={creditsDisplay}
          onOpenOrgPanel={onOpenOrgPanel}
          onOpenBilling={onOpenBilling}
          onCreateNewOrg={onCreateNewOrg}
        />
      </div>

      {/* New Chat Button */}
      <div className="p-2">
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
      <nav className="px-2 space-y-1">
        <NavItem view="home" label="Home" collapsed={collapsed} currentView={currentView} onViewChange={onViewChange} icon={
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
          </svg>
        } />
        <NavItem view="data-sources" label="Connectors" badge={connectedSourcesCount} collapsed={collapsed} currentView={currentView} onViewChange={onViewChange} icon={
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4" />
          </svg>
        } />
        <NavItem view="data" label="Search Data" collapsed={collapsed} currentView={currentView} onViewChange={onViewChange} icon={
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 10h18M3 14h18m-9-4v8m-7 0h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
          </svg>
        } />
        <NavItem view="workflows" label="Workflows" badge={workflowCount} collapsed={collapsed} currentView={currentView} onViewChange={onViewChange} icon={
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
        } />
        <NavItem view="apps" label="Apps" activeViews={['apps', 'app-view']} collapsed={collapsed} currentView={currentView} onViewChange={onViewChange} icon={
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
        } />
        {pendingChangesCount > 0 && (
          <NavItem view="pending-changes" label="Changes" badge={pendingChangesCount} badgeColor="amber" colorTheme="amber" fontWeight="medium" collapsed={collapsed} currentView={currentView} onViewChange={onViewChange} icon={
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
            </svg>
          } />
        )}
        {isGlobalAdmin && (
          <button
            onClick={() => onViewChange('admin')}
            title={collapsed ? 'Global Admin' : undefined}
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
            {!collapsed && <span>Global Admin</span>}
          </button>
        )}
      </nav>

      {/* Divider */}
      <div className="mx-3 my-3 border-t border-surface-800" />

      {/* Recent Chats - Accordion with Shared and Private sections */}
      <ChatAccordion
        collapsed={collapsed}
        orderedChats={orderedChats}
        currentChatId={currentChatId}
        activeTasksByConversation={activeTasksByConversation}
        pinnedChatIds={pinnedChatIds}
        currentUserId={user?.id ?? null}
        onSelectChat={onSelectChat}
        onDeleteChat={onDeleteChat}
        togglePinChat={togglePinChat}
      />

      {collapsed && <div className="flex-1" />}

      {/* Bottom Section */}
      <div className="mt-auto border-t border-surface-800">
        {/* User Profile */}
        {user && (
          <button
            onClick={onOpenProfilePanel}
            className={`w-full flex items-center gap-3 px-3 py-3 hover:bg-surface-800/50 transition-colors border-t border-surface-800 ${collapsed ? 'justify-center' : ''}`}
          >
            <Avatar user={user} size="md" />
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

/** Accordion for chat sections - only one section open at a time */
function ChatAccordion({
  collapsed,
  orderedChats,
  currentChatId,
  activeTasksByConversation,
  pinnedChatIds,
  currentUserId,
  onSelectChat,
  onDeleteChat,
  togglePinChat,
}: {
  collapsed: boolean;
  orderedChats: ChatSummary[];
  currentChatId: string | null;
  activeTasksByConversation: Record<string, string>;
  pinnedChatIds: string[];
  currentUserId: string | null;
  onSelectChat: (id: string) => void;
  onDeleteChat: (id: string) => void;
  togglePinChat: (id: string) => void;
}): JSX.Element | null {
  const [expandedSection, setExpandedSection] = useState<'shared' | 'private'>('shared');
  const [editingChatId, setEditingChatId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState('');
  const editInputRef = useRef<HTMLInputElement>(null);
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const setConversationTitle = useAppStore((s) => s.setConversationTitle);
  const prefetchConversation = useAppStore((s) => s.prefetchConversation);

  useEffect(() => {
    return () => { if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current); };
  }, []);

  const canRename = useCallback((chat: ChatSummary): boolean => {
    if (chat.scope === 'private') return true;
    return chat.userId === currentUserId;
  }, [currentUserId]);

  const startEditing = useCallback((chat: ChatSummary) => {
    if (!canRename(chat)) return;
    setEditingChatId(chat.id);
    setEditingTitle(chat.title);
  }, [canRename]);

  const saveTitle = useCallback(async (chatId: string) => {
    const trimmed = editingTitle.trim();
    setEditingChatId(null);
    if (!trimmed || trimmed === orderedChats.find(c => c.id === chatId)?.title) return;
    setConversationTitle(chatId, trimmed);
    const { error } = await updateConversation(chatId, trimmed);
    if (error) {
      const original = orderedChats.find(c => c.id === chatId)?.title ?? 'New Chat';
      setConversationTitle(chatId, original);
    }
  }, [editingTitle, orderedChats, setConversationTitle]);

  const cancelEditing = useCallback(() => {
    setEditingChatId(null);
  }, []);

  // Auto-focus and select-all when entering edit mode
  useEffect(() => {
    if (editingChatId && editInputRef.current) {
      editInputRef.current.focus();
      editInputRef.current.select();
    }
  }, [editingChatId]);

  if (collapsed) return null;

  const sharedChats = orderedChats.filter(c => c.scope === 'shared').slice(0, 50);
  const privateChats = orderedChats.filter(c => c.scope === 'private').slice(0, 50);

  const renderChatItem = (chat: ChatSummary, showLockIcon: boolean) => {
    const hasActiveTask = chat.id in activeTasksByConversation;
    const isPinned = pinnedChatIds.includes(chat.id);
    const isEditing = editingChatId === chat.id;
    const isRenamable = canRename(chat);

    return (
      <div
        key={chat.id}
        className={`relative w-full text-left px-3 py-2 rounded-lg transition-colors group cursor-pointer ${
          currentChatId === chat.id
            ? 'bg-surface-800 text-surface-100'
            : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800/50'
        }`}
        onClick={() => { if (!isEditing) onSelectChat(chat.id); }}
        onMouseEnter={() => {
          if (currentChatId === chat.id) return;
          hoverTimerRef.current = setTimeout(() => prefetchConversation(chat.id), 100);
        }}
        onMouseLeave={() => {
          if (hoverTimerRef.current) { clearTimeout(hoverTimerRef.current); hoverTimerRef.current = null; }
        }}
      >
        <div className="flex items-center gap-1.5 pr-14">
          {showLockIcon && (
            <svg className="w-3 h-3 text-surface-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
          )}
          {chat.type === 'workflow' && (
            <svg className="w-3.5 h-3.5 text-amber-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          )}
          {isEditing ? (
            <input
              ref={editInputRef}
              type="text"
              value={editingTitle}
              onChange={(e) => setEditingTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void saveTitle(chat.id);
                if (e.key === 'Escape') cancelEditing();
              }}
              onBlur={() => void saveTitle(chat.id)}
              onClick={(e) => e.stopPropagation()}
              className="truncate text-sm flex-1 bg-transparent border-b border-primary-500 outline-none text-surface-100 py-0 px-0"
              maxLength={100}
            />
          ) : (
            <div
              className="truncate text-sm flex-1"
              onDoubleClick={(e) => { e.stopPropagation(); startEditing(chat); }}
            >
              {chat.title}
            </div>
          )}
          {hasActiveTask && (
            <svg className="w-3 h-3 text-primary-400 flex-shrink-0 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
          )}
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          {!showLockIcon && chat.participants && chat.participants.length > 0 && (
            <div className="flex -space-x-1.5">
              {chat.participants.slice(0, 3).map((p, idx) => (
                <Avatar
                  key={p.id}
                  user={p}
                  size="xs"
                  bordered
                  style={{ zIndex: 3 - idx }}
                />
              ))}
              {chat.participants.length > 3 && (
                <div
                  className="w-5 h-5 rounded-full border border-surface-800 bg-surface-700 flex items-center justify-center text-[10px] font-medium text-surface-300"
                  title={`${chat.participants.length - 3} more`}
                >
                  +{chat.participants.length - 3}
                </div>
              )}
            </div>
          )}
          <span className="text-xs text-surface-500">
            {formatRelativeTime(chat.lastMessageAt)}
          </span>
        </div>
        {/* Rename button */}
        {isRenamable && !isEditing && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              startEditing(chat);
            }}
            className="absolute right-12 top-1/2 -translate-y-1/2 p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-surface-700 text-surface-500 hover:text-surface-300 transition-all"
            title="Rename conversation"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
            </svg>
          </button>
        )}
        <button
          onClick={(e) => {
            e.stopPropagation();
            togglePinChat(chat.id);
          }}
          className={`absolute right-7 top-1/2 -translate-y-1/2 p-1 rounded ${
            isPinned ? 'opacity-100 text-primary-400' : 'opacity-0 text-surface-500'
          } group-hover:opacity-100 hover:bg-surface-700 hover:text-surface-300 transition-all`}
          title={isPinned ? "Unpin conversation" : "Pin conversation"}
        >
          <svg className={`w-3.5 h-3.5 ${isPinned ? 'text-primary-400' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
          </svg>
        </button>
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
  };
  
  return (
    <div className="flex-1 flex flex-col min-h-0 px-2">
      {/* Shared Section Header - always visible */}
      <button
        onClick={() => setExpandedSection(expandedSection === 'shared' ? 'private' : 'shared')}
        className="flex-shrink-0 w-full flex items-center justify-between px-3 py-2 text-xs font-medium text-surface-400 hover:text-surface-200 transition-colors"
      >
        <span className="uppercase tracking-wider">Shared ({sharedChats.length})</span>
        <svg
          className={`w-4 h-4 transition-transform ${expandedSection === 'shared' ? 'rotate-180' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      
      {/* Shared Section Content - scrollable */}
      {expandedSection === 'shared' && (
        <div className="flex-1 overflow-y-auto space-y-0.5 min-h-0">
          {sharedChats.length > 0 ? (
            sharedChats.map((chat) => renderChatItem(chat, false))
          ) : (
            <div className="px-3 py-4 text-xs text-surface-500 text-center">
              No shared conversations yet
            </div>
          )}
        </div>
      )}
      
      {/* Private Section Header - always visible */}
      <button
        onClick={() => setExpandedSection(expandedSection === 'private' ? 'shared' : 'private')}
        className="flex-shrink-0 w-full flex items-center justify-between px-3 py-2 text-xs font-medium text-surface-400 hover:text-surface-200 transition-colors border-t border-surface-800 mt-1"
      >
        <span className="uppercase tracking-wider">Private ({privateChats.length})</span>
        <svg
          className={`w-4 h-4 transition-transform ${expandedSection === 'private' ? 'rotate-180' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      
      {/* Private Section Content - scrollable */}
      {expandedSection === 'private' && (
        <div className="flex-1 overflow-y-auto space-y-0.5 min-h-0">
          {privateChats.length > 0 ? (
            privateChats.map((chat) => renderChatItem(chat, true))
          ) : (
            <div className="px-3 py-4 text-xs text-surface-500 text-center">
              No private conversations yet
            </div>
          )}
        </div>
      )}
    </div>
  );
}

