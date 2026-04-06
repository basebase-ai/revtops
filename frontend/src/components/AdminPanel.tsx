/**
 * Admin Panel for global admins.
 * 
 * Features:
 * - Waitlist management (invite users)
 * - Future: User management, org management, data source debugging
 */

import { useEffect, useState, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import ReactMarkdown from 'react-markdown';
import { API_BASE, apiRequest, getAuthenticatedRequestHeaders } from '../lib/api';
import { useDeleteOrganization } from '../hooks';
import { useAppStore, useAuthStore, useChatStore, type UserProfile, type OrganizationInfo } from '../store';

// ─── Dashboard types ─────────────────────────────────────────────────────────

interface CreditUsageSeries {
  org_id: string;
  org_name: string;
  values: number[];
}

interface CreditUsageResponse {
  days: string[];
  series: CreditUsageSeries[];
}

interface TopConversation {
  id: string;
  title: string;
  summary: string | null;
  message_count: number;
  source: string;
  scope: string | null;
  updated_at: string | null;
  participant_names: string[];
}

interface TopOrgConversations {
  org_id: string;
  org_name: string;
  total_credits_used: number;
  conversations: TopConversation[];
}

interface TopConversationsResponse {
  organizations: TopOrgConversations[];
}

function formatRelativeTime(iso: string): string {
  const diffMs: number = Date.now() - new Date(iso).getTime();
  const mins: number = Math.floor(diffMs / 60_000);
  const hrs: number = Math.floor(diffMs / 3_600_000);
  const days: number = Math.floor(diffMs / 86_400_000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  if (hrs < 24) return `${hrs}h ago`;
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

function useThemeColors(): { fontColor: string; gridColor: string } {
  const [colors, setColors] = useState<{ fontColor: string; gridColor: string }>({
    fontColor: '#71717a',
    gridColor: 'rgba(0,0,0,0.08)',
  });

  useEffect(() => {
    const update = (): void => {
      const isDark: boolean = document.documentElement.classList.contains('dark');
      setColors({
        fontColor: getComputedStyle(document.documentElement).getPropertyValue('--surface-400').trim() || '#71717a',
        gridColor: isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.08)',
      });
    };
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    return () => observer.disconnect();
  }, []);

  return colors;
}

function CreditUsageChart({ PlotComponent, data }: {
  PlotComponent: typeof import('react-plotly.js').default;
  data: CreditUsageResponse;
}): JSX.Element {
  const { fontColor, gridColor } = useThemeColors();

  return (
    <div className="bg-surface-900 border border-surface-800 rounded-xl p-4">
      <PlotComponent
        data={data.series.map((s) => ({
          x: data.days,
          y: s.values,
          name: s.org_name,
          type: 'scatter' as const,
          mode: 'lines' as const,
          fill: 'tonexty' as const,
          stackgroup: 'one',
          line: { width: 1.5 },
          hovertemplate: `${s.org_name}: %{y} credits<br>%{x}<extra></extra>`,
        }))}
        layout={{
          autosize: true,
          height: 360,
          margin: { l: 50, r: 20, t: 10, b: 40 },
          paper_bgcolor: 'transparent',
          plot_bgcolor: 'transparent',
          font: { color: fontColor, size: 12 },
          xaxis: {
            gridcolor: gridColor,
            tickformat: '%b %d',
          },
          yaxis: {
            gridcolor: gridColor,
            title: { text: 'Credits used' },
          },
          legend: {
            orientation: 'h' as const,
            y: -0.2,
            font: { size: 11 },
          },
          hovermode: 'x unified' as const,
        }}
        config={{ displayModeBar: false, responsive: true }}
        useResizeHandler
        style={{ width: '100%' }}
      />
    </div>
  );
}

interface WaitlistEntry {
  id: string;
  email: string;
  name: string | null;
  status: string;
  waitlist_data: {
    title?: string;
    company_name?: string;
    num_employees?: string;
    apps_of_interest?: string[];
    core_needs?: string[];
  } | null;
  waitlisted_at: string | null;
  invited_at: string | null;
  created_at: string | null;
}

interface AdminUser {
  id: string;
  email: string;
  first_name: string | null;
  last_name: string | null;
  status: string;
  last_login: string | null;
  created_at: string | null;
  organization_id: string | null;
  organization_name: string | null;
  organizations: string[];
  is_guest: boolean;
}

interface AdminOrganization {
  id: string;
  name: string;
  email_domain: string | null;
  user_count: number;
  credits_balance: number;
  credits_included: number;
  created_at: string | null;
  last_sync_at: string | null;
}

interface GrantFreeCreditsApiResponse {
  success: boolean;
  organization_id: string;
  organization_name: string;
  credits_balance: number;
  credits_included: number;
  subscription_tier: string | null;
  subscription_status: string | null;
  current_period_end: string | null;
}

interface AdminIntegration {
  id: string;
  organization_id: string;
  organization_name: string;
  provider: string;
  is_active: boolean;
  last_sync_at: string | null;
  last_error: string | null;
  sync_stats: Record<string, number> | null;
  created_at: string | null;
}

interface AdminRunningJob {
  id: string;
  type: 'chat' | 'workflow' | 'connector_sync';
  status: string;
  organization_id: string | null;
  organization_name: string | null;
  started_at: string | null;
  title: string;
  description: string;
  metadata: Record<string, unknown> | null;
}

function ThreeDotsIcon(): JSX.Element {
  return (
    <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24" aria-hidden>
      <path d="M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z" />
    </svg>
  );
}

function AdminMobileCard({
  children,
  className = '',
}: {
  children: React.ReactNode;
  className?: string;
}): JSX.Element {
  return (
    <div className={`rounded-xl border border-surface-800 bg-surface-900 p-4 ${className}`.trim()}>
      {children}
    </div>
  );
}

function AdminMobileField({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}): JSX.Element {
  return (
    <div className="space-y-1">
      <div className="text-xs font-medium uppercase tracking-wide text-surface-500">{label}</div>
      <div className="text-sm text-surface-200">{value}</div>
    </div>
  );
}

function OrgRowActions({
  org,
  orgMenuOpenId,
  setOrgMenuOpenId,
  menuRef,
  onInvite,
  onAddCredits,
  onDeleteOrg,
}: {
  org: AdminOrganization;
  orgMenuOpenId: string | null;
  setOrgMenuOpenId: (id: string | null) => void;
  menuRef: React.RefObject<HTMLDivElement | null>;
  onInvite: () => void;
  onAddCredits: () => void;
  onDeleteOrg: (orgId: string, orgName: string) => void;
}): JSX.Element {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [menuRect, setMenuRect] = useState<{ top: number; right: number } | null>(null);
  const isOpen = orgMenuOpenId === org.id;

  useEffect(() => {
    if (isOpen && buttonRef.current) {
      const rect = buttonRef.current.getBoundingClientRect();
      setMenuRect({ top: rect.top, right: rect.right });
    } else {
      setMenuRect(null);
    }
  }, [isOpen]);

  const menuContent =
    isOpen &&
    menuRect &&
    createPortal(
      <div
        ref={menuRef as React.RefObject<HTMLDivElement>}
        onMouseDown={(e: React.MouseEvent) => {
          // Keep document-level mousedown listeners from treating in-menu clicks as "outside".
          e.stopPropagation();
        }}
        className="fixed z-[9999] py-1 min-w-[120px] bg-surface-800 border border-surface-700 rounded-lg shadow-xl"
        style={{
          bottom: window.innerHeight - menuRect.top + 4,
          right: window.innerWidth - menuRect.right,
        }}
      >
        <button
          type="button"
          onClick={() => {
            setOrgMenuOpenId(null);
            onInvite();
          }}
          className="w-full px-3 py-2 text-left text-sm text-surface-200 hover:bg-surface-700"
        >
          Invite
        </button>
        <button
          type="button"
          onClick={() => {
            setOrgMenuOpenId(null);
            onAddCredits();
          }}
          className="w-full px-3 py-2 text-left text-sm text-surface-200 hover:bg-surface-700"
        >
          Add credits
        </button>
        <button
          type="button"
          onClick={() => {
            setOrgMenuOpenId(null);
            onDeleteOrg(org.id, org.name);
          }}
          className="w-full px-3 py-2 text-left text-sm text-red-400 hover:bg-surface-700"
        >
          Delete
        </button>
      </div>,
      document.body
    );

  return (
    <div className="relative flex justify-end">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOrgMenuOpenId(isOpen ? null : org.id)}
        className="p-1.5 rounded-lg text-surface-400 hover:text-surface-200 hover:bg-surface-700 transition-colors"
        aria-label="Team options"
      >
        <ThreeDotsIcon />
      </button>
      {menuContent}
    </div>
  );
}

function UserRowActions({
  u,
  currentUserId,
  userMenuOpenId,
  setUserMenuOpenId,
  menuRef,
  onMasquerade,
  onDeleteUser,
  masquerading,
}: {
  u: AdminUser;
  currentUserId: string | undefined;
  userMenuOpenId: string | null;
  setUserMenuOpenId: (id: string | null) => void;
  menuRef: React.RefObject<HTMLDivElement | null>;
  onMasquerade: (userId: string) => void;
  onDeleteUser: (userId: string, userName: string) => void;
  masquerading: string | null;
}): JSX.Element {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [menuRect, setMenuRect] = useState<{ top: number; right: number } | null>(null);
  const isOpen = userMenuOpenId === u.id;
  const canMasquerade = u.id !== currentUserId && u.status === 'active' && !u.is_guest;

  useEffect(() => {
    if (isOpen && buttonRef.current) {
      const rect = buttonRef.current.getBoundingClientRect();
      setMenuRect({ top: rect.top, right: rect.right });
    } else {
      setMenuRect(null);
    }
  }, [isOpen]);

  const menuContent =
    isOpen &&
    menuRect &&
    createPortal(
      <div
        ref={menuRef as React.RefObject<HTMLDivElement>}
        onMouseDown={(e: React.MouseEvent) => {
          e.stopPropagation();
        }}
        className="fixed z-[9999] py-1 min-w-[140px] bg-surface-800 border border-surface-700 rounded-lg shadow-xl"
        style={{
          bottom: window.innerHeight - menuRect.top + 4,
          right: window.innerWidth - menuRect.right,
        }}
      >
        {canMasquerade && (
          <button
            type="button"
            onClick={() => {
              setUserMenuOpenId(null);
              onMasquerade(u.id);
            }}
            disabled={masquerading === u.id}
            className="w-full px-3 py-2 text-left text-sm text-amber-400 hover:bg-surface-700 disabled:opacity-50"
          >
            {masquerading === u.id ? 'Loading...' : 'Masquerade'}
          </button>
        )}
        <button
          type="button"
          onClick={() => {
            setUserMenuOpenId(null);
            onDeleteUser(u.id, u.email);
          }}
          className="w-full px-3 py-2 text-left text-sm text-red-400 hover:bg-surface-700"
        >
          Delete
        </button>
      </div>,
      document.body
    );

  return (
    <div className="relative flex justify-end">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setUserMenuOpenId(isOpen ? null : u.id)}
        className="p-1.5 rounded-lg text-surface-400 hover:text-surface-200 hover:bg-surface-700 transition-colors"
        aria-label="User options"
      >
        <ThreeDotsIcon />
      </button>
      {menuContent}
    </div>
  );
}

export function AdminPanel(): JSX.Element {
  const user = useAppStore((state) => state.user);
  const startMasquerade = useAppStore((state) => state.startMasquerade);
  const setCurrentView = useAppStore((state) => state.setCurrentView);
  const fetchUserOrganizations = useAppStore((state) => state.fetchUserOrganizations);
  const switchActiveOrganization = useAppStore((state) => state.switchActiveOrganization);
  const deleteOrganizationMutation = useDeleteOrganization();
  const activeTab = useAppStore((state) => state.adminPanelTab);
  const [masquerading, setMasquerading] = useState<string | null>(null);
  const [entries, setEntries] = useState<WaitlistEntry[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'waitlist' | 'invited'>('all');
  const [inviting, setInviting] = useState<string | null>(null);
  const [resendingInviteId, setResendingInviteId] = useState<string | null>(null);

  // Users tab state
  const [adminUsers, setAdminUsers] = useState<AdminUser[]>([]);
  const [usersLoading, setUsersLoading] = useState<boolean>(true);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [userSearch, setUserSearch] = useState<string>('');
  const [showGuestUsers, setShowGuestUsers] = useState<boolean>(false);

  // Organizations tab state
  const [adminOrgs, setAdminOrgs] = useState<AdminOrganization[]>([]);
  const [orgsLoading, setOrgsLoading] = useState<boolean>(true);
  const [orgsError, setOrgsError] = useState<string | null>(null);
  const [orgSearch, setOrgSearch] = useState<string>('');

  // Create team modal state
  const [showCreateOrgModal, setShowCreateOrgModal] = useState<boolean>(false);
  const [createOrgStep, setCreateOrgStep] = useState<1 | 2>(1);
  const [createOrgName, setCreateOrgName] = useState<string>('');
  const [createOrgDomain, setCreateOrgDomain] = useState<string>('');
  const [createOrgLogoUrl, setCreateOrgLogoUrl] = useState<string>('');
  const [createdOrgId, setCreatedOrgId] = useState<string | null>(null);
  const [createOrgInvitees, setCreateOrgInvitees] = useState<Array<{ email: string; name: string }>>([{ email: '', name: '' }]);
  const [createOrgSubmitting, setCreateOrgSubmitting] = useState<boolean>(false);
  const [createOrgError, setCreateOrgError] = useState<string | null>(null);
  const [inviteModalOrg, setInviteModalOrg] = useState<{ id: string; name: string } | null>(null);
  const [grantCreditsOrg, setGrantCreditsOrg] = useState<AdminOrganization | null>(null);
  const [grantCreditsAmount, setGrantCreditsAmount] = useState<number>(2000);
  const [grantCreditsMonths, setGrantCreditsMonths] = useState<number>(12);
  const [grantCreditsSubmitting, setGrantCreditsSubmitting] = useState<boolean>(false);
  const [grantCreditsError, setGrantCreditsError] = useState<string | null>(null);
  const [orgMenuOpenId, setOrgMenuOpenId] = useState<string | null>(null);
  const [userMenuOpenId, setUserMenuOpenId] = useState<string | null>(null);
  const orgMenuRef = useRef<HTMLDivElement>(null);
  const userMenuRef = useRef<HTMLDivElement>(null);

  // Sources tab state
  const [adminIntegrations, setAdminIntegrations] = useState<AdminIntegration[]>([]);
  const [integrationsLoading, setIntegrationsLoading] = useState<boolean>(true);
  const [integrationsError, setIntegrationsError] = useState<string | null>(null);
  const [sourceSearch, setSourceSearch] = useState<string>('');

  // Global sync state
  const [syncing, setSyncing] = useState<boolean>(false);
  const [syncResult, setSyncResult] = useState<{ status: string; taskId: string; count: number } | null>(null);
  const [runningDependencyChecks, setRunningDependencyChecks] = useState<boolean>(false);
  const [dependencyCheckTaskId, setDependencyCheckTaskId] = useState<string | null>(null);
  const [firingIncident, setFiringIncident] = useState<boolean>(false);
  const [incidentResult, setIncidentResult] = useState<string | null>(null);

  // Jobs tab state
  const [runningJobs, setRunningJobs] = useState<AdminRunningJob[]>([]);
  const [jobsLoading, setJobsLoading] = useState<boolean>(true);
  const [jobsError, setJobsError] = useState<string | null>(null);
  const [cancellingJobId, setCancellingJobId] = useState<string | null>(null);

  // Dashboard tab state
  const [creditUsage, setCreditUsage] = useState<CreditUsageResponse | null>(null);
  const [creditUsageLoading, setCreditUsageLoading] = useState<boolean>(true);
  const [creditUsageError, setCreditUsageError] = useState<string | null>(null);
  const [topConversations, setTopConversations] = useState<TopConversationsResponse | null>(null);
  const [topConversationsLoading, setTopConversationsLoading] = useState<boolean>(true);
  const [topConversationsError, setTopConversationsError] = useState<string | null>(null);
  const [PlotComponent, setPlotComponent] = useState<typeof import('react-plotly.js').default | null>(null);

  useEffect(() => {
    import('react-plotly.js')
      .then((mod) => setPlotComponent(() => mod.default))
      .catch(() => console.error('Failed to load chart library'));
  }, []);

  const fetchCreditUsage = useCallback(async (): Promise<void> => {
    setCreditUsageLoading(true);
    setCreditUsageError(null);
    try {
      const { data, error: reqErr } = await apiRequest<CreditUsageResponse>('/admin-dashboard/credit-usage');
      if (reqErr || !data) { setCreditUsageError(reqErr ?? 'Failed to fetch'); return; }
      setCreditUsage(data);
    } catch { setCreditUsageError('Request failed'); }
    finally { setCreditUsageLoading(false); }
  }, []);

  const fetchTopConversations = useCallback(async (): Promise<void> => {
    setTopConversationsLoading(true);
    setTopConversationsError(null);
    try {
      const { data, error: reqErr } = await apiRequest<TopConversationsResponse>('/admin-dashboard/top-conversations');
      if (reqErr || !data) { setTopConversationsError(reqErr ?? 'Failed to fetch'); return; }
      setTopConversations(data);
    } catch { setTopConversationsError('Request failed'); }
    finally { setTopConversationsLoading(false); }
  }, []);

  const fetchWaitlist = useCallback(async (): Promise<void> => {
    if (!user) return;
    
    setLoading(true);
    setError(null);

    try {
      const { data, error: requestError } = await apiRequest<{ entries: WaitlistEntry[]; total: number }>(
        `/waitlist/admin/list?status=${encodeURIComponent(filter)}`
      );

      if (requestError || !data) {
        setError(requestError ?? 'Failed to fetch waitlist');
        setEntries([]);
        return;
      }

      setEntries(data.entries);
    } catch (err) {
      setError('Failed to connect to server');
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }, [filter, user]);

  const fetchUsers = useCallback(async (): Promise<void> => {
    if (!user) return;
    
    setUsersLoading(true);
    setUsersError(null);

    try {
      const { data, error: requestError } = await apiRequest<{ users: AdminUser[]; total: number }>(
        '/waitlist/admin/users'
      );

      if (requestError || !data) {
        setUsersError(requestError ?? 'Failed to fetch users');
        setAdminUsers([]);
        return;
      }

      setAdminUsers(data.users);
    } catch (err) {
      setUsersError('Failed to connect to server');
      setAdminUsers([]);
    } finally {
      setUsersLoading(false);
    }
  }, [user]);

  const fetchOrganizations = useCallback(async (): Promise<void> => {
    if (!user) return;
    
    setOrgsLoading(true);
    setOrgsError(null);

    try {
      const { data, error: requestError } = await apiRequest<{ organizations: AdminOrganization[]; total: number }>(
        '/waitlist/admin/organizations'
      );

      if (requestError || !data) {
        setOrgsError(requestError ?? 'Failed to fetch teams');
        setAdminOrgs([]);
        return;
      }

      setAdminOrgs(data.organizations);
    } catch (err) {
      setOrgsError('Failed to connect to server');
      setAdminOrgs([]);
    } finally {
      setOrgsLoading(false);
    }
  }, [user]);

  const fetchIntegrations = useCallback(async (): Promise<void> => {
    if (!user) return;
    
    setIntegrationsLoading(true);
    setIntegrationsError(null);

    try {
      const headers = await getAuthenticatedRequestHeaders();
      const response = await fetch(`${API_BASE}/sync/admin/integrations`, {
        headers,
      });

      if (!response.ok) {
        if (response.status === 403) {
          setIntegrationsError('Access denied. You need global_admin role.');
        } else {
          setIntegrationsError('Failed to fetch integrations');
        }
        setAdminIntegrations([]);
        return;
      }

      const data = await response.json() as { integrations: AdminIntegration[]; total: number };
      setAdminIntegrations(data.integrations);
    } catch (err) {
      setIntegrationsError('Failed to connect to server');
      setAdminIntegrations([]);
    } finally {
      setIntegrationsLoading(false);
    }
  }, [user]);

  const fetchRunningJobs = useCallback(async (): Promise<void> => {
    if (!user) return;

    setJobsLoading(true);
    setJobsError(null);

    try {
      const headers = await getAuthenticatedRequestHeaders();
      const response = await fetch(`${API_BASE}/sync/admin/jobs`, { headers });

      if (!response.ok) {
        if (response.status === 403) {
          setJobsError('Access denied. You need global_admin role.');
        } else {
          setJobsError('Failed to fetch running jobs');
        }
        setRunningJobs([]);
        return;
      }

      const data = await response.json() as { jobs: AdminRunningJob[]; total: number };
      setRunningJobs(data.jobs);
    } catch {
      setJobsError('Failed to connect to server');
      setRunningJobs([]);
    } finally {
      setJobsLoading(false);
    }
  }, [user]);

  useEffect(() => {
    if (activeTab === 'dashboard') {
      void fetchCreditUsage();
      void fetchTopConversations();
    } else if (activeTab === 'waitlist') {
      void fetchWaitlist();
    } else if (activeTab === 'users') {
      void fetchUsers();
    } else if (activeTab === 'organizations') {
      void fetchOrganizations();
    } else if (activeTab === 'sources') {
      void fetchIntegrations();
    } else if (activeTab === 'jobs') {
      void fetchRunningJobs();
    }
  }, [activeTab, fetchCreditUsage, fetchTopConversations, fetchWaitlist, fetchUsers, fetchOrganizations, fetchIntegrations, fetchRunningJobs]);

  const handleCancelJob = async (job: AdminRunningJob): Promise<void> => {
    if (!user) return;

    setCancellingJobId(job.id);
    try {
      const authHeaders = await getAuthenticatedRequestHeaders();
      const response = await fetch(`${API_BASE}/sync/admin/jobs/${job.id}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders },
        body: JSON.stringify({ job_type: job.type }),
      });

      if (!response.ok) {
        const data = await response.json() as { detail?: string };
        throw new Error(data.detail ?? 'Failed to cancel job');
      }

      await fetchRunningJobs();
    } catch (err) {
      console.error('Failed to cancel job:', err);
      alert('Failed to cancel job: ' + (err instanceof Error ? err.message : 'Unknown error'));
    } finally {
      setCancellingJobId(null);
    }
  };

  const handleInvite = async (targetUserId: string): Promise<void> => {
    if (!user) return;

    setInviting(targetUserId);

    try {
      const { error: requestError } = await apiRequest<{ success: boolean; message: string }>(
        `/waitlist/admin/${targetUserId}/invite`,
        { method: 'POST' }
      );

      if (requestError) {
        throw new Error(requestError);
      }

      // Refresh the list
      await fetchWaitlist();
    } catch (err) {
      console.error('Failed to invite:', err);
    } finally {
      setInviting(null);
    }
  };

  const handleResendInvite = async (targetUserId: string): Promise<void> => {
    if (!user) return;

    setResendingInviteId(targetUserId);

    try {
      const { data, error: requestError } = await apiRequest<{ success: boolean; message: string }>(
        `/waitlist/admin/${targetUserId}/resend-invite`,
        { method: 'POST' }
      );

      if (requestError) {
        alert(`Failed to resend invite: ${requestError}`);
        return;
      }

      await fetchWaitlist();
      alert(data?.message ?? 'Invitation re-sent.');
    } catch (err) {
      console.error('Failed to resend invite:', err);
      alert('Failed to resend invite: ' + (err instanceof Error ? err.message : 'Unknown error'));
    } finally {
      setResendingInviteId(null);
    }
  };

  const handleCreateOrgSubmit = async (): Promise<void> => {
    const name: string = createOrgName.trim();
    const domain: string = createOrgDomain.trim().toLowerCase();
    if (!name || !domain || domain.includes('@')) {
      setCreateOrgError('Name and a valid email domain (e.g. acme.com) are required.');
      return;
    }
    setCreateOrgSubmitting(true);
    setCreateOrgError(null);
    try {
      const { data, error: reqError } = await apiRequest<AdminOrganization>(
        '/waitlist/admin/organizations',
        { method: 'POST', body: JSON.stringify({ name, email_domain: domain, logo_url: createOrgLogoUrl.trim() || undefined }) }
      );
      if (reqError || !data) {
        setCreateOrgError(reqError ?? 'Failed to create team');
        return;
      }
      setCreatedOrgId(data.id);
      setCreateOrgStep(2);
      void fetchOrganizations();
    } finally {
      setCreateOrgSubmitting(false);
    }
  };

  const handleCreateOrgInviteSubmit = async (): Promise<void> => {
    const orgId: string | null = createdOrgId ?? inviteModalOrg?.id ?? null;
    if (!user || !orgId) return;
    const rows: Array<{ email: string; name: string }> = createOrgInvitees.filter((r) => r.email.trim() !== '');
    if (rows.length === 0) {
      setCreateOrgError('Add at least one invitee with an email.');
      return;
    }
    setCreateOrgSubmitting(true);
    setCreateOrgError(null);
    const errors: string[] = [];
    for (const row of rows) {
      const email: string = row.email.trim().toLowerCase();
      const { error: reqError } = await apiRequest<unknown>(
        `/auth/organizations/${orgId}/invitations?user_id=${user.id}`,
        { method: 'POST', body: JSON.stringify({ email, role: 'member', name: row.name.trim() || undefined }) }
      );
      if (reqError) errors.push(`${email}: ${reqError}`);
    }
    setCreateOrgSubmitting(false);
    if (errors.length > 0) {
      setCreateOrgError(errors.join('; '));
      return;
    }
    setShowCreateOrgModal(false);
    setInviteModalOrg(null);
    void fetchOrganizations();
  };

  const handleGrantCreditsSubmit = async (): Promise<void> => {
    if (!grantCreditsOrg) return;
    const credits: number = Math.floor(Number(grantCreditsAmount));
    const months: number = Math.floor(Number(grantCreditsMonths));
    if (!Number.isFinite(credits) || credits < 1) {
      setGrantCreditsError('Credits must be at least 1.');
      return;
    }
    if (!Number.isFinite(months) || months < 1) {
      setGrantCreditsError('Months must be at least 1.');
      return;
    }
    setGrantCreditsSubmitting(true);
    setGrantCreditsError(null);
    try {
      const { data, error: reqError } = await apiRequest<GrantFreeCreditsApiResponse>(
        `/waitlist/admin/organizations/${encodeURIComponent(grantCreditsOrg.id)}/grant-credits`,
        { method: 'POST', body: JSON.stringify({ credits, months }) }
      );
      if (reqError || !data?.success) {
        setGrantCreditsError(reqError ?? 'Failed to add credits');
        return;
      }
      setGrantCreditsOrg(null);
      void fetchOrganizations();
    } finally {
      setGrantCreditsSubmitting(false);
    }
  };

  const handleMasquerade = async (targetUserId: string): Promise<void> => {
    const actor = useAuthStore.getState().user;
    if (!actor) {
      alert('You must be signed in to masquerade. Try refreshing the page.');
      return;
    }

    setMasquerading(targetUserId);

    try {
      type MasqueradeApiResponse = {
        id: string;
        email: string;
        name: string | null;
        avatar_url: string | null;
        roles: string[];
        organization: {
          id: string;
          name: string;
          logo_url: string | null;
          handle?: string | null;
        } | null;
      };

      const { data, error } = await apiRequest<MasqueradeApiResponse>(
        `/auth/masquerade/${encodeURIComponent(targetUserId)}`,
        { method: 'GET' },
      );

      if (error || !data) {
        throw new Error(error ?? 'Failed to masquerade');
      }

      if (!data.organization) {
        throw new Error(
          'This user has no team membership (active, onboarding, or invited). Add them to an organization before masquerading.',
        );
      }

      const targetUser: UserProfile = {
        id: data.id,
        email: data.email,
        name: data.name,
        avatarUrl: data.avatar_url,
        phoneNumber: null,
        jobTitle: null,
        roles: data.roles,
        smsConsent: false,
        whatsappConsent: false,
        phoneNumberVerified: false,
      };

      const targetOrg: OrganizationInfo = {
        id: data.organization.id,
        name: data.organization.name,
        logoUrl: data.organization.logo_url,
        handle: data.organization.handle ?? null,
      };

      startMasquerade(targetUser, targetOrg);
      await fetchUserOrganizations();
      setCurrentView('home');
    } catch (err) {
      console.error('Failed to masquerade:', err);
      alert('Failed to masquerade: ' + (err instanceof Error ? err.message : 'Unknown error'));
    } finally {
      setMasquerading(null);
    }
  };

  const handleDeleteOrg = async (orgId: string, orgName: string): Promise<void> => {
    if (!window.confirm(`Delete ${orgName}? This permanently removes the team and its data.`)) return;
    try {
      await deleteOrganizationMutation.mutateAsync({ orgId });
      await fetchOrganizations();
      await fetchUserOrganizations();
      const remainingOrgs: { id: string }[] = useAppStore.getState().organizations;
      const nextOrg: { id: string } | undefined = remainingOrgs[0];
      if (nextOrg) {
        await switchActiveOrganization(nextOrg.id);
      } else {
        await import('../lib/supabase').then((m) => m.supabase.auth.signOut());
        useAppStore.getState().logout();
        localStorage.clear();
        sessionStorage.clear();
        window.location.href = '/auth';
      }
      alert('Team deleted.');
    } catch (err) {
      alert(`Failed to delete team: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  };

  const handleDeleteUser = async (userId: string, userEmail: string): Promise<void> => {
    if (!window.confirm(`Delete ${userEmail}? This permanently removes the user and all their data.`)) return;
    try {
      const { error } = await apiRequest<{ status: string }>(
        `/waitlist/admin/users/${encodeURIComponent(userId)}`,
        { method: 'DELETE' }
      );
      if (error) throw new Error(error);
      await fetchUsers();
      alert('User deleted.');
    } catch (err) {
      alert(`Failed to delete user: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  };

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent): void => {
      const target = e.target as Node;
      if (orgMenuOpenId !== null && orgMenuRef.current && !orgMenuRef.current.contains(target)) {
        setOrgMenuOpenId(null);
      }
      if (userMenuOpenId !== null && userMenuRef.current && !userMenuRef.current.contains(target)) {
        setUserMenuOpenId(null);
      }
    };
    if (orgMenuOpenId !== null || userMenuOpenId !== null) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [orgMenuOpenId, userMenuOpenId]);

  const handleGlobalSync = async (): Promise<void> => {
    if (!user) return;
    
    setSyncing(true);
    setSyncResult(null);

    try {
      const headers = await getAuthenticatedRequestHeaders();
      const response = await fetch(`${API_BASE}/sync/admin/all`, {
        method: 'POST',
        headers,
      });

      if (!response.ok) {
        const data = await response.json() as { detail?: string };
        throw new Error(data.detail ?? 'Failed to trigger sync');
      }

      const data = await response.json() as { status: string; task_id: string; integration_count: number };
      setSyncResult({
        status: data.status,
        taskId: data.task_id,
        count: data.integration_count,
      });
    } catch (err) {
      console.error('Failed to trigger global sync:', err);
      alert('Failed to trigger sync: ' + (err instanceof Error ? err.message : 'Unknown error'));
    } finally {
      setSyncing(false);
    }
  };

  const handleRunDependencyChecks = async (): Promise<void> => {
    if (!user) return;

    setRunningDependencyChecks(true);
    setDependencyCheckTaskId(null);

    try {
      const headers = await getAuthenticatedRequestHeaders();
      const response = await fetch(`${API_BASE}/sync/admin/dependency-checks`, {
        method: 'POST',
        headers,
      });

      if (!response.ok) {
        const data = await response.json() as { detail?: string };
        throw new Error(data.detail ?? 'Failed to run dependency checks');
      }

      const data = await response.json() as { status: string; task_id: string };
      setDependencyCheckTaskId(data.task_id);
    } catch (err) {
      console.error('Failed to run dependency checks:', err);
      alert('Failed to run dependency checks: ' + (err instanceof Error ? err.message : 'Unknown error'));
    } finally {
      setRunningDependencyChecks(false);
    }
  };

  const handleFireIncident = async (): Promise<void> => {
    if (!user) return;

    setFiringIncident(true);
    setIncidentResult(null);

    try {
      const headers = await getAuthenticatedRequestHeaders();
      const response = await fetch(`${API_BASE}/sync/admin/fire-incident`, {
        method: 'POST',
        headers,
      });

      if (!response.ok) {
        const data = await response.json() as { detail?: string };
        throw new Error(data.detail ?? 'Failed to fire PagerDuty incident');
      }

      const data = await response.json() as { status: string; title: string };
      setIncidentResult(data.title);
    } catch (err) {
      console.error('Failed to fire PagerDuty incident:', err);
      alert('Failed to fire incident: ' + (err instanceof Error ? err.message : 'Unknown error'));
    } finally {
      setFiringIncident(false);
    }
  };

  const formatDate = (dateStr: string | null): string => {
    if (!dateStr) return '—';
    // Backend returns UTC times without 'Z' suffix, so append it if missing
    const utcDateStr = dateStr.endsWith('Z') || dateStr.includes('+') || dateStr.includes('-', 10)
      ? dateStr
      : `${dateStr}Z`;
    return new Date(utcDateStr).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  };

  const getStatusBadge = (status: string): JSX.Element => {
    const styles: Record<string, string> = {
      waitlist: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
      invited: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
      active: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
    };
    return (
      <span className={`px-2 py-0.5 rounded-full text-xs border ${styles[status] ?? styles.waitlist}`}>
        {status}
      </span>
    );
  };

  // Filter users by search term (in-memory)
  const filteredUsers = adminUsers.filter((u) => {
    if (!userSearch.trim()) return true;
    const searchLower = userSearch.toLowerCase();
    const firstName = (u.first_name ?? '').toLowerCase();
    const lastName = (u.last_name ?? '').toLowerCase();
    const orgName = (u.organization_name ?? '').toLowerCase();
    const orgNames = (u.organizations ?? []).map((name) => name.toLowerCase()).join(' ');
    const email = u.email.toLowerCase();
    return (
      firstName.includes(searchLower) ||
      lastName.includes(searchLower) ||
      orgName.includes(searchLower) ||
      orgNames.includes(searchLower) ||
      email.includes(searchLower)
    );
  });

  const filteredGuestUsers = filteredUsers.filter((u) => u.is_guest);
  const filteredNonGuestUsers = filteredUsers.filter((u) => !u.is_guest);

  // Filter organizations by search term (in-memory)
  const filteredOrgs = adminOrgs.filter((o) => {
    if (!orgSearch.trim()) return true;
    const searchLower = orgSearch.toLowerCase();
    const name = o.name.toLowerCase();
    const domain = (o.email_domain ?? '').toLowerCase();
    return name.includes(searchLower) || domain.includes(searchLower);
  });

  // Filter integrations by search term (in-memory)
  const filteredIntegrations = adminIntegrations.filter((i) => {
    if (!sourceSearch.trim()) return true;
    const searchLower = sourceSearch.toLowerCase();
    const orgName = i.organization_name.toLowerCase();
    const provider = i.provider.toLowerCase();
    return orgName.includes(searchLower) || provider.includes(searchLower);
  });

  const jobTypeLabel: Record<AdminRunningJob['type'], string> = {
    chat: 'Chat',
    workflow: 'Workflow',
    connector_sync: 'Connector Sync',
  };

  // Provider display names
  const providerNames: Record<string, string> = {
    salesforce: 'Salesforce',
    hubspot: 'HubSpot',
    slack: 'Slack',
    fireflies: 'Fireflies',
    google_calendar: 'Google Calendar',
    gmail: 'Gmail',
    microsoft_calendar: 'Microsoft Calendar',
    microsoft_mail: 'Microsoft Mail',
    zoom: 'Zoom',
  };

  return (
    <div className="flex-1 overflow-y-auto">
      {/* Header */}
      <header className="sticky top-0 z-10 border-b border-surface-800 bg-surface-950 px-4 py-5 md:px-8 md:py-6">
        <div className="flex items-start gap-3 sm:items-center">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-amber-500 to-orange-600 flex items-center justify-center">
            <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
          </div>
          <div>
            <h1 className="text-2xl font-bold text-surface-50">Global Admin</h1>
            <p className="text-surface-400 mt-0.5">Manage Basebase globally</p>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-4 py-4 md:px-8 md:py-6">
        {/* Dashboard Tab Content */}
        {activeTab === 'dashboard' && (
          <div className="space-y-8">
            {/* Credit Usage Chart */}
            <section>
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-surface-100">Credit Usage — Past 7 Days</h2>
                <button
                  onClick={() => { void fetchCreditUsage(); void fetchTopConversations(); }}
                  className="px-3 py-1.5 rounded-lg border border-surface-700 text-xs font-medium text-surface-300 hover:bg-surface-800 transition-colors"
                >
                  Refresh
                </button>
              </div>

              {creditUsageLoading && (
                <div className="text-center py-16 text-surface-400">Loading credit usage data...</div>
              )}
              {creditUsageError && (
                <div className="text-center py-16 text-red-400">{creditUsageError}</div>
              )}
              {!creditUsageLoading && !creditUsageError && creditUsage && PlotComponent && (
                <CreditUsageChart PlotComponent={PlotComponent} data={creditUsage} />
              )}
              {!creditUsageLoading && !creditUsageError && creditUsage && creditUsage.series.length === 0 && (
                <div className="text-center py-16 text-surface-400">No credit usage in the past 7 days.</div>
              )}
            </section>

            {/* Top Conversations */}
            <section>
              <h2 className="text-lg font-semibold text-surface-100 mb-4">Top Customers — Active Conversations</h2>

              {topConversationsLoading && (
                <div className="text-center py-12 text-surface-400">Loading conversations...</div>
              )}
              {topConversationsError && (
                <div className="text-center py-12 text-red-400">{topConversationsError}</div>
              )}
              {!topConversationsLoading && !topConversationsError && topConversations && topConversations.organizations.length === 0 && (
                <div className="text-center py-12 text-surface-400">No conversation data available.</div>
              )}
              {!topConversationsLoading && !topConversationsError && topConversations && topConversations.organizations.length > 0 && (
                <div className="space-y-6">
                  {topConversations.organizations.map((org) => (
                    <div key={org.org_id} className="rounded-xl border border-surface-800 bg-surface-900 overflow-hidden">
                      <div className="px-5 py-3 border-b border-surface-800 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-surface-100">{org.org_name}</span>
                        </div>
                        <span className="text-xs text-surface-400 bg-surface-800 rounded-full px-2.5 py-0.5">
                          {org.total_credits_used.toLocaleString()} credits used
                        </span>
                      </div>
                      {org.conversations.length === 0 ? (
                        <div className="px-5 py-6 text-sm text-surface-500">No recent conversations</div>
                      ) : (
                        <div className="divide-y divide-surface-800">
                          {org.conversations.map((conv) => (
                            <button
                              key={conv.id}
                              type="button"
                              onClick={() => {
                                useChatStore.getState().setCurrentChatId(conv.id);
                                useAppStore.getState().setCurrentView('chat');
                              }}
                              className="w-full text-left px-5 py-3 hover:bg-surface-800/40 transition-colors cursor-pointer"
                            >
                              <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0 flex-1">
                                  <div className="text-sm font-medium text-surface-200 truncate">{conv.title}</div>
                                  {conv.participant_names.length > 0 && (
                                    <div className="text-xs text-surface-500 mt-0.5">{conv.participant_names.join(', ')}</div>
                                  )}
                                  {conv.summary && (
                                    <div className="mt-1 text-xs text-surface-300 line-clamp-3 prose dark:prose-invert prose-xs max-w-none [&_p]:m-0 [&_strong]:text-surface-100 [&_em]:text-surface-200 [&_a]:text-primary-600 dark:[&_a]:text-primary-400 [&_li]:text-surface-300">
                                      <ReactMarkdown>{conv.summary}</ReactMarkdown>
                                    </div>
                                  )}
                                </div>
                                <div className="flex items-center gap-3 shrink-0">
                                  {conv.updated_at && (
                                    <span className="text-[11px] text-surface-500">{formatRelativeTime(conv.updated_at)}</span>
                                  )}
                                  <span className="text-xs text-surface-500 capitalize">{conv.source}</span>
                                  <span className="text-xs text-surface-400 bg-surface-800 rounded px-1.5 py-0.5">
                                    {conv.message_count} msgs
                                  </span>
                                </div>
                              </div>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </section>
          </div>
        )}

        {/* Waitlist Tab Content */}
        {activeTab === 'waitlist' && (
          <div className="space-y-6">
            {/* Filters & Actions */}
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-wrap gap-2">
                {(['waitlist', 'invited', 'all'] as const).map((f) => (
                  <button
                    key={f}
                    onClick={() => setFilter(f)}
                    className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                      filter === f
                        ? 'bg-primary-500/20 text-primary-400 border border-primary-500/30'
                        : 'bg-surface-800 text-surface-400 border border-surface-700 hover:border-surface-600'
                    }`}
                  >
                    {f === 'all' ? 'All' : f.charAt(0).toUpperCase() + f.slice(1)}
                  </button>
                ))}
              </div>
              <button
                onClick={() => void fetchWaitlist()}
                disabled={loading}
                className="flex w-full items-center justify-center gap-2 rounded-lg border border-surface-700 bg-surface-800 px-4 py-2 text-surface-300 transition-colors hover:bg-surface-700 disabled:opacity-50 sm:w-auto"
              >
                <svg className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                Refresh
              </button>
            </div>

            {/* Error */}
            {error && (
              <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400">
                {error}
              </div>
            )}

            {/* Loading */}
            {loading && (
              <div className="text-center py-12 text-surface-400">
                <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
                Loading waitlist...
              </div>
            )}

            {/* Empty state */}
            {!loading && !error && entries.length === 0 && (
              <div className="text-center py-12">
                <div className="w-16 h-16 rounded-full bg-surface-800 flex items-center justify-center mx-auto mb-4">
                  <svg className="w-8 h-8 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
                  </svg>
                </div>
                <p className="text-surface-400">No {filter === 'all' ? '' : filter} users found</p>
              </div>
            )}

            {/* Table */}
            {!loading && !error && entries.length > 0 && (
              <>
              <div className="hidden overflow-x-auto rounded-xl border border-surface-800 bg-surface-900 md:block">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-surface-800 text-left">
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">User</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Company</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Apps</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Status</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Signed Up</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-surface-800">
                    {entries.map((entry) => (
                      <tr key={entry.id} className="hover:bg-surface-800/50">
                        <td className="px-4 py-3">
                          <div>
                            <div className="font-medium text-surface-100">{entry.name ?? 'Unknown'}</div>
                            <div className="text-sm text-surface-400">{entry.email}</div>
                            {entry.waitlist_data?.title && (
                              <div className="text-xs text-surface-500">{entry.waitlist_data.title}</div>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <div className="text-surface-200">{entry.waitlist_data?.company_name ?? '—'}</div>
                          {entry.waitlist_data?.num_employees && (
                            <div className="text-xs text-surface-500">{entry.waitlist_data.num_employees} employees</div>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex flex-wrap gap-1">
                            {entry.waitlist_data?.apps_of_interest?.slice(0, 3).map((app) => (
                              <span key={app} className="px-1.5 py-0.5 rounded bg-surface-700 text-xs text-surface-300">
                                {app}
                              </span>
                            ))}
                            {(entry.waitlist_data?.apps_of_interest?.length ?? 0) > 3 && (
                              <span className="px-1.5 py-0.5 text-xs text-surface-500">
                                +{(entry.waitlist_data?.apps_of_interest?.length ?? 0) - 3}
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-3">{getStatusBadge(entry.status)}</td>
                        <td className="px-4 py-3 text-sm text-surface-400">
                          {formatDate(entry.waitlisted_at)}
                        </td>
                        <td className="px-4 py-3">
                          {entry.status === 'waitlist' ? (
                            <button
                              onClick={() => void handleInvite(entry.id)}
                              disabled={inviting === entry.id}
                              className="px-3 py-1.5 rounded-lg bg-primary-500 hover:bg-primary-600 text-white text-sm font-medium transition-colors disabled:opacity-50"
                            >
                              {inviting === entry.id ? 'Inviting...' : 'Invite'}
                            </button>
                          ) : entry.status === 'invited' ? (
                            <div className="flex flex-col gap-1">
                              <span className="text-sm text-surface-500">Invited {formatDate(entry.invited_at)}</span>
                              <button
                                onClick={() => void handleResendInvite(entry.id)}
                                disabled={resendingInviteId === entry.id}
                                className="px-3 py-1 rounded-lg bg-surface-700 hover:bg-surface-600 text-surface-300 text-sm font-medium transition-colors disabled:opacity-50 self-start"
                              >
                                {resendingInviteId === entry.id ? 'Sending...' : 'Resend'}
                              </button>
                            </div>
                          ) : (
                            <span className="text-sm text-emerald-400">Active</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="space-y-3 md:hidden">
                {entries.map((entry) => (
                  <AdminMobileCard key={entry.id}>
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="font-medium text-surface-100">{entry.name ?? 'Unknown'}</div>
                        <div className="break-all text-sm text-surface-400">{entry.email}</div>
                        {entry.waitlist_data?.title && (
                          <div className="mt-1 text-xs text-surface-500">{entry.waitlist_data.title}</div>
                        )}
                      </div>
                      <div className="shrink-0">{getStatusBadge(entry.status)}</div>
                    </div>
                    <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                      <AdminMobileField
                        label="Company"
                        value={
                          <>
                            <div>{entry.waitlist_data?.company_name ?? '—'}</div>
                            {entry.waitlist_data?.num_employees && (
                              <div className="text-xs text-surface-500">{entry.waitlist_data.num_employees} employees</div>
                            )}
                          </>
                        }
                      />
                      <AdminMobileField label="Signed up" value={formatDate(entry.waitlisted_at)} />
                      <AdminMobileField
                        label="Apps"
                        value={
                          <div className="flex flex-wrap gap-1">
                            {entry.waitlist_data?.apps_of_interest?.length ? (
                              <>
                                {entry.waitlist_data.apps_of_interest.slice(0, 3).map((app) => (
                                  <span key={app} className="rounded bg-surface-700 px-1.5 py-0.5 text-xs text-surface-300">
                                    {app}
                                  </span>
                                ))}
                                {entry.waitlist_data.apps_of_interest.length > 3 && (
                                  <span className="px-1.5 py-0.5 text-xs text-surface-500">
                                    +{entry.waitlist_data.apps_of_interest.length - 3}
                                  </span>
                                )}
                              </>
                            ) : '—'}
                          </div>
                        }
                      />
                      <AdminMobileField
                        label="Action"
                        value={
                          entry.status === 'waitlist' ? (
                            <button
                              onClick={() => void handleInvite(entry.id)}
                              disabled={inviting === entry.id}
                              className="rounded-lg bg-primary-500 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-primary-600 disabled:opacity-50"
                            >
                              {inviting === entry.id ? 'Inviting...' : 'Invite'}
                            </button>
                          ) : entry.status === 'invited' ? (
                            <div className="flex flex-col items-start gap-2">
                              <span className="text-sm text-surface-500">Invited {formatDate(entry.invited_at)}</span>
                              <button
                                onClick={() => void handleResendInvite(entry.id)}
                                disabled={resendingInviteId === entry.id}
                                className="self-start rounded-lg bg-surface-700 px-3 py-1 text-sm font-medium text-surface-300 transition-colors hover:bg-surface-600 disabled:opacity-50"
                              >
                                {resendingInviteId === entry.id ? 'Sending...' : 'Resend'}
                              </button>
                            </div>
                          ) : (
                            <span className="text-sm text-emerald-400">Active</span>
                          )
                        }
                      />
                    </div>
                  </AdminMobileCard>
                ))}
              </div>
              </>
            )}

            {/* Stats */}
            {!loading && !error && (
              <div className="text-sm text-surface-500 text-center">
                Showing {entries.length} {filter === 'all' ? 'total' : filter} users
              </div>
            )}
          </div>
        )}

        {/* Users Tab Content */}
        {activeTab === 'users' && (
          <div className="space-y-6">
            {/* Search & Actions */}
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="relative w-full flex-1 sm:max-w-md">
                <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-surface-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                <input
                  type="text"
                  placeholder="Search by name, email, or team..."
                  value={userSearch}
                  onChange={(e) => setUserSearch(e.target.value)}
                  className="w-full pl-10 pr-4 py-2 rounded-lg bg-surface-800 border border-surface-700 text-surface-100 placeholder-surface-500 focus:outline-none focus:border-primary-500"
                />
              </div>
              <button
                onClick={() => void fetchUsers()}
                disabled={usersLoading}
                className="flex w-full items-center justify-center gap-2 rounded-lg border border-surface-700 bg-surface-800 px-4 py-2 text-surface-300 transition-colors hover:bg-surface-700 disabled:opacity-50 sm:w-auto"
              >
                <svg className={`w-4 h-4 ${usersLoading ? 'animate-spin' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                Refresh
              </button>
            </div>

            {/* Error */}
            {usersError && (
              <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400">
                {usersError}
              </div>
            )}

            {/* Loading */}
            {usersLoading && (
              <div className="text-center py-12 text-surface-400">
                <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
                Loading users...
              </div>
            )}

            {/* Empty state */}
            {!usersLoading && !usersError && filteredUsers.length === 0 && (
              <div className="text-center py-12">
                <div className="w-16 h-16 rounded-full bg-surface-800 flex items-center justify-center mx-auto mb-4">
                  <svg className="w-8 h-8 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
                  </svg>
                </div>
                <p className="text-surface-400">
                  {userSearch ? 'No users match your search' : 'No users found'}
                </p>
              </div>
            )}

            {/* Table */}
            {!usersLoading && !usersError && filteredUsers.length > 0 && (
              <>
              <div className="hidden overflow-x-auto rounded-xl border border-surface-800 bg-surface-900 md:block">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-surface-800 text-left">
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">User</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Team</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Status</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Last Login</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Joined</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400 w-12"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-surface-800">
                    {filteredNonGuestUsers.map((u) => (
                      <tr key={u.id} className="hover:bg-surface-800/50">
                        <td className="px-4 py-3">
                          <div>
                            <div className="font-medium text-surface-100">
                              {u.first_name || u.last_name
                                ? `${u.first_name ?? ''} ${u.last_name ?? ''}`.trim()
                                : 'Unknown'}
                            </div>
                            <div className="text-sm text-surface-400">{u.email}</div>
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          {(u.organizations ?? []).length > 0 ? (
                            <div className="flex flex-wrap gap-1.5">
                              {(u.organizations ?? []).map((organizationName, orgIdx) => (
                                <span
                                  key={`${u.id}-org-${orgIdx}-${organizationName}`}
                                  className="px-2 py-0.5 rounded-full text-xs border border-primary-500/30 bg-primary-500/10 text-primary-300"
                                >
                                  {organizationName}
                                </span>
                              ))}
                            </div>
                          ) : (
                            <div className="text-surface-200">{u.organization_name ?? '—'}</div>
                          )}
                        </td>
                        <td className="px-4 py-3">{getStatusBadge(u.status)}</td>
                        <td className="px-4 py-3 text-sm text-surface-400">
                          {u.last_login ? formatDate(u.last_login) : 'Never'}
                        </td>
                        <td className="px-4 py-3 text-sm text-surface-400">
                          {formatDate(u.created_at)}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <UserRowActions
                            u={u}
                            currentUserId={user?.id}
                            userMenuOpenId={userMenuOpenId}
                            setUserMenuOpenId={setUserMenuOpenId}
                            menuRef={userMenuRef}
                            onMasquerade={handleMasquerade}
                            onDeleteUser={handleDeleteUser}
                            masquerading={masquerading}
                          />
                        </td>
                      </tr>
                    ))}
                    {filteredGuestUsers.length > 0 && (
                      <tr className="bg-surface-800/30">
                        <td colSpan={6} className="px-4 py-2.5 text-sm">
                          <button
                            onClick={() => setShowGuestUsers((prev) => !prev)}
                            className="flex items-center gap-2 text-surface-300 hover:text-surface-100 transition-colors"
                          >
                            <span className="text-xs text-surface-500">{showGuestUsers ? '▼' : '▶'}</span>
                            <span>
                              {filteredGuestUsers.length} guest {filteredGuestUsers.length === 1 ? 'user' : 'users'}
                            </span>
                          </button>
                        </td>
                      </tr>
                    )}
                    {showGuestUsers && filteredGuestUsers.map((u) => (
                      <tr key={u.id} className="hover:bg-surface-800/40">
                        <td className="px-4 py-2.5">
                          <div>
                            <div className="font-medium text-surface-200">Guest user</div>
                            <div className="text-xs text-surface-500">{u.email}</div>
                          </div>
                        </td>
                        <td className="px-4 py-2.5 text-xs text-surface-400">{u.organization_name ?? '—'}</td>
                        <td className="px-4 py-2.5">{getStatusBadge(u.status)}</td>
                        <td className="px-4 py-2.5 text-xs text-surface-500">{u.last_login ? formatDate(u.last_login) : 'Never'}</td>
                        <td className="px-4 py-2.5 text-xs text-surface-500">{formatDate(u.created_at)}</td>
                        <td className="px-4 py-2.5 text-xs text-surface-500">—</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="space-y-3 md:hidden">
                {filteredNonGuestUsers.map((u) => (
                  <AdminMobileCard key={u.id}>
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="font-medium text-surface-100">
                          {u.first_name || u.last_name
                            ? `${u.first_name ?? ''} ${u.last_name ?? ''}`.trim()
                            : 'Unknown'}
                        </div>
                        <div className="break-all text-sm text-surface-400">{u.email}</div>
                      </div>
                      <div className="shrink-0">
                        <UserRowActions
                          u={u}
                          currentUserId={user?.id}
                          userMenuOpenId={userMenuOpenId}
                          setUserMenuOpenId={setUserMenuOpenId}
                          menuRef={userMenuRef}
                          onMasquerade={handleMasquerade}
                          onDeleteUser={handleDeleteUser}
                          masquerading={masquerading}
                        />
                      </div>
                    </div>
                    <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                      <AdminMobileField
                        label="Teams"
                        value={
                          (u.organizations ?? []).length > 0 ? (
                            <div className="flex flex-wrap gap-1.5">
                              {(u.organizations ?? []).map((organizationName) => (
                                <span
                                  key={`${u.id}-${organizationName}`}
                                  className="rounded-full border border-primary-500/30 bg-primary-500/10 px-2 py-0.5 text-xs text-primary-300"
                                >
                                  {organizationName}
                                </span>
                              ))}
                            </div>
                          ) : (
                            u.organization_name ?? '—'
                          )
                        }
                      />
                      <AdminMobileField label="Status" value={getStatusBadge(u.status)} />
                      <AdminMobileField label="Last login" value={u.last_login ? formatDate(u.last_login) : 'Never'} />
                      <AdminMobileField label="Joined" value={formatDate(u.created_at)} />
                    </div>
                  </AdminMobileCard>
                ))}
                {filteredGuestUsers.length > 0 && (
                  <AdminMobileCard className="bg-surface-900/70">
                    <button
                      onClick={() => setShowGuestUsers((prev) => !prev)}
                      className="flex w-full items-center gap-2 text-left text-surface-300 transition-colors hover:text-surface-100"
                    >
                      <span className="text-xs text-surface-500">{showGuestUsers ? '▼' : '▶'}</span>
                      <span>{filteredGuestUsers.length} guest {filteredGuestUsers.length === 1 ? 'user' : 'users'}</span>
                    </button>
                  </AdminMobileCard>
                )}
                {showGuestUsers && filteredGuestUsers.map((u) => (
                  <AdminMobileCard key={u.id} className="bg-surface-900/70">
                    <div className="font-medium text-surface-200">Guest user</div>
                    <div className="mt-1 break-all text-xs text-surface-500">{u.email}</div>
                    <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                      <AdminMobileField label="Team" value={u.organization_name ?? '—'} />
                      <AdminMobileField label="Status" value={getStatusBadge(u.status)} />
                      <AdminMobileField label="Last login" value={u.last_login ? formatDate(u.last_login) : 'Never'} />
                      <AdminMobileField label="Joined" value={formatDate(u.created_at)} />
                    </div>
                  </AdminMobileCard>
                ))}
              </div>
              </>
            )}

            {/* Stats */}
            {!usersLoading && !usersError && (
              <div className="text-sm text-surface-500 text-center">
                Showing {filteredUsers.length} of {adminUsers.length} users
                {userSearch && ` matching "${userSearch}"`}
              </div>
            )}
          </div>
        )}

        {/* Organizations Tab Content */}
        {activeTab === 'organizations' && (
          <div className="space-y-6">
            {/* Search & Actions */}
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="relative w-full flex-1 sm:max-w-md">
                <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-surface-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                <input
                  type="text"
                  placeholder="Search by name or domain..."
                  value={orgSearch}
                  onChange={(e) => setOrgSearch(e.target.value)}
                  className="w-full pl-10 pr-4 py-2 rounded-lg bg-surface-800 border border-surface-700 text-surface-100 placeholder-surface-500 focus:outline-none focus:border-primary-500"
                />
              </div>
              <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row">
                <button
                  onClick={() => {
                    setInviteModalOrg(null);
                    setShowCreateOrgModal(true);
                    setCreateOrgStep(1);
                    setCreateOrgName('');
                    setCreateOrgDomain('');
                    setCreateOrgLogoUrl('');
                    setCreatedOrgId(null);
                    setCreateOrgInvitees([{ email: '', name: '' }]);
                    setCreateOrgError(null);
                  }}
                  className="flex w-full items-center justify-center gap-2 rounded-lg border border-primary-500/30 bg-primary-500/20 px-4 py-2 text-primary-400 transition-colors hover:bg-primary-500/30 sm:w-auto"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                  </svg>
                  Create team
                </button>
                <button
                  onClick={() => void fetchOrganizations()}
                  disabled={orgsLoading}
                  className="flex w-full items-center justify-center gap-2 rounded-lg border border-surface-700 bg-surface-800 px-4 py-2 text-surface-300 transition-colors hover:bg-surface-700 disabled:opacity-50 sm:w-auto"
                >
                  <svg className={`w-4 h-4 ${orgsLoading ? 'animate-spin' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  Refresh
                </button>
              </div>
            </div>

            {/* Error */}
            {orgsError && (
              <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400">
                {orgsError}
              </div>
            )}

            {/* Loading */}
            {orgsLoading && (
              <div className="text-center py-12 text-surface-400">
                <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
                Loading teams...
              </div>
            )}

            {/* Empty state */}
            {!orgsLoading && !orgsError && filteredOrgs.length === 0 && (
              <div className="text-center py-12">
                <div className="w-16 h-16 rounded-full bg-surface-800 flex items-center justify-center mx-auto mb-4">
                  <svg className="w-8 h-8 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                  </svg>
                </div>
                <p className="text-surface-400">
                  {orgSearch ? 'No teams match your search' : 'No teams found'}
                </p>
              </div>
            )}

            {/* Table */}
            {!orgsLoading && !orgsError && filteredOrgs.length > 0 && (
              <>
              <div className="hidden overflow-x-auto rounded-xl border border-surface-800 bg-surface-900 md:block">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-surface-800 text-left">
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Team</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Domain</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Users</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Credits</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Last Sync</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Created</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400 w-12"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-surface-800">
                    {filteredOrgs.map((o) => (
                      <tr key={o.id} className="hover:bg-surface-800/50">
                        <td className="px-4 py-3">
                          <div className="font-medium text-surface-100">{o.name}</div>
                        </td>
                        <td className="px-4 py-3">
                          <div className="text-surface-300">{o.email_domain ?? '—'}</div>
                        </td>
                        <td className="px-4 py-3">
                          <span className="px-2 py-0.5 rounded-full text-xs bg-surface-700 text-surface-300">
                            {o.user_count} {o.user_count === 1 ? 'user' : 'users'}
                          </span>
                        </td>
                        <td className="px-4 py-3 font-mono text-sm tabular-nums text-surface-300">
                          {o.credits_balance.toLocaleString()} / {o.credits_included.toLocaleString()}
                        </td>
                        <td className="px-4 py-3 text-sm text-surface-400">
                          {o.last_sync_at ? formatDate(o.last_sync_at) : 'Never'}
                        </td>
                        <td className="px-4 py-3 text-sm text-surface-400">
                          {formatDate(o.created_at)}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <OrgRowActions
                            org={o}
                            orgMenuOpenId={orgMenuOpenId}
                            setOrgMenuOpenId={setOrgMenuOpenId}
                            menuRef={orgMenuRef}
                            onInvite={() => {
                              setInviteModalOrg({ id: o.id, name: o.name });
                              setCreateOrgStep(2);
                              setCreateOrgInvitees([{ email: '', name: '' }]);
                              setCreateOrgError(null);
                              setShowCreateOrgModal(true);
                            }}
                            onAddCredits={() => {
                              setGrantCreditsOrg(o);
                              setGrantCreditsAmount(2000);
                              setGrantCreditsMonths(12);
                              setGrantCreditsError(null);
                            }}
                            onDeleteOrg={handleDeleteOrg}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="space-y-3 md:hidden">
                {filteredOrgs.map((o) => (
                  <AdminMobileCard key={o.id}>
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="font-medium text-surface-100">{o.name}</div>
                        <div className="mt-1 break-all text-sm text-surface-400">{o.email_domain ?? '—'}</div>
                      </div>
                      <OrgRowActions
                        org={o}
                        orgMenuOpenId={orgMenuOpenId}
                        setOrgMenuOpenId={setOrgMenuOpenId}
                        menuRef={orgMenuRef}
                        onInvite={() => {
                          setInviteModalOrg({ id: o.id, name: o.name });
                          setCreateOrgStep(2);
                          setCreateOrgInvitees([{ email: '', name: '' }]);
                          setCreateOrgError(null);
                          setShowCreateOrgModal(true);
                        }}
                        onAddCredits={() => {
                          setGrantCreditsOrg(o);
                          setGrantCreditsAmount(2000);
                          setGrantCreditsMonths(12);
                          setGrantCreditsError(null);
                        }}
                        onDeleteOrg={handleDeleteOrg}
                      />
                    </div>
                    <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                      <AdminMobileField
                        label="Users"
                        value={
                          <span className="rounded-full bg-surface-700 px-2 py-0.5 text-xs text-surface-300">
                            {o.user_count} {o.user_count === 1 ? 'user' : 'users'}
                          </span>
                        }
                      />
                      <AdminMobileField
                        label="Credits (rem. / total)"
                        value={
                          <span className="font-mono text-sm tabular-nums text-surface-200">
                            {o.credits_balance.toLocaleString()} / {o.credits_included.toLocaleString()}
                          </span>
                        }
                      />
                      <AdminMobileField label="Last sync" value={o.last_sync_at ? formatDate(o.last_sync_at) : 'Never'} />
                      <AdminMobileField label="Created" value={formatDate(o.created_at)} />
                    </div>
                  </AdminMobileCard>
                ))}
              </div>
              </>
            )}

            {/* Stats */}
            {!orgsLoading && !orgsError && (
              <div className="text-sm text-surface-500 text-center">
                Showing {filteredOrgs.length} of {adminOrgs.length} teams
                {orgSearch && ` matching "${orgSearch}"`}
              </div>
            )}

            {/* Create team modal */}
            {showCreateOrgModal && (
              <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => { if (!createOrgSubmitting) { setShowCreateOrgModal(false); setInviteModalOrg(null); } }}>
                <div className="bg-surface-900 border border-surface-700 rounded-xl shadow-xl max-w-lg w-full max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
                  <div className="p-6">
                    <h3 className="text-lg font-semibold text-surface-100 mb-4">
                      {createOrgStep === 1
                        ? 'Create team'
                        : inviteModalOrg
                          ? `Invite users to ${inviteModalOrg.name}`
                          : 'Invite users'}
                    </h3>
                    {createOrgError && (
                      <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
                        {createOrgError}
                      </div>
                    )}
                    {createOrgStep === 1 ? (
                      <>
                        <div className="space-y-4">
                          <div>
                            <label className="block text-sm font-medium text-surface-400 mb-1">Team name</label>
                            <input
                              type="text"
                              value={createOrgName}
                              onChange={(e) => setCreateOrgName(e.target.value)}
                              placeholder="Acme Inc"
                              className="w-full px-3 py-2 rounded-lg bg-surface-800 border border-surface-700 text-surface-100 placeholder-surface-500 focus:outline-none focus:border-primary-500"
                            />
                          </div>
                          <div>
                            <label className="block text-sm font-medium text-surface-400 mb-1">Email domain</label>
                            <input
                              type="text"
                              value={createOrgDomain}
                              onChange={(e) => setCreateOrgDomain(e.target.value)}
                              placeholder="acme.com"
                              className="w-full px-3 py-2 rounded-lg bg-surface-800 border border-surface-700 text-surface-100 placeholder-surface-500 focus:outline-none focus:border-primary-500"
                            />
                          </div>
                          <div>
                            <label className="block text-sm font-medium text-surface-400 mb-1">Logo URL (optional)</label>
                            <input
                              type="url"
                              value={createOrgLogoUrl}
                              onChange={(e) => setCreateOrgLogoUrl(e.target.value)}
                              placeholder="https://..."
                              className="w-full px-3 py-2 rounded-lg bg-surface-800 border border-surface-700 text-surface-100 placeholder-surface-500 focus:outline-none focus:border-primary-500"
                            />
                          </div>
                        </div>
                        <div className="mt-6 flex justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => setShowCreateOrgModal(false)}
                            disabled={createOrgSubmitting}
                            className="px-4 py-2 rounded-lg bg-surface-800 text-surface-300 hover:bg-surface-700 disabled:opacity-50"
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleCreateOrgSubmit()}
                            disabled={createOrgSubmitting}
                            className="px-4 py-2 rounded-lg bg-primary-500 text-surface-900 font-medium hover:bg-primary-400 disabled:opacity-50 flex items-center gap-2"
                          >
                            {createOrgSubmitting ? 'Creating...' : 'Create & invite users'}
                          </button>
                        </div>
                      </>
                    ) : (
                      <>
                        <p className="text-sm text-surface-400 mb-4">Add one or more invitees. They will receive an email invitation.</p>
                        <div className="space-y-3 max-h-48 overflow-y-auto">
                          {createOrgInvitees.map((row, idx) => (
                            <div key={idx} className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center">
                              <input
                                type="email"
                                value={row.email}
                                onChange={(e) => {
                                  const next = [...createOrgInvitees];
                                  const rowItem = next[idx];
                                  if (rowItem) next[idx] = { email: e.target.value, name: rowItem.name };
                                  setCreateOrgInvitees(next);
                                }}
                                placeholder="email@example.com"
                                className="flex-1 px-3 py-2 rounded-lg bg-surface-800 border border-surface-700 text-surface-100 placeholder-surface-500 focus:outline-none focus:border-primary-500 text-sm"
                              />
                              <input
                                type="text"
                                value={row.name}
                                onChange={(e) => {
                                  const next = [...createOrgInvitees];
                                  const rowItem = next[idx];
                                  if (rowItem) next[idx] = { email: rowItem.email, name: e.target.value };
                                  setCreateOrgInvitees(next);
                                }}
                                placeholder="Name (optional)"
                                className="w-full rounded-lg border border-surface-700 bg-surface-800 px-3 py-2 text-sm text-surface-100 placeholder-surface-500 focus:border-primary-500 focus:outline-none sm:w-32"
                              />
                              <button
                                type="button"
                                onClick={() => setCreateOrgInvitees((prev) => prev.filter((_, i) => i !== idx))}
                                disabled={createOrgInvitees.length <= 1}
                                className="p-2 rounded-lg text-surface-400 hover:bg-surface-800 hover:text-surface-200 disabled:opacity-40 disabled:cursor-not-allowed"
                                title="Remove row"
                              >
                                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
                              </button>
                            </div>
                          ))}
                        </div>
                        <button
                          type="button"
                          onClick={() => setCreateOrgInvitees((prev) => [...prev, { email: '', name: '' }])}
                          className="mt-2 text-sm text-primary-400 hover:text-primary-300"
                        >
                          + Add another
                        </button>
                        <div className="mt-6 flex justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => setShowCreateOrgModal(false)}
                            disabled={createOrgSubmitting}
                            className="px-4 py-2 rounded-lg bg-surface-800 text-surface-300 hover:bg-surface-700 disabled:opacity-50"
                          >
                            Close
                          </button>
                          <button
                            type="button"
                            onClick={() => { setCreateOrgStep(1); setCreateOrgError(null); }}
                            disabled={createOrgSubmitting}
                            className="px-4 py-2 rounded-lg bg-surface-800 text-surface-300 hover:bg-surface-700 disabled:opacity-50"
                          >
                            Back
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleCreateOrgInviteSubmit()}
                            disabled={createOrgSubmitting}
                            className="px-4 py-2 rounded-lg bg-primary-500 text-surface-900 font-medium hover:bg-primary-400 disabled:opacity-50 flex items-center gap-2"
                          >
                            {createOrgSubmitting ? 'Sending...' : 'Send invitations'}
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                </div>
              </div>
            )}

            {grantCreditsOrg && (
              <div
                className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
                onClick={() => {
                  if (!grantCreditsSubmitting) setGrantCreditsOrg(null);
                }}
              >
                <div
                  className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-xl border border-surface-700 bg-surface-900 shadow-xl"
                  onClick={(e) => e.stopPropagation()}
                >
                  <div className="p-6">
                    <h3 className="mb-2 text-lg font-semibold text-surface-100">
                      Add credits — {grantCreditsOrg.name}
                    </h3>
                    <p className="mb-4 text-sm text-surface-400">
                      Sets partner tier, billing period, credit balance and included credits (same behavior as the grant_free_credits script). Stripe customer and subscription IDs are cleared.
                    </p>
                    {grantCreditsError && (
                      <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 p-3 text-sm text-red-400">
                        {grantCreditsError}
                      </div>
                    )}
                    <div className="space-y-4">
                      <div>
                        <label className="mb-1 block text-sm font-medium text-surface-400">Credits</label>
                        <input
                          type="number"
                          min={1}
                          max={10_000_000}
                          value={grantCreditsAmount}
                          onChange={(e) => {
                            const n: number = parseInt(e.target.value, 10);
                            setGrantCreditsAmount(Number.isNaN(n) ? 0 : n);
                          }}
                          className="w-full rounded-lg border border-surface-700 bg-surface-800 px-3 py-2 text-surface-100 focus:border-primary-500 focus:outline-none"
                        />
                      </div>
                      <div>
                        <label className="mb-1 block text-sm font-medium text-surface-400">Period (months)</label>
                        <input
                          type="number"
                          min={1}
                          max={120}
                          value={grantCreditsMonths}
                          onChange={(e) => {
                            const n: number = parseInt(e.target.value, 10);
                            setGrantCreditsMonths(Number.isNaN(n) ? 0 : n);
                          }}
                          className="w-full rounded-lg border border-surface-700 bg-surface-800 px-3 py-2 text-surface-100 focus:border-primary-500 focus:outline-none"
                        />
                      </div>
                    </div>
                    <div className="mt-6 flex justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => setGrantCreditsOrg(null)}
                        disabled={grantCreditsSubmitting}
                        className="rounded-lg bg-surface-800 px-4 py-2 text-surface-300 hover:bg-surface-700 disabled:opacity-50"
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleGrantCreditsSubmit()}
                        disabled={grantCreditsSubmitting}
                        className="flex items-center gap-2 rounded-lg bg-primary-500 px-4 py-2 font-medium text-surface-900 hover:bg-primary-400 disabled:opacity-50"
                      >
                        {grantCreditsSubmitting ? 'Adding…' : 'Add Credits'}
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Sources Tab Content */}
        {activeTab === 'sources' && (
          <div className="space-y-6">
            {/* Search & Actions */}
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="relative w-full flex-1 sm:max-w-md">
                <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-surface-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                <input
                  type="text"
                  placeholder="Search by team or provider..."
                  value={sourceSearch}
                  onChange={(e) => setSourceSearch(e.target.value)}
                  className="w-full pl-10 pr-4 py-2 rounded-lg bg-surface-800 border border-surface-700 text-surface-100 placeholder-surface-500 focus:outline-none focus:border-primary-500"
                />
              </div>
              <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row">
                <button
                  onClick={() => void handleRunDependencyChecks()}
                  disabled={runningDependencyChecks}
                  className="flex w-full items-center justify-center gap-2 rounded-lg border border-blue-500/30 bg-blue-500/20 px-4 py-2 text-blue-400 transition-colors hover:bg-blue-500/30 disabled:opacity-50 sm:w-auto"
                  title="Run dependency checks immediately"
                >
                  <svg className={`w-4 h-4 ${runningDependencyChecks ? 'animate-spin' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6M7 4h10a2 2 0 012 2v12a2 2 0 01-2 2H7a2 2 0 01-2-2V6a2 2 0 012-2z" />
                  </svg>
                  {runningDependencyChecks ? 'Checking...' : 'Run Checks'}
                </button>
                <button
                  onClick={() => void handleFireIncident()}
                  disabled={firingIncident}
                  className="flex w-full items-center justify-center gap-2 rounded-lg border border-red-500/30 bg-red-500/20 px-4 py-2 text-red-400 transition-colors hover:bg-red-500/30 disabled:opacity-50 sm:w-auto"
                  title="Fire a test PagerDuty incident"
                >
                  <svg className={`w-4 h-4 ${firingIncident ? 'animate-pulse' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01M5.07 19h13.86c1.54 0 2.5-1.67 1.73-3L13.73 4c-.77-1.33-2.69-1.33-3.46 0L3.34 16c-.77 1.33.19 3 1.73 3z" />
                  </svg>
                  {firingIncident ? 'Firing...' : 'Fire Incident'}
                </button>
                <button
                  onClick={() => void handleGlobalSync()}
                  disabled={syncing}
                  className="flex w-full items-center justify-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/20 px-4 py-2 text-emerald-400 transition-colors hover:bg-emerald-500/30 disabled:opacity-50 sm:w-auto"
                  title="Trigger sync for all teams (same as hourly scheduled sync)"
                >
                  <svg className={`w-4 h-4 ${syncing ? 'animate-spin' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  {syncing ? 'Syncing...' : 'Sync All'}
                </button>
                <button
                  onClick={() => void fetchIntegrations()}
                  disabled={integrationsLoading}
                  className="flex w-full items-center justify-center gap-2 rounded-lg border border-surface-700 bg-surface-800 px-4 py-2 text-surface-300 transition-colors hover:bg-surface-700 disabled:opacity-50 sm:w-auto"
                >
                  <svg className={`w-4 h-4 ${integrationsLoading ? 'animate-spin' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  Refresh
                </button>
              </div>
            </div>

            {/* Sync Result Banner */}
            {syncResult && (
              <div className="p-4 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 flex items-center justify-between">
                <div>
                  <span className="font-medium">Sync queued!</span>
                  <span className="ml-2 text-emerald-300/80">
                    {syncResult.count} integrations will be synced.
                  </span>
                  <span className="ml-2 text-xs text-emerald-400/60">
                    Task ID: {syncResult.taskId.slice(0, 8)}...
                  </span>
                </div>
                <button
                  onClick={() => setSyncResult(null)}
                  className="text-emerald-400/60 hover:text-emerald-400"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            )}

            {dependencyCheckTaskId && (
              <div className="p-4 rounded-lg bg-blue-500/10 border border-blue-500/20 text-blue-400 flex items-center justify-between">
                <div>
                  <span className="font-medium">Dependency checks queued.</span>
                  <span className="ml-2 text-xs text-blue-400/70">Task ID: {dependencyCheckTaskId.slice(0, 8)}...</span>
                </div>
                <button
                  onClick={() => setDependencyCheckTaskId(null)}
                  className="text-blue-400/60 hover:text-blue-400"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            )}

            {incidentResult && (
              <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 flex items-center justify-between">
                <div>
                  <span className="font-medium">Incident sent:</span>
                  <span className="ml-2 text-red-300/80">{incidentResult}</span>
                </div>
                <button
                  onClick={() => setIncidentResult(null)}
                  className="text-red-400/60 hover:text-red-400"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            )}

            {/* Error */}
            {integrationsError && (
              <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400">
                {integrationsError}
              </div>
            )}

            {/* Loading */}
            {integrationsLoading && (
              <div className="text-center py-12 text-surface-400">
                <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
                Loading data sources...
              </div>
            )}

            {/* Empty state */}
            {!integrationsLoading && !integrationsError && filteredIntegrations.length === 0 && (
              <div className="text-center py-12">
                <div className="w-16 h-16 rounded-full bg-surface-800 flex items-center justify-center mx-auto mb-4">
                  <svg className="w-8 h-8 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4" />
                  </svg>
                </div>
                <p className="text-surface-400">
                  {sourceSearch ? 'No sources match your search' : 'No data sources connected'}
                </p>
              </div>
            )}

            {/* Table */}
            {!integrationsLoading && !integrationsError && filteredIntegrations.length > 0 && (
              <>
              <div className="hidden overflow-x-auto rounded-xl border border-surface-800 bg-surface-900 md:block">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-surface-800 text-left">
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Team</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Provider</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Status</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Last Sync</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Records</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-surface-800">
                    {filteredIntegrations.map((i) => (
                      <tr key={i.id} className="hover:bg-surface-800/50">
                        <td className="px-4 py-3">
                          <div className="font-medium text-surface-100">{i.organization_name}</div>
                        </td>
                        <td className="px-4 py-3">
                          <div className="text-surface-200">{providerNames[i.provider] ?? i.provider}</div>
                        </td>
                        <td className="px-4 py-3">
                          {i.is_active ? (
                            i.last_error ? (
                              <span className="px-2 py-0.5 rounded-full text-xs bg-red-500/20 text-red-400 border border-red-500/30" title={i.last_error}>
                                Error
                              </span>
                            ) : (
                              <span className="px-2 py-0.5 rounded-full text-xs bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                                Active
                              </span>
                            )
                          ) : (
                            <span className="px-2 py-0.5 rounded-full text-xs bg-surface-700 text-surface-400">
                              Inactive
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-sm text-surface-400">
                          {i.last_sync_at ? formatDate(i.last_sync_at) : 'Never'}
                        </td>
                        <td className="px-4 py-3 text-sm text-surface-400">
                          {i.sync_stats ? (
                            <span title={JSON.stringify(i.sync_stats, null, 2)}>
                              {Object.values(i.sync_stats).reduce((a, b) => a + b, 0).toLocaleString()}
                            </span>
                          ) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="space-y-3 md:hidden">
                {filteredIntegrations.map((i) => (
                  <AdminMobileCard key={i.id}>
                    <div className="font-medium text-surface-100">{i.organization_name}</div>
                    <div className="mt-1 text-sm text-surface-400">{providerNames[i.provider] ?? i.provider}</div>
                    <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                      <AdminMobileField
                        label="Status"
                        value={
                          i.is_active ? (
                            i.last_error ? (
                              <span className="rounded-full border border-red-500/30 bg-red-500/20 px-2 py-0.5 text-xs text-red-400" title={i.last_error}>
                                Error
                              </span>
                            ) : (
                              <span className="rounded-full border border-emerald-500/30 bg-emerald-500/20 px-2 py-0.5 text-xs text-emerald-400">
                                Active
                              </span>
                            )
                          ) : (
                            <span className="rounded-full bg-surface-700 px-2 py-0.5 text-xs text-surface-400">
                              Inactive
                            </span>
                          )
                        }
                      />
                      <AdminMobileField label="Last sync" value={i.last_sync_at ? formatDate(i.last_sync_at) : 'Never'} />
                      <AdminMobileField
                        label="Records"
                        value={i.sync_stats
                          ? Object.values(i.sync_stats).reduce((a, b) => a + b, 0).toLocaleString()
                          : '—'}
                      />
                    </div>
                  </AdminMobileCard>
                ))}
              </div>
              </>
            )}

            {/* Stats */}
            {!integrationsLoading && !integrationsError && (
              <div className="text-sm text-surface-500 text-center">
                Showing {filteredIntegrations.length} of {adminIntegrations.length} data sources
                {sourceSearch && ` matching "${sourceSearch}"`}
              </div>
            )}
          </div>
        )}

        {activeTab === 'jobs' && (
          <div className="space-y-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-sm text-surface-400">Manage currently running chat, workflow, and connector sync jobs.</p>
              <button
                onClick={() => void fetchRunningJobs()}
                className="rounded-lg border border-surface-700 bg-surface-800 px-3 py-1.5 text-sm text-surface-200 hover:bg-surface-700 sm:self-auto self-start"
              >
                Refresh
              </button>
            </div>

            {jobsError && (
              <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400">
                {jobsError}
              </div>
            )}

            {jobsLoading && (
              <div className="text-center py-12 text-surface-400">Loading running jobs...</div>
            )}

            {!jobsLoading && !jobsError && runningJobs.length === 0 && (
              <div className="text-center py-12 text-surface-400">No running jobs found.</div>
            )}

            {!jobsLoading && !jobsError && runningJobs.length > 0 && (
              <>
              <div className="hidden overflow-x-auto rounded-xl border border-surface-800 bg-surface-900 md:block">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-surface-800 text-left">
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Type</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Title</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Team</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Started</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-surface-800">
                    {runningJobs.map((job) => (
                      <tr key={`${job.type}:${job.id}`} className="hover:bg-surface-800/50">
                        <td className="px-4 py-3 text-surface-200">{jobTypeLabel[job.type]}</td>
                        <td className="px-4 py-3">
                          <div className="text-surface-100 font-medium">{job.title}</div>
                          <div className="text-xs text-surface-400">{job.description}</div>
                        </td>
                        <td className="px-4 py-3 text-surface-300">{job.organization_name ?? '—'}</td>
                        <td className="px-4 py-3 text-sm text-surface-400">{formatDate(job.started_at)}</td>
                        <td className="px-4 py-3">
                          <button
                            onClick={() => void handleCancelJob(job)}
                            disabled={cancellingJobId === job.id}
                            className="px-3 py-1.5 text-xs font-medium rounded-lg bg-red-500/15 text-red-300 border border-red-500/30 hover:bg-red-500/25 disabled:opacity-50"
                          >
                            {cancellingJobId === job.id ? 'Cancelling...' : 'Cancel'}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="space-y-3 md:hidden">
                {runningJobs.map((job) => (
                  <AdminMobileCard key={`${job.type}:${job.id}`}>
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="font-medium text-surface-100">{job.title}</div>
                        <div className="mt-1 text-sm text-surface-400">{job.description}</div>
                      </div>
                      <span className="rounded-full bg-surface-700 px-2 py-0.5 text-xs text-surface-300">
                        {jobTypeLabel[job.type]}
                      </span>
                    </div>
                    <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                      <AdminMobileField label="Team" value={job.organization_name ?? '—'} />
                      <AdminMobileField label="Started" value={formatDate(job.started_at)} />
                      <AdminMobileField
                        label="Action"
                        value={
                          <button
                            onClick={() => void handleCancelJob(job)}
                            disabled={cancellingJobId === job.id}
                            className="rounded-lg border border-red-500/30 bg-red-500/15 px-3 py-1.5 text-xs font-medium text-red-300 hover:bg-red-500/25 disabled:opacity-50"
                          >
                            {cancellingJobId === job.id ? 'Cancelling...' : 'Cancel'}
                          </button>
                        }
                      />
                    </div>
                  </AdminMobileCard>
                ))}
              </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
