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
import { createPortal } from 'react-dom';
import type { View, ChatSummary, OrganizationInfo } from './AppLayout';
import { useAppStore, useAuthStore, useChatStore, useIsGlobalAdmin, useActiveTasksByConversation, type UserOrganization } from '../store';
import { apiRequest } from '../lib/api';
import { FaLifeRing } from 'react-icons/fa';
import { Avatar, type AvatarUser } from './Avatar';
import { ScopeLockIcon } from './ScopeVisibilityIcons';
import { APP_NAME, LOGO_PATH, RELEASE_STAGE } from '../lib/brand';

/** Help button and modal for support requests. */
function HelpButton(): JSX.Element {
  const [showModal, setShowModal] = useState(false);
  const [message, setMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = useCallback(async (): Promise<void> => {
    const trimmed = message.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    setError(null);
    const { error: err } = await apiRequest<{ status: string; detail: string }>('/support/request', {
      method: 'POST',
      body: JSON.stringify({ message: trimmed }),
    });
    setSubmitting(false);
    if (err) {
      setError(err);
      return;
    }
    setSuccess(true);
    setMessage('');
  }, [message, submitting]);

  const handleClose = useCallback((): void => {
    setShowModal(false);
    setSuccess(false);
    setError(null);
    setMessage('');
  }, []);

  return (
    <>
      <button
        onClick={() => setShowModal(true)}
        title="Get Immediate Help"
        className="mr-0.5 p-2 rounded-lg text-amber-400 hover:text-amber-300 hover:bg-amber-500/15 transition-colors"
        aria-label="Get Immediate Help"
      >
        <FaLifeRing className="w-[18px] h-[18px]" />
      </button>
      {showModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          onClick={(e) => { if (e.target === e.currentTarget) handleClose(); }}
        >
          <div
            className="bg-surface-900 border border-surface-700 rounded-xl shadow-xl max-w-md w-full p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-surface-100">Get Help</h2>
              <button
                onClick={handleClose}
                className="p-1 text-surface-400 hover:text-surface-200 rounded transition-colors"
                aria-label="Close"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            {success ? (
              <p className="text-sm text-surface-300 mb-4">
                Your message has been sent. A team member will be notified immediately and will respond within a few minutes during business hours.
              </p>
            ) : (
              <>
                <p className="text-sm text-surface-300 mb-4">
                  You&apos;re our partner in building this product. Share questions, feedback, feature requests, or suggestions of any kind—we read every message and respond within a few minutes during business hours.
                </p>
                <textarea
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder="Questions, feedback, feature requests, or suggestions..."
                  rows={4}
                  className="w-full px-3 py-2 rounded-lg bg-surface-800 border border-surface-700 text-surface-100 placeholder-surface-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent resize-none mb-4"
                  maxLength={4000}
                />
                {error && <p className="text-sm text-red-400 mb-2">{error}</p>}
              </>
            )}
            <div className="flex justify-end gap-2">
              {success ? (
                <button
                  onClick={handleClose}
                  className="px-4 py-2 rounded-lg bg-primary-600 hover:bg-primary-700 text-white font-medium transition-colors"
                >
                  Done
                </button>
              ) : (
                <>
                  <button
                    onClick={handleClose}
                    className="px-4 py-2 rounded-lg text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => void handleSubmit()}
                    disabled={!message.trim() || submitting}
                    className="px-4 py-2 rounded-lg bg-primary-600 hover:bg-primary-700 text-white font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {submitting ? 'Sending...' : 'Send'}
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

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
  isMobile,
}: {
  organization: OrganizationInfo;
  members: AvatarUser[];
  creditsDisplay: { balance: number; included: number } | null;
  onOpenOrgPanel: () => void;
  onOpenBilling: () => void;
  onCreateNewOrg: () => void;
  isMobile: boolean;
}): JSX.Element {
  const organizations: UserOrganization[] = useAppStore((state) => state.organizations);
  const switchActiveOrganization = useAppStore((state) => state.switchActiveOrganization);
  const fetchConversations = useAppStore((state) => state.fetchConversations);
  const fetchIntegrations = useAppStore((state) => state.fetchIntegrations);
  const [showDropdown, setShowDropdown] = useState(false);
  const [dropdownRect, setDropdownRect] = useState<{ top: number; left: number; width: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dropdownContentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent): void => {
      const target = e.target as Node;
      if (
        triggerRef.current?.contains(target) ||
        dropdownContentRef.current?.contains(target)
      ) {
        return;
      }
      setShowDropdown(false);
    };
    if (showDropdown) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showDropdown]);

  useEffect(() => {
    if (showDropdown && triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      const inset = 8; // matches original mx-2
      setDropdownRect({
        top: rect.bottom + 4,
        left: rect.left + inset,
        width: Math.max(200, rect.width - inset * 2),
      });
    } else {
      setDropdownRect(null);
    }
  }, [showDropdown]);

  const handleSwitchOrg = async (orgId: string): Promise<void> => {
    setShowDropdown(false);
    useAuthStore.setState({ isSwitchingOrg: true });
    try {
      await switchActiveOrganization(orgId);
      await Promise.all([fetchConversations(), fetchIntegrations()]);
    } finally {
      useAuthStore.setState({ isSwitchingOrg: false });
    }
  };

  return (
    <div className="relative">
      {/* Org identity row */}
      <div className="relative">
        <button
          ref={triggerRef}
          onClick={() => setShowDropdown((prev) => !prev)}
          className="w-full flex items-center gap-3 px-3 pt-3 pb-1 hover:bg-surface-800/50 transition-colors"
        >
          {organization.logoUrl ? (
            <img
              src={organization.logoUrl}
              alt={organization.name}
              className="w-9 h-9 rounded-lg object-cover flex-shrink-0 self-start mt-0.5"
            />
          ) : (
            <div className="w-9 h-9 rounded-lg bg-surface-800 flex items-center justify-center flex-shrink-0 self-start mt-0.5">
              <img src={LOGO_PATH} alt={APP_NAME} className="w-6 h-6" />
            </div>
          )}
          <div className="flex-1 min-w-0 text-left">
            <div className="text-lg font-semibold text-surface-100 truncate leading-tight">
              {organization.name}
            </div>
            {RELEASE_STAGE.stage && (
              <div className="">
                <span className="px-1.5 py-0.5 rounded bg-primary-500/15 text-primary-400/80 font-medium text-[10px] uppercase tracking-wide" title={RELEASE_STAGE.description}>
                  {RELEASE_STAGE.message}
                </span>
              </div>
            )}
          </div>
          {!isMobile && (
            <svg className="w-4 h-4 text-surface-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          )}
        </button>

        {/* Org switcher dropdown — rendered in portal to escape overflow clipping */}
        {showDropdown && dropdownRect != null && createPortal(
          <div
            ref={dropdownContentRef}
            role="menu"
            className="fixed bg-surface-800 border border-surface-700 rounded-lg shadow-xl overflow-hidden z-[9999]"
            style={{ top: dropdownRect.top, left: dropdownRect.left, width: dropdownRect.width }}
          >
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
              <div className="flex items-center justify-between px-3 py-2.5">
                <span className="text-sm text-surface-400">New Team</span>
                <button
                  type="button"
                  onClick={() => { setShowDropdown(false); onCreateNewOrg(); }}
                  className="shrink-0 px-2.5 py-1 rounded-md bg-primary-600 hover:bg-primary-500 text-white text-xs font-medium transition-colors"
                >
                  + Create
                </button>
              </div>
            </div>
          </div>,
          document.body
        )}
      </div>

      {/* Team members row */}
      {members.length > 0 && (
        <div className="flex items-center gap-2 px-3 py-1.5">
          <button
            onClick={onOpenOrgPanel}
            className="flex-1 min-w-0 flex items-center gap-2 hover:bg-surface-800/50 transition-colors rounded -mx-1 px-1 py-0.5"
          >
            <span className="text-xs text-surface-500 shrink-0">
              {members.length} {members.length !== 1 ? 'members' : 'member'}
            </span>
            <div className="flex -space-x-1.5 ml-auto">
              {members.filter((m) => m.avatarUrl && !m.isGuest).slice(0, 3).map((m, idx) => (
                <img
                  key={m.id}
                  src={m.avatarUrl!}
                  alt={m.name ?? m.email ?? ''}
                  referrerPolicy="no-referrer"
                  className="w-6 h-6 rounded-full object-cover border border-surface-700 dark:border-surface-600"
                  style={{ zIndex: 3 - idx }}
                />
              ))}
            </div>
          </button>
          <button
            onClick={onOpenOrgPanel}
            className="shrink-0 px-2.5 py-1 rounded-md bg-primary-600 hover:bg-primary-500 text-white text-xs font-medium transition-colors"
          >
            + Invite
          </button>
        </div>
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
      className={`w-full flex items-center gap-2 px-3 py-1.5 rounded-lg transition-colors ${isActive ? activeClass : inactiveClass} ${collapsed ? 'justify-center' : ''}`}
    >
      {badge != null && badge > 0 ? (
        <div className="relative">
          {icon}
          <span className={`absolute -top-1.5 -right-1.5 min-w-[16px] h-4 px-1 ${badgeBg} rounded-full text-[10px] font-bold text-white flex items-center justify-center`}>
            {badge}
          </span>
        </div>
      ) : icon}
      {!collapsed && (
        <span className={fontWeight === 'medium' ? 'text-sm font-medium' : 'text-sm'}>{label}</span>
      )}
    </button>
  );
}

export function Sidebar({
  collapsed,
  // onToggleCollapse — kept in SidebarProps for future re-introduction
  currentView,
  onViewChange,
  connectedSourcesCount,
  workflowCount,
  pendingChangesCount,
  recentChats,
  onSelectChat,
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
  const isGlobalAdmin = useIsGlobalAdmin();
  const activeTasksByConversation = useActiveTasksByConversation();
  const storedWidth = useAppStore((state) => state.sidebarWidth);
  const widthPx = collapsed ? 64 : storedWidth;

  // Draggable divider between nav and chat history
  const [navHeight, setNavHeight] = useState<number | null>(null);
  const isDraggingDividerRef = useRef(false);
  const startYRef = useRef(0);
  const startNavHeightRef = useRef(0);
  const navRef = useRef<HTMLDivElement>(null);

  const handleNavDividerMouseDown = useCallback((e: React.MouseEvent): void => {
    e.preventDefault();
    isDraggingDividerRef.current = true;
    startYRef.current = e.clientY;
    startNavHeightRef.current = navRef.current?.getBoundingClientRect().height ?? 200;
    document.body.style.cursor = 'ns-resize';
    document.body.style.userSelect = 'none';

    const onMouseMove = (ev: MouseEvent): void => {
      if (!isDraggingDividerRef.current) return;
      const delta = ev.clientY - startYRef.current;
      const newHeight = Math.min(500, Math.max(80, startNavHeightRef.current + delta));
      setNavHeight(newHeight);
    };
    const onMouseUp = (): void => {
      isDraggingDividerRef.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  }, []);

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
      className="h-full bg-surface-900 border-r border-surface-800 flex flex-col transition-all duration-200 ease-in-out flex-shrink-0 overflow-hidden"
    >
      {/* Header: Organization identity */}
      <div className="border-b border-surface-800 relative min-w-0 overflow-hidden flex-shrink-0">
        {isMobile && (
          <button
            onClick={onCloseMobile}
            className="absolute right-2 top-3 z-10 p-1.5 rounded-md text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors shrink-0"
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
          isMobile={isMobile}
        />
      </div>

      {/* New Chat Button */}
      <div className="px-2 py-1">
        <button
          onClick={onNewChat}
          className={`w-full flex items-center gap-2 px-3 py-1.5 rounded-lg bg-primary-600 hover:bg-primary-700 text-white font-medium text-sm transition-colors ${collapsed ? 'justify-center' : ''}`}
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          {!collapsed && <span>New Chat</span>}
        </button>
      </div>

      {/* Navigation Tabs — scrollable pane */}
      <div
        ref={navRef}
        className="overflow-y-auto scrollbar-thin flex-shrink-0"
        style={navHeight != null ? { height: navHeight } : undefined}
      >
        <nav className="px-2 space-y-0.5">
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
          <NavItem view="documents" label="Documents" activeViews={['documents', 'artifact-view']} collapsed={collapsed} currentView={currentView} onViewChange={onViewChange} icon={
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
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
              className={`w-full flex items-center gap-2 px-3 py-1.5 rounded-lg transition-colors ${
                currentView === 'admin'
                  ? 'bg-surface-800 text-surface-100'
                  : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800/50'
              } ${collapsed ? 'justify-center' : ''}`}
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              {!collapsed && <span className="text-sm">Global Admin</span>}
            </button>
          )}
        </nav>
      </div>

      {/* Draggable divider between nav and chat history */}
      <div
        onMouseDown={handleNavDividerMouseDown}
        className="mx-3 h-1.5 cursor-ns-resize flex-shrink-0 group flex items-center justify-center"
      >
        <div className="w-full border-t border-surface-800 group-hover:border-surface-600 group-active:border-primary-500 transition-colors" />
      </div>

      {/* Recent chats (single list) */}
      <ChatAccordion
        collapsed={collapsed}
        orderedChats={orderedChats}
        currentChatId={currentChatId}
        activeTasksByConversation={activeTasksByConversation}
        onSelectChat={onSelectChat}
        onViewAll={() => onViewChange('chats')}
      />

      {collapsed && <div className="flex-1" />}

      {/* Bottom Section */}
      <div className="mt-auto border-t border-surface-800">
        {/* User Profile + Help */}
        {user && (
          <div className="flex items-center border-t border-surface-800">
            <button
              onClick={onOpenProfilePanel}
              className={`flex-1 min-w-0 flex items-center gap-3 px-3 py-3 hover:bg-surface-800/50 transition-colors ${collapsed ? 'justify-center' : ''}`}
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
            <HelpButton />
          </div>
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

/** Recent chats: shared + private in one list (recency), pinned first; lock marks private. Row actions live in the chat ⋮ menu. */
function ChatAccordion({
  collapsed,
  orderedChats,
  currentChatId,
  activeTasksByConversation,
  onSelectChat,
  onViewAll,
}: {
  collapsed: boolean;
  orderedChats: ChatSummary[];
  currentChatId: string | null;
  activeTasksByConversation: Record<string, string>;
  onSelectChat: (id: string) => void;
  onViewAll: () => void;
}): JSX.Element | null {
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prefetchConversation = useAppStore((s) => s.prefetchConversation);
  const pinnedChatIds = useAppStore((s) => s.pinnedChatIds);
  const unreadConversationIds = useChatStore((s) => s.unreadConversationIds);

  useEffect(() => {
    return () => { if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current); };
  }, []);

  const recentSidebarChats = useMemo(() => {
    const pinnedSet = new Set(pinnedChatIds);
    const sorted = [...orderedChats].sort(
      (a, b) => b.lastMessageAt.getTime() - a.lastMessageAt.getTime(),
    );
    const pinned = sorted.filter((c) => pinnedSet.has(c.id));
    const unpinned = sorted.filter((c) => !pinnedSet.has(c.id));
    const merged = [...pinned, ...unpinned];
    const limit = 15;
    return merged.slice(0, limit);
  }, [orderedChats, pinnedChatIds]);

  if (collapsed) return null;

  const renderChatItem = (chat: ChatSummary): JSX.Element => {
    const hasActiveTask = chat.id in activeTasksByConversation;
    const isUnread = unreadConversationIds.has(chat.id);

    return (
      <div
        key={chat.id}
        className={`relative w-full text-left px-2 py-1 rounded-md transition-colors cursor-pointer leading-tight ${
          currentChatId === chat.id
            ? 'bg-surface-800 text-surface-100'
            : 'text-surface-400 hover:text-surface-200 hover:bg-surface-800/50'
        }`}
        onClick={() => onSelectChat(chat.id)}
        onMouseEnter={() => {
          if (currentChatId === chat.id) return;
          hoverTimerRef.current = setTimeout(() => prefetchConversation(chat.id), 100);
        }}
        onMouseLeave={() => {
          if (hoverTimerRef.current) { clearTimeout(hoverTimerRef.current); hoverTimerRef.current = null; }
        }}
      >
        <div className="flex items-center gap-1">
          {chat.scope === 'private' && (
            <span className="flex shrink-0 text-surface-500" title="Private">
              <ScopeLockIcon className="w-3 h-3" />
            </span>
          )}
          {chat.type === 'workflow' && (
            <svg className="w-3.5 h-3.5 text-amber-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          )}
          <div className="truncate text-sm flex-1 leading-tight">
            {chat.title}
          </div>
          {isUnread && (
            <span
              className="h-3 w-3 shrink-0 rounded-full bg-primary-500 [background-image:none]"
              title="Unread"
              aria-label="Unread"
            />
          )}
          {hasActiveTask && (
            <svg className="w-3 h-3 text-primary-400 flex-shrink-0 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
          )}
        </div>
        <div className="flex items-center gap-1 mt-0 leading-none">
          {chat.scope === 'shared' && chat.participants && chat.participants.length > 0 && (
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
                  className="w-5 h-5 rounded-full border border-surface-700 dark:border-surface-600 bg-surface-700 flex items-center justify-center text-[10px] font-medium text-surface-300"
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
      </div>
    );
  };
  
  return (
    <div className="flex-1 flex flex-col min-h-0 px-1.5">
      {/* Chat History header with View All link */}
      <div className="flex-shrink-0 flex items-center justify-between px-2 py-0.5">
        <span className="text-xs font-semibold text-surface-200 uppercase tracking-wider">Chat History</span>
        <button
          onClick={onViewAll}
          className="text-xs font-medium text-amber-400 hover:text-amber-300 transition-colors"
        >
          View All
        </button>
      </div>

      <div className="flex-1 overflow-y-auto scrollbar-thin space-y-0 min-h-0">
        {recentSidebarChats.length > 0 ? (
          recentSidebarChats.map((chat) => renderChatItem(chat))
        ) : (
          <div className="px-2 py-1.5 text-xs text-surface-500 text-center">
            No conversations yet
          </div>
        )}
      </div>
    </div>
  );
}
