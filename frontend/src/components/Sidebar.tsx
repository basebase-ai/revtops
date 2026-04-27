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
import { useAppStore, useAuthStore, useChatStore, useIsGlobalAdmin, useIsOrgAdmin, useActiveTasksByConversation, type UserOrganization, type AdminPanelTab } from '../store';
import { apiRequest } from '../lib/api';
import { FaLifeRing } from 'react-icons/fa';
import { Avatar, type AvatarUser } from './Avatar';
import { ScopeLockIcon } from './ScopeVisibilityIcons';
import { APP_NAME, LOGO_PATH, RELEASE_STAGE } from '../lib/brand';

const CHANNEL_PERSONALITY_MAX_LENGTH = 2000;
const CHANNEL_PERSONALITY_TEXTAREA_BASE_HEIGHT_PX = 160;
const CHANNEL_PERSONALITY_TEXTAREA_MAX_HEIGHT_PX = Math.round(CHANNEL_PERSONALITY_TEXTAREA_BASE_HEIGHT_PX * 1.5);

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

/** Shield icon for global admin console identity in the org switcher. */
function GlobalAdminShieldIcon({ className }: { className?: string }): JSX.Element {
  return (
    <svg className={className ?? 'w-5 h-5'} fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden>
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
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
  currentView,
  onViewChange,
}: {
  organization: OrganizationInfo;
  members: AvatarUser[];
  creditsDisplay: { balance: number; included: number } | null;
  onOpenOrgPanel: () => void;
  onOpenBilling: () => void;
  onCreateNewOrg: () => void;
  isMobile: boolean;
  currentView: View;
  onViewChange: (view: View) => void;
}): JSX.Element {
  const isGlobalAdmin: boolean = useIsGlobalAdmin();
  const isAdminConsole: boolean = currentView === 'admin';
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
      const switched: boolean = await switchActiveOrganization(orgId);
      if (!switched) {
        alert("You don't have access to that organization.");
        return;
      }
      await Promise.all([fetchConversations(), fetchIntegrations()]);
    } finally {
      useAuthStore.setState({ isSwitchingOrg: false });
    }
  };

  const handleEnterGlobalAdmin = (): void => {
    setShowDropdown(false);
    useAuthStore.setState({
      organizations: organizations.map((o) => ({ ...o, isActive: false })),
    });
    onViewChange('admin');
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
          {isAdminConsole ? (
            <>
              <div className="w-9 h-9 rounded-lg bg-surface-800 flex items-center justify-center flex-shrink-0 self-start mt-0.5 text-amber-400">
                <GlobalAdminShieldIcon className="w-6 h-6" />
              </div>
              <div className="flex-1 min-w-0 text-left">
                <div className="text-lg font-semibold text-surface-100 truncate leading-tight">
                  Global Admin
                </div>
              </div>
            </>
          ) : (
            <>
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
            </>
          )}
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
              {isGlobalAdmin && (
                <>
                  <div className="border-t border-surface-700 my-1" />
                  <button
                    type="button"
                    onClick={handleEnterGlobalAdmin}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors ${
                      isAdminConsole
                        ? 'bg-primary-500/10 text-primary-400'
                        : 'text-surface-300 hover:bg-surface-700'
                    }`}
                  >
                    <div className="w-6 h-6 rounded bg-surface-700 flex items-center justify-center flex-shrink-0 text-amber-400">
                      <GlobalAdminShieldIcon className="w-4 h-4" />
                    </div>
                    <span className="text-sm truncate flex-1">Global Admin</span>
                    {isAdminConsole && (
                      <svg className="w-4 h-4 text-primary-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                    )}
                  </button>
                </>
              )}
            </div>
          </div>,
          document.body
        )}
      </div>

      {/* Team members row */}
      {!isAdminConsole && members.length > 0 && (
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
      {!isAdminConsole && creditsDisplay != null && (() => {
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
    : 'text-surface-300 hover:text-surface-200 hover:bg-surface-800/50';
  const badgeBg = badgeColor === 'amber' ? 'bg-amber-500' : 'bg-primary-500';

  return (
    <button
      onClick={() => onViewChange(view)}
      title={collapsed ? label : undefined}
      className={`w-full flex items-center gap-2 px-3 py-[5px] rounded-lg transition-colors ${isActive ? activeClass : inactiveClass} ${collapsed ? 'justify-center' : ''}`}
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

function GlobalAdminSidebarNavItem({
  label,
  collapsed,
  active,
  onClick,
  icon,
}: {
  label: string;
  collapsed: boolean;
  active: boolean;
  onClick: () => void;
  icon: JSX.Element;
}): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      title={collapsed ? label : undefined}
      className={`w-full flex items-center gap-2 px-3 py-[5px] rounded-lg transition-colors ${
        active ? 'bg-surface-800 text-surface-100' : 'text-surface-300 hover:text-surface-200 hover:bg-surface-800/50'
      } ${collapsed ? 'justify-center' : ''}`}
    >
      {icon}
      {!collapsed && <span className="text-sm">{label}</span>}
    </button>
  );
}

const GLOBAL_ADMIN_NAV_ITEMS: ReadonlyArray<{
  id: AdminPanelTab;
  label: string;
  icon: JSX.Element;
}> = [
  {
    id: 'dashboard',
    label: 'Dashboard',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden>
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
      </svg>
    ),
  },
  {
    id: 'waitlist',
    label: 'Waitlist',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden>
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
      </svg>
    ),
  },
  {
    id: 'users',
    label: 'Users',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden>
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" />
      </svg>
    ),
  },
  {
    id: 'organizations',
    label: 'Teams',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden>
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
      </svg>
    ),
  },
  {
    id: 'sources',
    label: 'Sources & Health',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden>
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4" />
      </svg>
    ),
  },
  {
    id: 'jobs',
    label: 'Running Jobs',
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden>
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
      </svg>
    ),
  },
  {
    id: 'graph-magic',
    label: "UJ's Graph Magic",
    icon: (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden>
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 19l6-6 4 4 6-10" />
      </svg>
    ),
  },
];

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
  const adminPanelTab = useAppStore((state) => state.adminPanelTab);
  const setAdminPanelTab = useAppStore((state) => state.setAdminPanelTab);
  const isGlobalAdmin = useIsGlobalAdmin();
  const isOrgAdmin = useIsOrgAdmin();
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
          currentView={currentView}
          onViewChange={onViewChange}
        />
      </div>

      {currentView !== 'admin' && (
      <>
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
          {isOrgAdmin && (
            <NavItem view="activity-log" label="Activity" collapsed={collapsed} currentView={currentView} onViewChange={onViewChange} icon={
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
              </svg>
            } />
          )}
          <NavItem view="org-settings" label="Settings" collapsed={collapsed} currentView={currentView} onViewChange={onViewChange} icon={
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
          } />
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
      </>
      )}

      {currentView === 'admin' && isGlobalAdmin && (
        <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
          <div className="px-2 py-2 overflow-y-auto scrollbar-thin flex-1">
            <nav className="space-y-0.5" aria-label="Global Admin sections">
              {GLOBAL_ADMIN_NAV_ITEMS.map((item) => (
                <GlobalAdminSidebarNavItem
                  key={item.id}
                  label={item.label}
                  collapsed={collapsed}
                  active={adminPanelTab === item.id}
                  onClick={() => setAdminPanelTab(item.id)}
                  icon={item.icon}
                />
              ))}
            </nav>
          </div>
        </div>
      )}

      {/* New Chat Button */}
      {currentView !== 'admin' && (
        <div className="px-2 py-1">
          <button
            onClick={onNewChat}
            className={`w-full flex items-center gap-2 px-3 py-[5px] rounded-lg bg-primary-600 hover:bg-primary-700 text-white font-medium text-sm transition-colors ${collapsed ? 'justify-center' : ''}`}
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            {!collapsed && <span>New Chat</span>}
          </button>
        </div>
      )}

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

interface ChannelMemoryResponse {
  id: string;
  content: string;
}

function normalizeChannelIdForMemory(source: string | null | undefined, channelKey: string, normalizedChannelId?: string | null): string {
  const raw = (normalizedChannelId ?? '').trim() || channelKey.replace(/^channel:/, '').trim();
  if ((source ?? '').toLowerCase() === 'slack') {
    return raw.split(':', 1)[0] ?? raw;
  }
  return raw;
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
  const [collapsedSections, setCollapsedSections] = useState<Record<string, boolean>>({});
  const [channelPersonalityTarget, setChannelPersonalityTarget] = useState<{
    key: string;
    label: string;
    source: string | null;
    normalizedChannelId: string;
  } | null>(null);
  const prefetchConversation = useAppStore((s) => s.prefetchConversation);
  const pinnedChatIds = useAppStore((s) => s.pinnedChatIds);
  const organizationId = useAppStore((s) => s.organization?.id ?? null);
  const unreadConversationIds = useChatStore((s) => s.unreadConversationIds);

  useEffect(() => {
    return () => { if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current); };
  }, []);

  const groupedSidebarChats = useMemo(() => {
    const pinnedSet = new Set(pinnedChatIds);
    const sorted = [...orderedChats].sort(
      (a, b) => b.lastMessageAt.getTime() - a.lastMessageAt.getTime(),
    );
    const direct: ChatSummary[] = [];
    const uncategorized: ChatSummary[] = [];
    const channels = new Map<string, {
      label: string;
      source: string | null;
      normalizedChannelId: string | null;
      chats: ChatSummary[];
      newestTs: number;
    }>();
    const pinned: ChatSummary[] = [];
    for (const chat of sorted) {
      const ts = chat.lastMessageAt.getTime();
      if (pinnedSet.has(chat.id)) pinned.push(chat);
      const bucket = chat.groupBucketType ?? 'uncategorized';
      if (bucket === 'direct') {
        direct.push(chat);
        continue;
      }
      if (bucket === 'channel' && chat.groupBucketKey) {
        const current = channels.get(chat.groupBucketKey) ?? {
          label: chat.resolvedChannelName ?? chat.normalizedChannelId ?? 'Channel',
          source: chat.source ?? null,
          normalizedChannelId: chat.normalizedChannelId ?? null,
          chats: [],
          newestTs: 0,
        };
        current.chats.push(chat);
        current.newestTs = Math.max(current.newestTs, ts);
        channels.set(chat.groupBucketKey, current);
        continue;
      }
      uncategorized.push(chat);
    }
    const globalLimit = 50;
    const byNewest = (a: ChatSummary, b: ChatSummary): number => b.lastMessageAt.getTime() - a.lastMessageAt.getTime();
    const channelSections = Array.from(channels.entries())
      .map(([key, value]) => ({
        key,
        label: value.label,
        source: value.source,
        normalizedChannelId: value.normalizedChannelId,
        chats: value.chats.sort(byNewest),
        newestTs: value.newestTs,
      }))
      .sort((a, b) => b.newestTs - a.newestTs);
    const flattenCount =
      pinned.length +
      direct.length +
      uncategorized.length +
      channelSections.reduce((acc, c) => acc + c.chats.length, 0);
    let remaining = globalLimit;
    const take = (items: ChatSummary[]): ChatSummary[] => {
      if (remaining <= 0) return [];
      const selected = items.slice(0, remaining);
      remaining -= selected.length;
      return selected;
    };
    const limitedPinned = take(pinned.sort(byNewest));
    const limitedDirect = take(direct.sort(byNewest));
    const limitedChannels = channelSections
      .map((section) => ({ ...section, chats: take(section.chats) }))
      .filter((section) => section.chats.length > 0);
    const limitedUncategorized = take(uncategorized.sort(byNewest));
    return {
      pinned: limitedPinned,
      direct: limitedDirect,
      uncategorized: limitedUncategorized,
      channels: limitedChannels,
      flattenCount,
    };
  }, [orderedChats, pinnedChatIds]);

  if (collapsed) return null;

  const isSectionCollapsed = (sectionKey: string): boolean => {
    const explicit = collapsedSections[sectionKey];
    if (typeof explicit === 'boolean') return explicit;
    return sectionKey !== 'direct';
  };

  const toggleSection = (sectionKey: string): void => {
    setCollapsedSections((prev) => ({
      ...prev,
      [sectionKey]:
        typeof prev[sectionKey] === 'boolean'
          ? !prev[sectionKey]
          : sectionKey === 'direct',
    }));
  };

  const renderChatItem = (chat: ChatSummary, itemKey: string): JSX.Element => {
    const hasActiveTask = chat.id in activeTasksByConversation;
    const isUnread = unreadConversationIds.has(chat.id);

    return (
      <div
        key={itemKey}
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
    <div className="flex-1 flex flex-col min-h-0 px-1.5 py-px">
      {/* Chat History header — whole row is clickable to open All Chats (with search) */}
      <button
        type="button"
        onClick={onViewAll}
        className="flex-shrink-0 flex items-center justify-between w-full px-2 py-1.5 rounded-md hover:bg-surface-800 transition-colors group"
      >
        <span className="text-xs font-semibold text-surface-200 uppercase tracking-wider group-hover:text-surface-100">Chat History</span>
        <span className="flex items-center gap-1 text-xs font-medium text-primary-400 group-hover:text-primary-300">
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          View All
        </span>
      </button>

      <div className="flex-1 overflow-y-auto scrollbar-thin space-y-0 min-h-0">
        {groupedSidebarChats.flattenCount > 0 ? (
          <>
            {groupedSidebarChats.pinned.length > 0 && (
              <>
                <SidebarSectionHeader
                  title="Pinned"
                  collapsed={isSectionCollapsed('pinned')}
                  onToggle={() => toggleSection('pinned')}
                />
                {!isSectionCollapsed('pinned') && groupedSidebarChats.pinned.map((chat) => renderChatItem(chat, `pinned-${chat.id}`))}
              </>
            )}
            {groupedSidebarChats.direct.length > 0 && (
              <>
                <SidebarSectionHeader
                  title="Direct"
                  collapsed={isSectionCollapsed('direct')}
                  onToggle={() => toggleSection('direct')}
                />
                {!isSectionCollapsed('direct') && groupedSidebarChats.direct.map((chat) => renderChatItem(chat, `direct-${chat.id}`))}
              </>
            )}
            {groupedSidebarChats.channels.map((channel) => (
              <div key={channel.key}>
                <SidebarSectionHeader
                  title={channel.label}
                  collapsed={isSectionCollapsed(`channel:${channel.key}`)}
                  onToggle={() => toggleSection(`channel:${channel.key}`)}
                  onOptionsClick={() => {
                    const normalizedChannelId = normalizeChannelIdForMemory(
                      channel.source,
                      channel.key,
                      channel.normalizedChannelId,
                    );
                    setChannelPersonalityTarget({
                      key: channel.key,
                      label: channel.label,
                      source: channel.source,
                      normalizedChannelId,
                    });
                  }}
                />
                {!isSectionCollapsed(`channel:${channel.key}`) &&
                  channel.chats.map((chat) => renderChatItem(chat, `channel-${channel.key}-${chat.id}`))}
              </div>
            ))}
            {groupedSidebarChats.uncategorized.length > 0 && (
              <>
                <SidebarSectionHeader
                  title="Uncategorized"
                  collapsed={isSectionCollapsed('uncategorized')}
                  onToggle={() => toggleSection('uncategorized')}
                />
                {!isSectionCollapsed('uncategorized') &&
                  groupedSidebarChats.uncategorized.map((chat) => renderChatItem(chat, `uncategorized-${chat.id}`))}
              </>
            )}
          </>
        ) : (
          <div className="px-2 py-1.5 text-xs text-surface-500 text-center">
            No conversations yet
          </div>
        )}
      </div>
      {channelPersonalityTarget && (
        <ChannelPersonalityPanel
          organizationId={organizationId}
          channelName={channelPersonalityTarget.label}
          source={channelPersonalityTarget.source}
          normalizedChannelId={channelPersonalityTarget.normalizedChannelId}
          onClose={() => setChannelPersonalityTarget(null)}
        />
      )}
    </div>
  );
}

function SidebarSectionHeader({
  title,
  collapsed,
  onToggle,
  onOptionsClick,
}: {
  title: string;
  collapsed: boolean;
  onToggle: () => void;
  onOptionsClick?: () => void;
}): JSX.Element {
  return (
    <div className="px-1 pt-2 pb-1 flex items-center gap-1">
      <button
        type="button"
        onClick={onToggle}
        className="flex-1 min-w-0 px-1 py-0.5 rounded-md hover:bg-surface-800/60 transition-colors flex items-center gap-1.5 text-left"
        aria-expanded={!collapsed}
        aria-label={`${collapsed ? 'Expand' : 'Collapse'} ${title}`}
      >
        <svg
          className={`w-3 h-3 text-surface-500 transition-transform ${collapsed ? '' : 'rotate-90'}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <h3 className="truncate text-[10px] uppercase tracking-wider text-surface-500 font-semibold">{title}</h3>
      </button>
      {onOptionsClick && (
        <button
          type="button"
          className="p-1 rounded-md text-surface-500 hover:bg-surface-800/60 hover:text-surface-300 transition-colors"
          aria-label={`${title} options`}
          onClick={onOptionsClick}
        >
          <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="5" cy="12" r="1.8" />
            <circle cx="12" cy="12" r="1.8" />
            <circle cx="19" cy="12" r="1.8" />
          </svg>
        </button>
      )}
    </div>
  );
}

function ChannelPersonalityPanel({
  organizationId,
  channelName,
  source,
  normalizedChannelId,
  onClose,
}: {
  organizationId: string | null;
  channelName: string;
  source: string | null;
  normalizedChannelId: string;
  onClose: () => void;
}): JSX.Element {
  const [draft, setDraft] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isDirty, setIsDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastSavedRef = useRef('');
  const saveTimeoutRef = useRef<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = `${CHANNEL_PERSONALITY_TEXTAREA_BASE_HEIGHT_PX}px`;
    textarea.style.height = `${Math.min(textarea.scrollHeight, CHANNEL_PERSONALITY_TEXTAREA_MAX_HEIGHT_PX)}px`;
  }, [draft, isLoading]);

  useEffect(() => {
    let isActive = true;
    const load = async (): Promise<void> => {
      if (!organizationId || !source || !normalizedChannelId) {
        setError('Channel identity is unavailable for this section.');
        return;
      }
      setIsLoading(true);
      setError(null);
      const params = new URLSearchParams({
        source: source.toLowerCase(),
        channel_id: normalizedChannelId,
      });
      const { data, error: requestError } = await apiRequest<ChannelMemoryResponse | null>(`/memories/${organizationId}/channel?${params.toString()}`);
      if (!isActive) return;
      if (requestError) {
        setError(requestError);
      } else {
        const nextValue = data?.content ?? '';
        setDraft(nextValue);
        lastSavedRef.current = nextValue;
      }
      setIsDirty(false);
      setIsLoading(false);
    };
    void load();
    return () => {
      isActive = false;
      if (saveTimeoutRef.current) {
        window.clearTimeout(saveTimeoutRef.current);
      }
    };
  }, [organizationId, source, normalizedChannelId]);

  useEffect(() => {
    if (!isDirty || isLoading || !organizationId || !source || !normalizedChannelId) {
      return;
    }
    if (saveTimeoutRef.current) {
      window.clearTimeout(saveTimeoutRef.current);
    }
    saveTimeoutRef.current = window.setTimeout(() => {
      const persist = async (): Promise<void> => {
        if (draft.length > CHANNEL_PERSONALITY_MAX_LENGTH) return;
        const normalizedSource = source.toLowerCase();
        const trimmed = draft.trim();
        if (trimmed === lastSavedRef.current.trim()) {
          setIsDirty(false);
          return;
        }
        setIsSaving(true);
        setError(null);
        const params = new URLSearchParams({
          source: normalizedSource,
          channel_id: normalizedChannelId,
        });
        const endpoint = `/memories/${organizationId}/channel?${params.toString()}`;
        const result = trimmed
          ? await apiRequest<ChannelMemoryResponse>(endpoint, {
            method: 'PUT',
            body: JSON.stringify({ content: trimmed }),
          })
          : await apiRequest<{ status: string; memory_id: string }>(endpoint, { method: 'DELETE' });
        if (result.error) {
          setError(result.error);
          setIsDirty(false);
        } else {
          lastSavedRef.current = trimmed;
          setIsDirty(false);
        }
        setIsSaving(false);
      };
      void persist();
    }, 700);
    return () => {
      if (saveTimeoutRef.current) {
        window.clearTimeout(saveTimeoutRef.current);
      }
    };
  }, [draft, isDirty, isLoading, organizationId, source, normalizedChannelId]);

  return (
    <>
      <div className="fixed inset-0 bg-black/50 z-40" onClick={onClose} />
      <div className="fixed right-0 top-0 bottom-0 w-full max-w-md bg-surface-900 border-l border-surface-800 z-50 flex flex-col shadow-2xl">
        <header className="flex items-center justify-between px-6 py-4 border-b border-surface-800">
          <h2 className="font-semibold text-surface-100 truncate">{channelName}</h2>
          <button
            onClick={onClose}
            className="p-2 text-surface-400 hover:text-surface-200 hover:bg-surface-800 rounded-lg transition-colors"
            aria-label="Close channel personality panel"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </header>

        <div className="p-6 space-y-3">
          <div className="text-xs uppercase tracking-wide text-primary-300">Channel personality</div>
          <p className="text-xs text-surface-400">Applied on replies in this channel. Maximum {CHANNEL_PERSONALITY_MAX_LENGTH} characters.</p>
          {isLoading ? (
            <p className="text-sm text-surface-400">Loading channel personality...</p>
          ) : (
            <>
              <textarea
                ref={textareaRef}
                className="w-full rounded-lg bg-surface-800 border border-surface-700 px-3 py-2 text-sm text-surface-100 overflow-y-auto"
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value);
                  setIsDirty(true);
                }}
                style={{
                  minHeight: `${CHANNEL_PERSONALITY_TEXTAREA_BASE_HEIGHT_PX}px`,
                  maxHeight: `${CHANNEL_PERSONALITY_TEXTAREA_MAX_HEIGHT_PX}px`,
                }}
                placeholder="e.g. Keep answers concise, action-oriented, and include channel-specific context."
                maxLength={CHANNEL_PERSONALITY_MAX_LENGTH}
              />
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-surface-500">
                  {draft.length}/{CHANNEL_PERSONALITY_MAX_LENGTH}
                </span>
                {isSaving && <span className="text-xs text-surface-500">Saving...</span>}
              </div>
            </>
          )}
          {error && <p className="text-xs text-red-400">{error}</p>}
        </div>
      </div>
    </>
  );
}
