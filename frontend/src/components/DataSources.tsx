/**
 * Connectors management screen.
 * 
 * Features:
 * - View all connected connectors
 * - View available connectors to connect
 * - Sync status and manual sync trigger
 * - Disconnect integrations
 * 
 * Uses React Query for server state (integrations list).
 */

import { useState, useEffect, useCallback, useMemo } from 'react';
import Nango from '@nangohq/frontend';
import type { IconType } from 'react-icons';
import {
  SiSalesforce,
  SiHubspot,
  SiSlack,
  SiZoom,
  SiGooglecalendar,
  SiGmail,
  SiGoogledrive,
  SiGithub,
  SiLinear,
  SiJira,
  SiAsana,
} from 'react-icons/si';
import { HiOutlineCalendar, HiOutlineMail, HiGlobeAlt, HiUserGroup, HiDeviceMobile, HiMicrophone, HiLightningBolt, HiX, HiCog, HiShare, HiLockClosed, HiDocumentText, HiCube, HiLink, HiChevronDown } from 'react-icons/hi';
// Custom Apollo.io icon - 8-ray starburst matching their brand
const ApolloIcon: IconType = ({ className, ...props }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className={className} {...props}>
    <line x1="12" y1="2" x2="12" y2="22" />
    <line x1="2" y1="12" x2="22" y2="12" />
    <line x1="4.93" y1="4.93" x2="19.07" y2="19.07" />
    <line x1="19.07" y1="4.93" x2="4.93" y2="19.07" />
  </svg>
);
import { API_BASE, apiRequest } from '../lib/api';
import { useAppStore, useIntegrations, useIntegrationsLoading, type Integration, type SyncStats } from '../store';
import { useWebSocket } from '../hooks/useWebSocket';

// Icon map for integration providers
const ICON_MAP: Record<string, IconType> = {
  hubspot: SiHubspot,
  salesforce: SiSalesforce,
  slack: SiSlack,
  zoom: SiZoom,
  'google-calendar': SiGooglecalendar,
  google_calendar: SiGooglecalendar,
  gmail: SiGmail,
  'microsoft-calendar': HiOutlineCalendar,
  microsoft_calendar: HiOutlineCalendar,
  'microsoft-mail': HiOutlineMail,
  microsoft_mail: HiOutlineMail,
  fireflies: HiMicrophone,
  google_drive: SiGoogledrive,
  apollo: ApolloIcon,
  github: SiGithub,
  linear: SiLinear,
  jira: SiJira,
  asana: SiAsana,
  globe: HiGlobeAlt,
  terminal: HiLightningBolt,
  sms: HiDeviceMobile,
  artifacts: HiDocumentText,
  apps: HiCube,
  plug: HiLink,
};

/** Connector metadata from GET /api/connectors */
interface ConnectorMetaFromApi {
  slug: string;
  name: string;
  description: string;
  auth_type: string;
  scope: 'user' | 'organization';
  default_sharing: { share_synced_data: boolean; share_query_access: boolean; share_write_access: boolean };
  connection_flow: 'oauth' | 'builtin' | 'custom_credentials';
  capabilities: string[];
  icon: string;
}

/** Display overrides (icon, color) by slug; fallback used when missing. */
const CONNECTOR_DISPLAY_OVERRIDE: Record<string, { icon?: string; color?: string }> = {
  hubspot: { icon: 'hubspot', color: 'from-orange-500 to-orange-600' },
  salesforce: { icon: 'salesforce', color: 'from-blue-500 to-blue-600' },
  slack: { icon: 'slack', color: 'from-purple-500 to-purple-600' },
  zoom: { icon: 'zoom', color: 'from-blue-400 to-blue-500' },
  google_calendar: { icon: 'google_calendar', color: 'from-green-500 to-green-600' },
  gmail: { icon: 'gmail', color: 'from-red-500 to-red-600' },
  microsoft_calendar: { icon: 'microsoft_calendar', color: 'from-sky-500 to-sky-600' },
  microsoft_mail: { icon: 'microsoft_mail', color: 'from-sky-500 to-sky-600' },
  fireflies: { icon: 'fireflies', color: 'from-violet-500 to-violet-600' },
  granola: { icon: '/connector-icons/granola.png', color: 'from-lime-500 to-green-600' },
  google_drive: { icon: 'google_drive', color: 'from-yellow-500 to-amber-500' },
  apollo: { icon: 'apollo', color: 'from-yellow-400 to-yellow-500' },
  github: { icon: 'github', color: 'from-gray-600 to-gray-700' },
  linear: { icon: 'linear', color: 'from-indigo-500 to-violet-600' },
  jira: { icon: 'jira', color: 'from-blue-500 to-blue-600' },
  asana: { icon: 'asana', color: 'from-fuchsia-500 to-pink-600' },
  web_search: { icon: 'globe', color: 'from-emerald-500 to-teal-600' },
  code_sandbox: { icon: 'terminal', color: 'from-amber-500 to-orange-600' },
  twilio: { icon: 'sms', color: 'from-red-500 to-pink-600' },
  artifacts: { icon: 'artifacts', color: 'from-slate-500 to-slate-600' },
  apps: { icon: 'apps', color: 'from-violet-500 to-purple-600' },
  mcp: { icon: 'plug', color: 'from-cyan-500 to-blue-600' },
  ispot_tv: { icon: 'globe', color: 'from-emerald-500 to-teal-600' },
};

const DEFAULT_ICON = 'globe';
const DEFAULT_COLOR = 'from-gray-500 to-gray-600';

/** Fallback when API fails or provider not in registry. */
interface IntegrationConfigEntry {
  name: string;
  description: string;
  icon: string;
  color: string;
  scope: 'organization' | 'user';
}

const INTEGRATION_CONFIG_FALLBACK: Record<string, IntegrationConfigEntry> = {
  hubspot: { name: 'HubSpot', description: 'CRM data including deals, contacts, and companies', icon: 'hubspot', color: 'from-orange-500 to-orange-600', scope: 'user' },
  salesforce: { name: 'Salesforce', description: 'CRM - Opportunities, Accounts', icon: 'salesforce', color: 'from-blue-500 to-blue-600', scope: 'user' },
  slack: { name: 'Slack', description: 'Team messages and communication history', icon: 'slack', color: 'from-purple-500 to-purple-600', scope: 'organization' },
  zoom: { name: 'Zoom', description: 'Meeting transcripts and cloud recording insights', icon: 'zoom', color: 'from-blue-400 to-blue-500', scope: 'user' },
  google_calendar: { name: 'Google Calendar', description: 'Meetings, events, and scheduling data', icon: 'google_calendar', color: 'from-green-500 to-green-600', scope: 'user' },
  gmail: { name: 'Gmail', description: 'Google email communications', icon: 'gmail', color: 'from-red-500 to-red-600', scope: 'user' },
  microsoft_calendar: { name: 'Microsoft Calendar', description: 'Outlook calendar events and meetings', icon: 'microsoft_calendar', color: 'from-sky-500 to-sky-600', scope: 'user' },
  microsoft_mail: { name: 'Microsoft Mail', description: 'Outlook emails and communications', icon: 'microsoft_mail', color: 'from-sky-500 to-sky-600', scope: 'user' },
  fireflies: { name: 'Fireflies', description: 'Meeting transcriptions and notes', icon: 'fireflies', color: 'from-violet-500 to-violet-600', scope: 'user' },
  granola: { name: 'Granola', description: 'AI meeting notes, transcripts, and action items', icon: '/connector-icons/granola.png', color: 'from-lime-500 to-green-600', scope: 'user' },
  google_drive: { name: 'Google Drive', description: 'Sync files — search and read Docs, Sheets, Slides from Drive', icon: 'google_drive', color: 'from-yellow-500 to-amber-500', scope: 'user' },
  apollo: { name: 'Apollo.io', description: 'Data enrichment - Contact titles, companies, emails', icon: 'apollo', color: 'from-yellow-400 to-yellow-500', scope: 'user' },
  github: { name: 'GitHub', description: 'Track repos, commits, and pull requests by team', icon: 'github', color: 'from-gray-600 to-gray-700', scope: 'user' },
  linear: { name: 'Linear', description: 'Issue tracking - sync and manage teams, projects, and issues', icon: 'linear', color: 'from-indigo-500 to-violet-600', scope: 'user' },
  jira: { name: 'Jira', description: 'Issue tracking - sync projects and issues from Atlassian Jira', icon: 'jira', color: 'from-blue-500 to-blue-600', scope: 'user' },
  asana: { name: 'Asana', description: 'Tasks and projects - sync and manage workspaces, projects, and tasks', icon: 'asana', color: 'from-fuchsia-500 to-pink-600', scope: 'user' },
  web_search: { name: 'Web Search', description: 'Web search and URL fetching — enable for the agent to search the web or fetch pages', icon: 'globe', color: 'from-emerald-500 to-teal-600', scope: 'organization' },
  code_sandbox: { name: 'Code Sandbox', description: 'Run shell commands and scripts in a secure sandbox (Python, Node, bash)', icon: 'terminal', color: 'from-amber-500 to-orange-600', scope: 'organization' },
  twilio: { name: 'Twilio', description: 'Send SMS messages to phone numbers', icon: 'sms', color: 'from-red-500 to-pink-600', scope: 'organization' },
  artifacts: { name: 'Artifact Builder', description: 'Create and update downloadable files (reports, markdown, PDFs, charts)', icon: 'artifacts', color: 'from-slate-500 to-slate-600', scope: 'organization' },
  apps: { name: 'App Builder', description: 'Create and update interactive mini-apps with React + SQL', icon: 'apps', color: 'from-violet-500 to-purple-600', scope: 'organization' },
  mcp: { name: 'MCP Server', description: 'Connect any MCP-compatible server by URL', icon: 'plug', color: 'from-cyan-500 to-blue-600', scope: 'user' },
  ispot_tv: { name: 'iSpot.tv', description: 'TV ad analytics — airings, spend, impressions, and conversions', icon: 'globe', color: 'from-emerald-500 to-teal-600', scope: 'organization' },
};

// Common integrations to show as tiles when org has zero connected (display order)
const COMMON_INTEGRATION_KEYS: ReadonlyArray<string> = [
  'hubspot',
  'salesforce',
  'slack',
  'gmail',
  'google_calendar',
  'zoom',
  'apollo',
];

// Extended integration type with display info
interface DisplayIntegration extends Integration {
  name: string;
  description: string;
  icon: string;
  color: string;
  connected: boolean;
}

/** ISO8601 UTC timestamp for ``since`` query (manual resync from). */
function isoUtcSubtractMs(offsetMs: number): string {
  return new Date(Date.now() - offsetMs).toISOString();
}

const RESYNC_OFFSET_MS = {
  hours24: 24 * 60 * 60 * 1000,
  days7: 7 * 24 * 60 * 60 * 1000,
  days30: 30 * 24 * 60 * 60 * 1000,
} as const;

interface SlackUserMapping {
  id: string;
  external_userid: string | null;
  external_email: string | null;
  source: string;
  match_source: string;
  created_at: string;
}

/**
 * Format sync stats into a human-readable summary string.
 * Shows counts for different object types synced.
 * Always shows stats for CRM providers (even zeros) for trust/debugging.
 */
function formatSyncStats(stats: SyncStats | null, provider: string): string | null {
  if (!stats) return null;

  const parts: string[] = [];

  // GitHub: show repos, commits, PRs
  if (provider === 'github') {
    const repos = stats.repositories ?? 0;
    const commits = stats.commits ?? 0;
    const prs = stats.pull_requests ?? 0;
    if (repos > 0) parts.push(`${repos} repos`);
    if (commits > 0) parts.push(`${commits.toLocaleString()} commits`);
    if (prs > 0) parts.push(`${prs} PRs`);
  } else if (provider === 'linear' || provider === 'jira' || provider === 'asana') {
    // Issue tracker providers: teams, projects, issues
    const teams = stats.teams ?? 0;
    const projects = stats.projects ?? 0;
    const issues = stats.issues ?? 0;
    if (teams > 0) parts.push(`${teams} ${teams === 1 ? 'team' : 'teams'}`);
    if (projects > 0) parts.push(`${projects} ${projects === 1 ? 'project' : 'projects'}`);
    if (issues > 0) parts.push(`${issues.toLocaleString()} issues`);
  } else if (provider === 'google_drive') {
    const total = stats.total_files ?? 0;
    const docs = stats.docs ?? 0;
    const sheets = stats.sheets ?? 0;
    const slides = stats.slides ?? 0;
    if (total > 0) parts.push(`${total.toLocaleString()} files`);
    if (docs > 0) parts.push(`${docs} docs`);
    if (sheets > 0) parts.push(`${sheets} sheets`);
    if (slides > 0) parts.push(`${slides} slides`);
  } else if (provider === 'slack') {
    const messages = stats.activities ?? 0;
    const channels = stats.channels ?? 0;
    if (channels > 0) {
      parts.push(`${messages.toLocaleString()} messages from ${channels} channel${channels !== 1 ? 's' : ''}`);
    } else {
      parts.push(`${messages.toLocaleString()} messages from 0 channels`);
    }
  } else {
  // CRM providers always show contact/account/deal counts (even if 0)
  const isCrmProvider = provider === 'hubspot' || provider === 'salesforce';
  if (isCrmProvider) {
    // Always show CRM stats for trust and debugging
    const contacts = stats.contacts ?? 0;
    const accounts = stats.accounts ?? 0;
    const deals = stats.deals ?? 0;
    parts.push(`${contacts.toLocaleString()} contacts`);
    parts.push(`${accounts.toLocaleString()} accounts`);
    parts.push(`${deals.toLocaleString()} deals`);
    if (stats.goals && stats.goals > 0) {
      parts.push(`${stats.goals.toLocaleString()} goals`);
    }
  } else {
    // Non-CRM: only show if > 0
    if (stats.contacts && stats.contacts > 0) {
      parts.push(`${stats.contacts.toLocaleString()} contacts`);
    }
    if (stats.accounts && stats.accounts > 0) {
      parts.push(`${stats.accounts.toLocaleString()} accounts`);
    }
    if (stats.deals && stats.deals > 0) {
      parts.push(`${stats.deals.toLocaleString()} deals`);
    }
  }
  }

  // Activity-based connectors (email, calendar, meetings) — Slack handled above
  if (provider !== 'slack' && stats.activities !== undefined) {
    const activityLabel = getActivityLabel(provider, stats.activities);
    parts.push(activityLabel);
  }

  if (parts.length === 0) return null;

  return parts.join(', ');
}

/**
 * Map CRM sync step to the noun used in the count label (e.g. "accounts", "deals").
 */
function getCrmStepNoun(step: string): string {
  if (step === 'accounts' || step === 'fetching accounts') return 'accounts';
  if (step === 'deals' || step === 'fetching deals') return 'deals';
  if (step === 'contacts' || step === 'fetching contacts') return 'contacts';
  if (step === 'activities') return 'activities';
  if (step === 'goals' || step === 'fetching goals') return 'goals';
  return 'items';
}

/**
 * Get a provider-specific label for activities count.
 * For CRM providers (HubSpot/Salesforce), pass optional step so the label matches the current sync phase (e.g. "0 accounts" during account sync).
 */
function getActivityLabel(provider: string, count: number, step?: string): string {
  const formatted = count.toLocaleString();
  if ((provider === 'hubspot' || provider === 'salesforce') && step !== undefined) {
    return `${formatted} ${getCrmStepNoun(step)}`;
  }
  switch (provider) {
    case 'gmail':
    case 'microsoft_mail':
      return `${formatted} emails`;
    case 'google_calendar':
    case 'microsoft_calendar':
      return `${formatted} meetings`;
    case 'slack':
      return `${formatted} messages`;
    case 'fireflies':
    case 'zoom':
      return `${formatted} recordings`;
    case 'hubspot':
    case 'salesforce':
      return `${formatted} activities`;
    default:
      return `${formatted} activities`;
  }
}

async function getResponseErrorMessage(response: Response, fallback: string): Promise<string> {
  const responseText = await response.text();
  if (!responseText) return fallback;

  try {
    const payload = JSON.parse(responseText) as { detail?: string; message?: string } | string;
    if (typeof payload === 'string' && payload.trim()) return payload;
    if (payload && typeof payload === 'object') {
      if (typeof payload.detail === 'string' && payload.detail.trim()) return payload.detail;
      if (typeof payload.message === 'string' && payload.message.trim()) return payload.message;
    }
  } catch {
    // Fall through to raw response text.
  }

  return responseText.trim() || fallback;
}

export function DataSources(): JSX.Element {
  // Get user/org from Zustand (auth state)
  const { user, organization, organizations } = useAppStore();
  const fetchUserOrganizations = useAppStore((state) => state.fetchUserOrganizations);
  

  // Zustand: Get integrations state
  const rawIntegrations = useIntegrations();
  const integrationsLoading = useIntegrationsLoading();
  const fetchIntegrations = useAppStore((state) => state.fetchIntegrations);

  // Fetch integrations when component mounts or user/org changes
  useEffect(() => {
    if (organization?.id && user?.id) {
      void fetchIntegrations();
    }
  }, [organization?.id, user?.id, fetchIntegrations]);

  useEffect(() => {
    if (user?.id) {
      void fetchUserOrganizations();
    }
  }, [user?.id, fetchUserOrganizations]);

  const [syncingProviders, setSyncingProviders] = useState<Set<string>>(new Set());
  /** True while org-wide "Sync all" is running (until all provider polls finish). */
  const [syncingAll, setSyncingAll] = useState<boolean>(false);

  // On mount/org change, check if any syncs are already in-flight (survives page reload)
  useEffect(() => {
    if (!organization?.id) return;
    const orgId: string = organization.id;
    const syncableProviders: string[] = rawIntegrations
      .filter((i) => i.isActive)
      .map((i) => i.provider);
    if (syncableProviders.length === 0) return;

    let cancelled = false;
    const checkInFlight = async (): Promise<void> => {
      const inFlight = new Set<string>();
      await Promise.all(
        syncableProviders.map(async (provider: string) => {
          try {
            const res: Response = await fetch(`${API_BASE}/sync/${orgId}/${provider}/status`);
            if (!res.ok) return;
            const data = (await res.json()) as { status: string };
            if (data.status === 'syncing') {
              inFlight.add(provider);
            }
          } catch {
            // ignore — status check is best-effort
          }
        }),
      );
      if (!cancelled && inFlight.size > 0) {
        setSyncingProviders((prev) => {
          const next = new Set(prev);
          for (const p of inFlight) next.add(p);
          return next;
        });
      }
    };
    void checkInFlight();
    return () => { cancelled = true; };
  }, [organization?.id, rawIntegrations]);
  const [disconnectingProviders, setDisconnectingProviders] = useState<Set<string>>(new Set());
  const [connectingProvider, setConnectingProvider] = useState<string | null>(null);
  const [slackMappings, setSlackMappings] = useState<SlackUserMapping[]>([]);
  const [slackMappingsLoading, setSlackMappingsLoading] = useState(false);
  const [slackMappingsError, setSlackMappingsError] = useState<string | null>(null);
  const [slackEmailInput, setSlackEmailInput] = useState('');
  const [slackCodeInput, setSlackCodeInput] = useState('');
  const [slackMappingStatus, setSlackMappingStatus] = useState<string | null>(null);
  const [slackSendCodeLoading, setSlackSendCodeLoading] = useState<boolean>(false);
  const [slackVerifyCodeLoading, setSlackVerifyCodeLoading] = useState<boolean>(false);
  const [showSlackVerificationModal, setShowSlackVerificationModal] = useState(false);
  const [showConnectModal, setShowConnectModal] = useState(false);
  const [connectSearch, setConnectSearch] = useState('');
  const [showCodeSandboxWarning, setShowCodeSandboxWarning] = useState(false);

  // MCP connect form state
  const [showMcpForm, setShowMcpForm] = useState(false);
  const [mcpName, setMcpName] = useState('');
  const [mcpEndpointUrl, setMcpEndpointUrl] = useState('');
  const [mcpBearerToken, setMcpBearerToken] = useState('');
  const [mcpConnecting, setMcpConnecting] = useState(false);
  const [mcpError, setMcpError] = useState<string | null>(null);

  // iSpot.tv connect form state (client credentials)
  const [showIspotForm, setShowIspotForm] = useState(false);
  const [ispotClientId, setIspotClientId] = useState('');
  const [ispotClientSecret, setIspotClientSecret] = useState('');
  const [ispotConnecting, setIspotConnecting] = useState(false);
  const [ispotError, setIspotError] = useState<string | null>(null);

  // Connectors from API (source of truth for Connect modal and display)
  const [connectorsFromApi, setConnectorsFromApi] = useState<ConnectorMetaFromApi[]>([]);
  const [connectorsLoading, setConnectorsLoading] = useState(true);
  const [connectorsError, setConnectorsError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setConnectorsLoading(true);
    setConnectorsError(null);
    fetch(`${API_BASE}/connectors`)
      .then((res) => {
        if (!res.ok) throw new Error(res.statusText);
        return res.json() as Promise<ConnectorMetaFromApi[]>;
      })
      .then((data) => {
        if (!cancelled) setConnectorsFromApi(data);
      })
      .catch((err) => {
        if (!cancelled) setConnectorsError(err instanceof Error ? err.message : 'Failed to load connectors');
      })
      .finally(() => {
        if (!cancelled) setConnectorsLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  /** Resolve display config for a provider (use API + overlay, or fallback). */
  const getConnectorDisplay = useCallback((provider: string): IntegrationConfigEntry & { connection_flow?: 'oauth' | 'builtin' | 'custom_credentials'; default_sharing?: { shareSyncedData: boolean; shareQueryAccess: boolean; shareWriteAccess: boolean }; hasSync?: boolean } => {
    const baseSlug = provider.startsWith('mcp_') ? 'mcp' : provider;
    const fallback = INTEGRATION_CONFIG_FALLBACK[baseSlug] ?? INTEGRATION_CONFIG_FALLBACK[provider];
    const apiConnector = connectorsFromApi.find((c) => c.slug === baseSlug);
    const override = CONNECTOR_DISPLAY_OVERRIDE[baseSlug] ?? CONNECTOR_DISPLAY_OVERRIDE[provider];
    if (apiConnector) {
      const icon = override?.icon ?? (apiConnector.icon || DEFAULT_ICON);
      const color = override?.color ?? DEFAULT_COLOR;
      return {
        name: apiConnector.name,
        description: apiConnector.description,
        icon,
        color,
        scope: apiConnector.scope,
        connection_flow: apiConnector.connection_flow,
        default_sharing: {
          shareSyncedData: apiConnector.default_sharing.share_synced_data,
          shareQueryAccess: apiConnector.default_sharing.share_query_access,
          shareWriteAccess: apiConnector.default_sharing.share_write_access,
        },
        hasSync: apiConnector.capabilities.includes('sync'),
      };
    }
    if (fallback) {
      return {
        ...fallback,
        default_sharing: { shareSyncedData: false, shareQueryAccess: false, shareWriteAccess: false },
        hasSync: true,
      };
    }
    return {
      name: provider,
      description: '',
      icon: DEFAULT_ICON,
      color: DEFAULT_COLOR,
      scope: 'user',
      default_sharing: { shareSyncedData: false, shareQueryAccess: false, shareWriteAccess: false },
      hasSync: false,
    };
  }, [connectorsFromApi]);

  /** Whether a provider is supported for display (in API list or mcp_* or fallback). */
  const isSupportedProvider = useCallback((provider: string): boolean => {
    if (provider.startsWith('mcp_')) return true;
    if (connectorsFromApi.length > 0) return connectorsFromApi.some((c) => c.slug === provider);
    return Object.prototype.hasOwnProperty.call(INTEGRATION_CONFIG_FALLBACK, provider) || (provider.startsWith('mcp_') && Object.prototype.hasOwnProperty.call(INTEGRATION_CONFIG_FALLBACK, 'mcp'));
  }, [connectorsFromApi]);

  // GitHub: available repos (from token), tracked repo ids, selection, loading
  interface GitHubRepo {
    github_repo_id: number;
    owner: string;
    name: string;
    full_name: string;
    description?: string;
    default_branch: string;
    is_private: boolean;
    language?: string;
    url: string;
  }
  const [githubAvailableRepos, setGithubAvailableRepos] = useState<GitHubRepo[]>([]);
  const [githubTrackedIds, setGithubTrackedIds] = useState<Set<number>>(new Set());
  const [githubTrackedNames, setGithubTrackedNames] = useState<string[]>([]);
  const [githubReposLoading, setGithubReposLoading] = useState(false);
  const [githubReposError, setGithubReposError] = useState<string | null>(null);
  const [githubSelectedIds, setGithubSelectedIds] = useState<Set<number>>(new Set());
  const [githubSaving, setGithubSaving] = useState(false);
  const [githubReposExpanded, setGithubReposExpanded] = useState(false);
  
  // Live sync progress from WebSocket
  const [syncProgress, setSyncProgress] = useState<Record<string, number>>({});
  const [syncStep, setSyncStep] = useState<Record<string, string>>({});

  // Sharing modal state
  interface SharingModalState {
    isOpen: boolean;
    integrationId: string;
    provider: string;
    providerName: string;
    shareSyncedData: boolean;
    shareQueryAccess: boolean;
    shareWriteAccess: boolean;
    isInitialSetup: boolean;  // true = post-OAuth, false = editing existing
  }
  const [sharingModal, setSharingModal] = useState<SharingModalState | null>(null);
  const [sharingSaving, setSharingSaving] = useState(false);

  // Disconnect confirmation modal state
  interface DisconnectModalState {
    provider: string;
    step: 'confirm' | 'ask-delete';
  }
  const [disconnectModal, setDisconnectModal] = useState<DisconnectModalState | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);
  /** Which integration tile id has the "resync from" dropdown open (null = closed). */
  const [resyncMenuOpenForId, setResyncMenuOpenForId] = useState<string | null>(null);
  const [disconnectError, setDisconnectError] = useState<string | null>(null);
  const [disconnectSuccess, setDisconnectSuccess] = useState<string | null>(null);
  const [sharingError, setSharingError] = useState<string | null>(null);

  const organizationId = organization?.id ?? '';
  const userId = user?.id ?? '';
  const activeMembership = organizations.find((org) => org.id === organizationId);
  const canConnectCodeSandbox = (user?.roles.includes('global_admin') ?? false) || activeMembership?.role === 'admin';
  const canSyncAllConnectors: boolean = useMemo((): boolean => {
    const isGlobalAdmin: boolean = user?.roles.includes('global_admin') ?? false;
    if (isGlobalAdmin) return true;
    return activeMembership?.role === 'admin';
  }, [user?.roles, activeMembership?.role]);

  useEffect(() => {
    if (resyncMenuOpenForId === null) return;
    const onPointerDown = (e: PointerEvent): void => {
      const t = e.target as HTMLElement | null;
      if (t?.closest('[data-resync-menu-root]')) return;
      setResyncMenuOpenForId(null);
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [resyncMenuOpenForId]);

  const connectBuiltinConnector = useCallback(
    async (
      provider: string,
      extraData?: Record<string, unknown> | null,
    ): Promise<void> => {
      const { data, error } = await apiRequest<{ status: string; provider: string }>(
        '/auth/integrations/connect-builtin',
        {
          method: 'POST',
          body: JSON.stringify({
            organization_id: organizationId,
            provider,
            user_id: userId,
            ...(extraData ? { extra_data: extraData } : {}),
          }),
        },
      );
      if (error || !data) {
        throw new Error(error ?? 'Failed to connect');
      }
    },
    [organizationId, userId],
  );

  const slackIntegration = rawIntegrations.find((integration) => integration.provider === 'slack');
  const slackConnected = Boolean(slackIntegration?.isActive);

  const githubIntegration = rawIntegrations.find((integration) => integration.provider === 'github');
  const githubConnected = Boolean(githubIntegration?.isActive);
  
  // Handle WebSocket messages for sync progress
  const handleWsMessage = useCallback((message: string) => {
    try {
      const data = JSON.parse(message) as { type: string; provider?: string; count?: number; status?: string; step?: string };
      if (data.type === 'sync_progress' && data.provider !== undefined && data.count !== undefined) {
        setSyncProgress((prev) => ({
          ...prev,
          [data.provider as string]: data.count as number,
        }));
        if (data.step) {
          setSyncStep((prev) => ({
            ...prev,
            [data.provider as string]: data.step as string,
          }));
        }
        
        // If sync is in progress, add to syncingProviders to show spinner
        if (data.status === 'syncing') {
          setSyncingProviders((prev) => new Set(prev).add(data.provider as string));
        }
        
        if (data.status === 'completed' || data.status === 'failed') {
          void fetchIntegrations();
          setTimeout(() => {
            setSyncProgress((prev) => {
              const next = { ...prev };
              delete next[data.provider as string];
              return next;
            });
            setSyncStep((prev) => {
              const next = { ...prev };
              delete next[data.provider as string];
              return next;
            });
            setSyncingProviders((prev) => {
              const next = new Set(prev);
              next.delete(data.provider as string);
              return next;
            });
          }, 1000);
        }
      }
    } catch {
      // Ignore non-JSON messages or parsing errors
    }
  }, [fetchIntegrations]);
  
  // Connect to WebSocket for sync progress updates - authenticated via JWT token
  useWebSocket(
    userId ? '/ws/chat' : '',
    {
      onMessage: handleWsMessage,
    },
    organizationId || undefined,
  );

  const fetchSlackMappings = useCallback(async (): Promise<void> => {
    if (!organizationId || !userId) return;
    setSlackMappingsLoading(true);
    setSlackMappingsError(null);
    try {
      const params = new URLSearchParams({ organization_id: organizationId, user_id: userId });
      const response = await fetch(`${API_BASE}/slack/user-mappings?${params.toString()}`);
      if (!response.ok) {
        throw new Error(`Failed to load Slack mappings: ${response.status}`);
      }
      const data = (await response.json()) as { mappings: SlackUserMapping[] };
      const mappingsFromIdentityTable = data.mappings
        .map((mapping) => ({
          id: mapping.id,
          external_userid: mapping.external_userid,
          external_email: mapping.external_email,
          source: mapping.source,
          match_source: mapping.match_source,
          created_at: mapping.created_at,
        }))
        .filter((mapping) => mapping.source.toLowerCase().includes('slack'));
      setSlackMappings(mappingsFromIdentityTable);
    } catch (error) {
      console.error('[DataSources] Failed to load Slack mappings:', error);
      setSlackMappingsError(error instanceof Error ? error.message : 'Unknown error');
    } finally {
      setSlackMappingsLoading(false);
    }
  }, [organizationId, userId]);

  useEffect(() => {
    if (slackConnected) {
      void fetchSlackMappings();
    }
  }, [fetchSlackMappings, slackConnected]);

  const fetchGitHubAvailableRepos = useCallback(async (): Promise<void> => {
    if (!organizationId) return;
    setGithubReposLoading(true);
    setGithubReposError(null);
    try {
      const res = await fetch(`${API_BASE}/sync/${organizationId}/github/repos`);
      if (!res.ok) throw new Error(`Failed to load repos: ${res.status}`);
      const data = (await res.json()) as { repos: GitHubRepo[] };
      setGithubAvailableRepos(data.repos ?? []);
    } catch (e) {
      setGithubReposError(e instanceof Error ? e.message : 'Failed to load repos');
      setGithubAvailableRepos([]);
    } finally {
      setGithubReposLoading(false);
    }
  }, [organizationId]);

  const fetchGitHubTrackedRepos = useCallback(async (): Promise<void> => {
    if (!organizationId) return;
    try {
      const res = await fetch(`${API_BASE}/sync/${organizationId}/github/repos/tracked`);
      if (!res.ok) return;
      const data = (await res.json()) as { repos: { github_repo_id: number; full_name?: string }[] };
      const repos = data.repos ?? [];
      const ids = new Set(repos.map((r) => r.github_repo_id));
      setGithubTrackedIds(ids);
      setGithubSelectedIds(ids);
      setGithubTrackedNames(repos.map((r) => r.full_name ?? '').filter(Boolean));
    } catch {
      setGithubTrackedIds(new Set());
      setGithubSelectedIds(new Set());
      setGithubTrackedNames([]);
    }
  }, [organizationId]);

  useEffect(() => {
    if (githubConnected && organizationId) {
      void fetchGitHubAvailableRepos();
      void fetchGitHubTrackedRepos();
    }
  }, [githubConnected, organizationId, fetchGitHubAvailableRepos, fetchGitHubTrackedRepos]);

  const handleGitHubTrackRepos = useCallback(async (): Promise<void> => {
    if (!organizationId || githubSaving) return;
    setGithubSaving(true);
    setGithubReposError(null);
    try {
      const res = await fetch(`${API_BASE}/sync/${organizationId}/github/repos/track`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ github_repo_ids: Array.from(githubSelectedIds) }),
      });
      if (!res.ok) {
        const err = (await res.json()) as { detail?: string };
        throw new Error(err.detail ?? `Failed to save: ${res.status}`);
      }
      await fetchGitHubTrackedRepos();
      void fetchIntegrations();
      setGithubReposExpanded(false);
    } catch (e) {
      setGithubReposError(e instanceof Error ? e.message : 'Failed to save');
    } finally {
      setGithubSaving(false);
    }
  }, [organizationId, githubSelectedIds, githubSaving, fetchGitHubTrackedRepos, fetchIntegrations]);

  // Transform raw integrations to display integrations with UI metadata
  // Filter out raw "microsoft" integration - it's a meta-integration from Nango's OAuth.
  // The actual data sources are microsoft_calendar and microsoft_mail.
  const integrations: DisplayIntegration[] = rawIntegrations
    .filter((integration) => {
      if (integration.provider === 'microsoft') {
        return false;
      }
      if (!isSupportedProvider(integration.provider)) {
        console.warn('[DataSources] Hiding unsupported integration provider from UI:', integration.provider);
        return false;
      }
      return true;
    })
    .map((integration) => {
      const config = getConnectorDisplay(integration.provider);
      const name: string = integration.displayName ?? config.name;
      return {
        ...integration,
        name,
        description: config.description,
        icon: config.icon,
        color: config.color,
        scope: config.scope,
        connected: integration.isActive,
      };
    });

  // Also include available (not connected) integrations
  const connectedProviders = new Set(integrations.map((i) => i.provider));
  const connectorSlugs = connectorsFromApi.length > 0
    ? connectorsFromApi.map((c) => c.slug)
    : Object.keys(INTEGRATION_CONFIG_FALLBACK);
  const availableProviders = connectorSlugs.filter((p) => !connectedProviders.has(p));
  const availableIntegrationsDisplay: DisplayIntegration[] = availableProviders.map((provider) => {
    const config = getConnectorDisplay(provider);
    const defaults = config.default_sharing ?? { shareSyncedData: false, shareQueryAccess: false, shareWriteAccess: false };
    return {
      id: provider,
      provider,
      userId: null,
      isActive: false,
      lastSyncAt: null,
      lastError: null,
      connectedAt: null,
      connectedBy: null,
      scope: config.scope,
      currentUserConnected: false,
      teamConnections: [],
      teamTotal: 0,
      syncStats: null,
      displayName: null,
      shareSyncedData: defaults.shareSyncedData,
      shareQueryAccess: defaults.shareQueryAccess,
      shareWriteAccess: defaults.shareWriteAccess,
      pendingSharingConfig: false,
      isOwner: false,
      name: config.name,
      description: config.description,
      icon: config.icon,
      color: config.color,
      connected: false,
    };
  });
  const allIntegrations: DisplayIntegration[] = [...integrations, ...availableIntegrationsDisplay];

  // Full list of all connectors for the Add Source modal (from API or fallback)
  const allConnectorsForModal: DisplayIntegration[] = connectorSlugs.map((provider: string): DisplayIntegration => {
    const config = getConnectorDisplay(provider);
    const defaults = config.default_sharing ?? { shareSyncedData: false, shareQueryAccess: false, shareWriteAccess: false };
    return {
      id: provider,
      provider,
      userId: null,
      isActive: false,
      lastSyncAt: null,
      lastError: null,
      connectedAt: null,
      connectedBy: null,
      scope: config.scope,
      currentUserConnected: false,
      teamConnections: [],
      teamTotal: 0,
      syncStats: null,
      displayName: null,
      shareSyncedData: defaults.shareSyncedData,
      shareQueryAccess: defaults.shareQueryAccess,
      shareWriteAccess: defaults.shareWriteAccess,
      pendingSharingConfig: false,
      isOwner: false,
      name: config.name,
      description: config.description,
      icon: config.icon,
      color: config.color,
      connected: false,
    };
  });

  const connectProvider = useCallback(async (provider: string): Promise<void> => {
    if (connectingProvider || !organizationId || !userId) return;

    setConnectingProvider(provider);

    try {
      const connectionFlow = getConnectorDisplay(provider).connection_flow;
      if (connectionFlow === 'custom_credentials') {
        setConnectingProvider(null);
        if (provider === 'mcp') {
          setMcpName('');
          setMcpEndpointUrl('');
          setMcpBearerToken('');
          setMcpError(null);
          setShowMcpForm(true);
        } else if (provider === 'ispot_tv') {
          setIspotClientId('');
          setIspotClientSecret('');
          setIspotError(null);
          setShowIspotForm(true);
        }
        return;
      }

      if (connectionFlow === 'builtin') {
        if (provider === 'code_sandbox' && !canConnectCodeSandbox) {
          throw new Error('Code Sandbox can only be connected by organization admins or global admins');
        }
        await connectBuiltinConnector(provider);
        void fetchIntegrations();
        setConnectingProvider(null);
        return;
      }

      // Get session token from backend for OAuth connectors
      const params = new URLSearchParams({ organization_id: organizationId, user_id: userId });
      const response = await fetch(
        `${API_BASE}/auth/connect/${provider}/session?${params.toString()}`
      );

      if (!response.ok) {
        throw new Error('Failed to get session token');
      }

      const data: { session_token: string; connection_id: string } = await response.json();
      const { session_token, connection_id } = data;

      // Initialize Nango and open connect UI in popup
      const nango = new Nango();

      nango.openConnectUI({
        sessionToken: session_token,
        onEvent: async (event) => {
          // Handle different possible event types from Nango
          const eventType = event.type as string;
          if (
            eventType === 'connect' ||
            eventType === 'connection-created' ||
            eventType === 'success'
          ) {
            // Connection successful - confirm and create integration record
            const eventData = event as { type: string; connectionId?: string; connection_id?: string; payload?: { connectionId?: string } };
            const nangoConnectionId = eventData.connectionId || eventData.connection_id || eventData.payload?.connectionId || connection_id;

            try {
              const confirmResponse = await fetch(`${API_BASE}/auth/integrations/confirm`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  provider,
                  connection_id: nangoConnectionId,
                  organization_id: organizationId,
                  user_id: userId,
                }),
              });

              if (!confirmResponse.ok) {
                console.error('Failed to confirm integration:', await confirmResponse.text());
                void fetchIntegrations();
                setConnectingProvider(null);
                return;
              }

              await confirmResponse.json();
              await fetchIntegrations();
            } catch (confirmError) {
              console.error('Error confirming integration:', confirmError);
            }

            setConnectingProvider(null);
          } else if (eventType === 'close' || eventType === 'closed') {
            // User closed the popup
            setConnectingProvider(null);
          }
        },
      });
    } catch (error) {
      console.error('Failed to connect:', error);
      setConnectingProvider(null);
    }
  }, [canConnectCodeSandbox, connectBuiltinConnector, connectingProvider, fetchIntegrations, getConnectorDisplay, organizationId, userId]);

  const handleConnect = useCallback(async (provider: string): Promise<void> => {
    if (provider === 'code_sandbox') {
      if (!canConnectCodeSandbox) {
        console.warn('[DataSources] Blocked non-admin Code Sandbox connection attempt');
        return;
      }
      setShowCodeSandboxWarning(true);
      return;
    }

    await connectProvider(provider);
  }, [canConnectCodeSandbox, connectProvider]);

  const handleConfirmCodeSandboxConnect = useCallback(async (): Promise<void> => {
    setShowCodeSandboxWarning(false);
    await connectProvider('code_sandbox');
  }, [connectProvider]);

  const handleMcpConnect = useCallback(async (): Promise<void> => {
    if (!organizationId || !userId || mcpConnecting) return;
    const trimmedUrl: string = mcpEndpointUrl.trim();
    const trimmedName: string = mcpName.trim();
    if (!trimmedName) {
      setMcpError('Name is required');
      return;
    }
    if (!trimmedUrl) {
      setMcpError('Endpoint URL is required');
      return;
    }

    setMcpConnecting(true);
    setMcpError(null);
    try {
      await connectBuiltinConnector('mcp', {
        display_name: trimmedName,
        endpoint_url: trimmedUrl,
        auth_header: mcpBearerToken.trim() || null,
      });
      setShowMcpForm(false);
      void fetchIntegrations();
    } catch (error) {
      setMcpError(error instanceof Error ? error.message : 'Failed to connect');
    } finally {
      setMcpConnecting(false);
    }
  }, [connectBuiltinConnector, fetchIntegrations, mcpBearerToken, mcpConnecting, mcpEndpointUrl, mcpName, organizationId, userId]);

  const handleIspotConnect = useCallback(async (): Promise<void> => {
    if (!organizationId || !userId || ispotConnecting) return;
    const clientId: string = ispotClientId.trim();
    const clientSecret: string = ispotClientSecret.trim();
    if (!clientId) {
      setIspotError('Client ID is required');
      return;
    }
    if (!clientSecret) {
      setIspotError('Client Secret is required');
      return;
    }
    setIspotConnecting(true);
    setIspotError(null);
    try {
      await connectBuiltinConnector('ispot_tv', { client_id: clientId, client_secret: clientSecret });
      setShowIspotForm(false);
      void fetchIntegrations();
    } catch (error) {
      setIspotError(error instanceof Error ? error.message : 'Failed to connect');
    } finally {
      setIspotConnecting(false);
    }
  }, [connectBuiltinConnector, fetchIntegrations, ispotClientId, ispotClientSecret, ispotConnecting, organizationId, userId]);

  const handleDisconnect = (provider: string): void => {
    if (!organizationId || !userId || disconnectingProviders.has(provider)) return;
    setSyncError(null);
    setDisconnectError(null);
    setDisconnectModal({ provider, step: 'confirm' });
  };

  const executeDisconnect = async (provider: string, deleteData: boolean): Promise<void> => {
    setDisconnectModal(null);

    // Set disconnecting state immediately for instant UI feedback
    setDisconnectingProviders((prev) => new Set(prev).add(provider));

    const params = new URLSearchParams({ organization_id: organizationId, user_id: userId });
    if (deleteData) {
      params.set('delete_data', 'true');
    }
    const url = `${API_BASE}/auth/integrations/${provider}?${params.toString()}`;

    try {
      const response = await fetch(url, { method: 'DELETE' });
      const responseText = await response.text();

      if (!response.ok) {
        let message = `Failed to disconnect ${getConnectorDisplay(provider).name}`;
        if (responseText) {
          try {
            const payload = JSON.parse(responseText) as { detail?: string; message?: string } | string;
            if (typeof payload === 'string' && payload.trim()) {
              message = payload;
            } else if (payload && typeof payload === 'object') {
              message = payload.detail ?? payload.message ?? message;
            }
          } catch {
            message = responseText;
          }
        }
        throw new Error(message);
      }

      // Parse response to show deletion summary
      try {
        const data = JSON.parse(responseText) as {
          deleted_activities?: number;
          deleted_contacts?: number;
          deleted_accounts?: number;
          deleted_deals?: number;
          deleted_goals?: number;
          deleted_pipelines?: number;
          deleted_meetings?: number;
        };
        const counts: string[] = [];
        if (data.deleted_activities)  counts.push(`${data.deleted_activities} activities`);
        if (data.deleted_deals)       counts.push(`${data.deleted_deals} deals`);
        if (data.deleted_contacts)    counts.push(`${data.deleted_contacts} contacts`);
        if (data.deleted_accounts)    counts.push(`${data.deleted_accounts} accounts`);
        if (data.deleted_goals)       counts.push(`${data.deleted_goals} goals`);
        if (data.deleted_pipelines)   counts.push(`${data.deleted_pipelines} pipelines`);
        if (data.deleted_meetings)    counts.push(`${data.deleted_meetings} orphaned meetings`);

        if (counts.length > 0) {
          setDisconnectSuccess(`Disconnected ${provider}. Deleted ${counts.join(', ')}.`);
          setTimeout(() => setDisconnectSuccess(null), 6000);
        }
      } catch {
        // Response wasn't JSON or didn't have deletion info, that's fine
      }

      try {
        await fetchIntegrations();
      } catch (fetchError) {
        console.error('Failed to refresh integrations after disconnect:', fetchError);
      }
      setDisconnectingProviders((prev) => {
        if (!prev.has(provider)) return prev;
        const next = new Set(prev);
        next.delete(provider);
        return next;
      });
    } catch (error) {
      console.error('Failed to disconnect:', error);
      setDisconnectError(`Failed to disconnect: ${error instanceof Error ? error.message : 'Unknown error'}`);
      setTimeout(() => setDisconnectError(null), 6000);
      setDisconnectingProviders((prev) => {
        const next = new Set(prev);
        next.delete(provider);
        return next;
      });
    }
  };

  // Save sharing preferences (POST for initial setup, PATCH for updates)
  const handleSaveSharing = async (): Promise<void> => {
    if (!sharingModal || sharingSaving) return;

    setSharingSaving(true);
    try {
      const endpoint = sharingModal.isInitialSetup
        ? `${API_BASE}/auth/integrations/${sharingModal.integrationId}/sharing`
        : `${API_BASE}/auth/integrations/${sharingModal.integrationId}/sharing`;
      const method = sharingModal.isInitialSetup ? 'POST' : 'PATCH';

      const params = userId ? new URLSearchParams({ user_id: userId }) : '';
      const response = await fetch(`${endpoint}?${params}`, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          share_synced_data: sharingModal.shareSyncedData,
          share_query_access: sharingModal.shareQueryAccess,
          share_write_access: sharingModal.shareWriteAccess,
        }),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error((err as { detail?: string }).detail ?? 'Failed to save sharing settings');
      }

      setSharingModal(null);
      void fetchIntegrations();
    } catch (error) {
      console.error('Failed to save sharing settings:', error);
      setSharingError(`Failed to save: ${error instanceof Error ? error.message : 'Unknown error'}`);
      setTimeout(() => setSharingError(null), 6000);
    } finally {
      setSharingSaving(false);
    }
  };

  // Open sharing modal for editing an existing integration
  const handleOpenSharingSettings = (integration: DisplayIntegration): void => {
    setSharingModal({
      isOpen: true,
      integrationId: integration.id,
      provider: integration.provider,
      providerName: integration.name,
      shareSyncedData: integration.shareSyncedData,
      shareQueryAccess: integration.shareQueryAccess,
      shareWriteAccess: integration.shareWriteAccess,
      isInitialSetup: false,
    });
  };

  const handleSync = async (provider: string, sinceIso?: string): Promise<void> => {
    if (syncingProviders.has(provider) || !organizationId) return;

    setSyncError(null);
    setSyncingProviders((prev) => new Set(prev).add(provider));

    try {
      // Google Drive uses its own sync endpoint (user-scoped)
      if (provider === 'google_drive') {
        const params = new URLSearchParams({ organization_id: organizationId, user_id: userId });
        const { error } = await apiRequest<{ status: string; message: string }>(`/drive/sync?${params.toString()}`, { method: 'POST' });
        if (error) throw new Error(error);
        // Drive sync runs in background — wait a bit then refresh integrations
        setTimeout(() => {
          setSyncingProviders((prev) => {
            const next = new Set(prev);
            next.delete(provider);
            return next;
          });
          void fetchIntegrations();
        }, 15000);
        return;
      }

      const syncUrl: string =
        sinceIso !== undefined && sinceIso.length > 0
          ? `${API_BASE}/sync/${organizationId}/${provider}?since=${encodeURIComponent(sinceIso)}`
          : `${API_BASE}/sync/${organizationId}/${provider}`;

      const response = await fetch(syncUrl, {
        method: 'POST',
      });

      if (!response.ok) throw new Error(await getResponseErrorMessage(response, `Failed to sync ${getConnectorDisplay(provider).name}`));

      // Poll for completion (GitHub sync can take 1–2 min; allow 2.5 min)
      let attempts = 0;
      const maxAttempts = 150;
      const checkStatus = async (): Promise<void> => {
        const statusRes = await fetch(`${API_BASE}/sync/${organizationId}/${provider}/status`);
        const status = await statusRes.json();

        if (status.status === 'completed' || status.status === 'failed' || attempts >= maxAttempts) {
          setSyncingProviders((prev) => {
            const next = new Set(prev);
            next.delete(provider);
            return next;
          });

          if (status.status === 'failed') {
            const providerName = getConnectorDisplay(provider).name;
            const detail = typeof status.error === 'string' && status.error.trim() ? status.error : `Failed to sync ${providerName}`;
            setSyncError(detail);
            setTimeout(() => setSyncError(null), 8000);
          }

          // Always refetch: on completion, failure, or timeout (slow syncs like GitHub can exceed 30s)
          void fetchIntegrations();
          // If we timed out, sync may still be running; refetch again after delay to pick up result
          if (attempts >= maxAttempts) {
            setTimeout(() => void fetchIntegrations(), 60000);
          }
        } else {
          attempts++;
          setTimeout(() => void checkStatus(), 2000);
        }
      };

      void checkStatus();
    } catch (error) {
      console.error('Sync error:', error);
      setSyncError(error instanceof Error ? error.message : `Failed to sync ${getConnectorDisplay(provider).name}`);
      setTimeout(() => setSyncError(null), 8000);
      setSyncingProviders((prev) => {
        const next = new Set(prev);
        next.delete(provider);
        return next;
      });
    }
  };

  const handleSyncAll = useCallback(async (): Promise<void> => {
    if (!organizationId || !canSyncAllConnectors || syncingAll) return;

    setSyncError(null);
    setSyncingAll(true);

    const { data, error } = await apiRequest<{
      status: string;
      organization_id: string;
      integrations: string[];
    }>(`/sync/${organizationId}/all`, { method: 'POST' });

    if (error !== null || data === null) {
      setSyncError(error ?? 'Failed to start sync');
      setTimeout(() => setSyncError(null), 8000);
      setSyncingAll(false);
      return;
    }

    const providers: readonly string[] = data.integrations;
    if (providers.length === 0) {
      setSyncingAll(false);
      return;
    }

    setSyncingProviders((prev) => {
      const next: Set<string> = new Set(prev);
      for (const p of providers) {
        next.add(p);
      }
      return next;
    });

    const maxAttempts: number = 150;
    const pollOne = async (provider: string): Promise<void> => {
      let attempts: number = 0;
      for (;;) {
        const statusRes: Response = await fetch(`${API_BASE}/sync/${organizationId}/${provider}/status`);
        const status: { status: string; error?: string } = (await statusRes.json()) as {
          status: string;
          error?: string;
        };
        if (status.status === 'completed' || status.status === 'failed' || attempts >= maxAttempts) {
          setSyncingProviders((prev) => {
            const next: Set<string> = new Set(prev);
            next.delete(provider);
            return next;
          });
          if (status.status === 'failed') {
            const providerName: string = getConnectorDisplay(provider).name;
            const detail: string =
              typeof status.error === 'string' && status.error.trim().length > 0
                ? status.error
                : `Failed to sync ${providerName}`;
            setSyncError(detail);
            setTimeout(() => setSyncError(null), 8000);
          }
          return;
        }
        attempts += 1;
        await new Promise<void>((resolve) => {
          setTimeout(resolve, 2000);
        });
      }
    };

    await Promise.all(providers.map((p: string) => pollOne(p)));
    void fetchIntegrations();
    setSyncingAll(false);
  }, [
    organizationId,
    canSyncAllConnectors,
    syncingAll,
    fetchIntegrations,
    getConnectorDisplay,
  ]);

  const handleSlackRequestCode = async (): Promise<void> => {
    if (!organizationId || !userId || !slackEmailInput.trim()) return;
    setSlackMappingStatus(null);
    setSlackSendCodeLoading(true);
    try {
      const response = await fetch(`${API_BASE}/slack/user-mappings/request-code`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: userId,
          organization_id: organizationId,
          email: slackEmailInput.trim(),
        }),
      });
      if (!response.ok) {
        let message = `Failed to send code: ${response.status}`;
        try {
          const data = await response.json();
          if (data && typeof data.detail === 'string') {
            message = data.detail;
          } else if (typeof data === 'string') {
            message = data;
          }
        } catch {
          const text = await response.text();
          if (text) message = text;
        }
        throw new Error(message);
      }
      setSlackMappingStatus('Verification code sent via Slack DM.');
    } catch (error) {
      console.error('[DataSources] Failed to request Slack code:', error);
      setSlackMappingStatus(
        error instanceof Error ? error.message : 'Failed to send verification code.',
      );
    } finally {
      setSlackSendCodeLoading(false);
    }
  };

  const handleSlackVerifyCode = async (): Promise<void> => {
    if (!organizationId || !userId || !slackEmailInput.trim() || !slackCodeInput.trim()) return;
    setSlackMappingStatus(null);
    setSlackVerifyCodeLoading(true);
    try {
      const response = await fetch(`${API_BASE}/slack/user-mappings/verify-code`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: userId,
          organization_id: organizationId,
          email: slackEmailInput.trim(),
          code: slackCodeInput.trim(),
        }),
      });
      if (!response.ok) {
        let message = `Failed to verify code: ${response.status}`;
        try {
          const data = await response.json();
          if (data && typeof data.detail === 'string') {
            message = data.detail;
          } else if (typeof data === 'string') {
            message = data;
          }
        } catch {
          const text = await response.text();
          if (text) message = text;
        }
        throw new Error(message);
      }
      setSlackMappingStatus('Slack account connected.');
      setSlackCodeInput('');
      setSlackEmailInput('');
      void fetchSlackMappings();
    } catch (error) {
      console.error('[DataSources] Failed to verify Slack code:', error);
      setSlackMappingStatus(
        error instanceof Error ? error.message : 'Failed to verify code.',
      );
    } finally {
      setSlackVerifyCodeLoading(false);
    }
  };

  const handleSlackDeleteMapping = async (mappingId: string): Promise<void> => {
    if (!organizationId || !userId) return;
    try {
      const params = new URLSearchParams({ organization_id: organizationId, user_id: userId });
      const response = await fetch(
        `${API_BASE}/slack/user-mappings/${mappingId}?${params.toString()}`,
        { method: 'DELETE' },
      );
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `Failed to delete mapping: ${response.status}`);
      }
      void fetchSlackMappings();
    } catch (error) {
      console.error('[DataSources] Failed to delete Slack mapping:', error);
      setSlackMappingStatus(
        error instanceof Error ? error.message : 'Failed to delete Slack mapping.',
      );
    }
  };

  // 1. My connectors — user-scoped integrations current user has connected (exclude org-scoped)
  // 2. Team Connectors — org-scoped integrations (Slack, Web Search, etc.); always separate, regardless of who connected
  // 3. From your team — user-scoped integrations connected by teammates (prompt user to add own)
  // 4. Available — no one in org has connected yet
  const myConnectors = allIntegrations.filter((i) => i.currentUserConnected && i.scope === 'user');
  const orgConnectors = allIntegrations.filter((i) => i.connected && i.scope === 'organization');
  const fromTeamConnectors = allIntegrations.filter(
    (i) => i.connected && !i.currentUserConnected && i.scope === 'user' && i.teamConnections.length > 0
  );
  const availableIntegrations = allIntegrations.filter((i) => !i.connected);

  const isImageIcon = (iconId: string): boolean =>
    iconId.startsWith('/') || iconId.startsWith('http');

  // Icon renderer — supports both react-icon keys and image paths
  const renderIcon = (iconId: string): JSX.Element => {
    if (isImageIcon(iconId)) {
      return <img src={iconId} alt="" className="w-full h-full rounded-xl object-cover" />;
    }
    const IconComponent = ICON_MAP[iconId] ?? HiGlobeAlt;
    return <IconComponent className="w-8 h-8" />;
  };

  // Color mapper
  const getColorClass = (color: string): string => {
    const colorMap: Record<string, string> = {
      'from-orange-500 to-orange-600': 'bg-orange-500',
      'from-blue-500 to-blue-600': 'bg-blue-500',
      'from-blue-400 to-blue-500': 'bg-blue-400',
      'from-purple-500 to-purple-600': 'bg-purple-500',
      'from-green-500 to-green-600': 'bg-green-500',
      'from-sky-500 to-sky-600': 'bg-sky-500',
      'from-red-500 to-red-600': 'bg-red-500',
      'from-violet-500 to-violet-600': 'bg-violet-500',
      'from-yellow-400 to-yellow-500': 'bg-yellow-400',
      'from-yellow-500 to-amber-500': 'bg-yellow-500',
      'from-indigo-500 to-violet-600': 'bg-indigo-500',
      'from-gray-600 to-gray-700': 'bg-gray-600',
      'from-gray-500 to-gray-600': 'bg-gray-500',
      'from-emerald-500 to-teal-600': 'bg-emerald-500',
    };
    return colorMap[color] ?? 'bg-surface-600';
  };

  // Tile state type for unified rendering
  type TileState = 'connected' | 'org-connected' | 'team-only' | 'available';

  // Unified integration tile component
  const renderIntegrationTile = (
    integration: DisplayIntegration,
    state: TileState
  ): JSX.Element => {
    const isConnecting = connectingProvider === integration.provider;
    const codeSandboxConnectBlocked = integration.provider === 'code_sandbox' && !canConnectCodeSandbox;
    const isStartingSync =
      (state === 'connected' || state === 'org-connected') &&
      getConnectorDisplay(integration.provider).hasSync !== false &&
      !integration.lastSyncAt &&
      !syncingProviders.has(integration.provider);
    const isSyncing = syncingProviders.has(integration.provider) || isStartingSync;
    const isDisconnecting = disconnectingProviders.has(integration.provider);

    // State-specific styling - no amber for team-only
    const cardClass = isDisconnecting
      ? 'card p-4 opacity-50 pointer-events-none transition-opacity duration-200'
      : 'card p-4';

    const iconOpacity = state === 'available' ? 'opacity-60' : '';

    // Badge config by state
    const badgeConfig: Record<TileState, { text: string; className: string } | null> = {
      'connected': { text: 'Connected', className: 'bg-emerald-500/20 text-emerald-400' },
      'org-connected': { text: 'Connected for team', className: 'bg-emerald-500/20 text-emerald-400' },
      'team-only': { text: 'From team', className: 'bg-surface-700 text-surface-300' },
      'available': null,
    };
    const badge = badgeConfig[state];

    // Button config by state
    const getButtonConfig = (): { text: string; className: string; action: () => void; disabled: boolean; hidden?: boolean } => {
      if (state === 'connected' || state === 'org-connected') {
        // Apollo, artifacts, apps, web_search, code_sandbox, twilio — no sync, on-demand only
        if (getConnectorDisplay(integration.provider).hasSync === false) {
          return {
            text: '',
            className: '',
            action: () => {},
            disabled: true,
            hidden: true,
          };
        }
        // Org-connected: anyone can sync (no owner restriction)
        return {
          text: isSyncing ? 'Syncing...' : 'Sync',
          className: 'px-4 py-2 text-sm font-medium text-surface-200 bg-surface-800 hover:bg-surface-700 disabled:opacity-50 rounded-lg transition-colors',
          action: () => void handleSync(integration.provider),
          disabled: isSyncing,
        };
      }
      // team-only and available: show Connect
      return {
        text: codeSandboxConnectBlocked
          ? 'Admins only'
          : (isConnecting ? 'Connecting...' : 'Connect'),
        className: codeSandboxConnectBlocked
          ? 'px-4 py-2 text-sm font-medium text-surface-500 border border-surface-700 rounded-lg cursor-not-allowed'
          : 'px-4 py-2 text-sm font-medium text-primary-400 border border-primary-500/30 hover:bg-primary-500/10 disabled:opacity-50 rounded-lg transition-colors',
        action: () => { void handleConnect(integration.provider); },
        disabled: isConnecting || codeSandboxConnectBlocked,
      };
    };
    const buttonConfig = getButtonConfig();

    // Sharing status badge (no "Setup required" - we use sensible defaults on connect)
    const renderSharingBadge = (): JSX.Element | null => {
      if (state !== 'connected') return null;

      if (integration.shareSyncedData) {
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-primary-500/20 text-primary-400">
            <HiShare className="w-3 h-3" />
            Shared with team
          </span>
        );
      }

      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-surface-700 text-surface-400">
          <HiLockClosed className="w-3 h-3" />
          Private
        </span>
      );
    };

    // Team connections info (for connected tiles; team-only/org-connected shows "Connected by" in body)
    const renderTeamInfo = (): JSX.Element | null => {
      if (state === 'team-only' || state === 'org-connected' || integration.teamTotal === 0) return null;

      const connectedCount = integration.teamConnections.length;
      const names = integration.teamConnections.map((tc) => tc.userName);
      const displayNames = names.slice(0, 3);
      const remaining = names.length - 3;
      const nameText = remaining > 0
        ? `${displayNames.join(', ')}, +${remaining} more`
        : displayNames.join(', ');

      return (
        <div className="mt-3 pt-3 border-t border-surface-700/50">
          <div className="flex items-center gap-2 text-sm text-surface-400">
            <HiUserGroup className="w-4 h-4" />
            <span>{connectedCount}/{integration.teamTotal} team members connected</span>
          </div>
          {connectedCount > 0 && (
            <p className="text-xs text-surface-500 mt-1 pl-6">{nameText}</p>
          )}
        </div>
      );
    };

    const renderSlackMapping = (): JSX.Element | null => {
      if (integration.provider !== 'slack' || state !== 'connected') return null;

      return (
        <div className="mt-4 pt-4 border-t border-surface-700/50 space-y-3">
          <div className="text-xs text-surface-400 space-y-1">
            <p><strong className="text-surface-300">To sync:</strong> Invite @Basebase to channels—type <code className="text-surface-300">/invite @Basebase</code> or add it from channel details.</p>
            <p><strong className="text-surface-300">To chat:</strong> Mention @Basebase in any channel it's in; it'll reply in the thread.</p>
          </div>
          <div className="flex items-center justify-between">
            <div>
              <h4 className="text-sm font-semibold text-surface-100">Slack Identity</h4>
              <p className="text-xs text-surface-400 mt-0.5">
                {slackMappings.length > 0
                  ? `${slackMappings.length} linked email${slackMappings.length !== 1 ? 's' : ''}`
                  : 'Link your Slack email to connect your account'}
              </p>
            </div>
            <button
              onClick={() => setShowSlackVerificationModal(true)}
              className="px-3 py-1.5 text-xs font-medium text-primary-300 border border-primary-500/30 hover:bg-primary-500/10 rounded-lg transition-colors"
            >
              {slackMappings.length > 0 ? 'Manage' : 'Link Account'}
            </button>
          </div>
        </div>
      );
    };

    const renderGitHubRepos = (): JSX.Element | null => {
      if (integration.provider !== 'github' || state !== 'connected') return null;
      const trackedCount = githubTrackedIds.size;
      const trackedNames =
        githubTrackedNames.length > 0
          ? githubTrackedNames
          : githubAvailableRepos
              .filter((r) => githubTrackedIds.has(r.github_repo_id))
              .map((r) => r.full_name);
      const showCompact = trackedCount > 0 && !githubReposExpanded;

      const toggleRepo = (id: number): void => {
        setGithubSelectedIds((prev) => {
          const next = new Set(prev);
          if (next.has(id)) next.delete(id);
          else next.add(id);
          return next;
        });
      };
      const selectAll = (): void => setGithubSelectedIds(new Set(githubAvailableRepos.map((r) => r.github_repo_id)));
      const selectNone = (): void => setGithubSelectedIds(new Set());

      return (
        <div className="mt-4 pt-4 border-t border-surface-700/50 space-y-3">
          <div className="flex items-start justify-between gap-2">
            <div>
              <h4 className="text-sm font-semibold text-surface-100">
                Repos to track
              </h4>
              <p className="text-xs text-surface-400 mt-0.5">
                {showCompact
                  ? `${trackedCount} repo${trackedCount !== 1 ? 's' : ''} tracked`
                  : 'Select which repositories to sync. Tracking for this team.'}
              </p>
            </div>
            {showCompact && (
              <button
                type="button"
                onClick={() => setGithubReposExpanded(true)}
                className="text-xs font-medium text-primary-400 hover:text-primary-300 whitespace-nowrap"
              >
                Change
              </button>
            )}
          </div>
          {showCompact ? (
            <p className="text-sm text-surface-300">
              {trackedNames.length > 0 ? trackedNames.join(', ') : '—'}
            </p>
          ) : (
            <>
              {githubReposError && (
                <p className="text-xs text-red-400">{githubReposError}</p>
              )}
              {githubReposLoading ? (
                <p className="text-sm text-surface-500">Loading repos…</p>
              ) : githubAvailableRepos.length === 0 ? (
                <p className="text-sm text-surface-500">No repos found. Check GitHub scopes (e.g. repo).</p>
              ) : (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={selectAll}
                      className="text-xs text-primary-400 hover:text-primary-300"
                    >
                      Select all
                    </button>
                    <span className="text-surface-600">|</span>
                    <button
                      type="button"
                      onClick={selectNone}
                      className="text-xs text-primary-400 hover:text-primary-300"
                    >
                      Select none
                    </button>
                    {trackedCount > 0 && (
                      <>
                        <span className="text-surface-600">|</span>
                        <button
                          type="button"
                          onClick={() => setGithubReposExpanded(false)}
                          className="text-xs text-primary-400 hover:text-primary-300"
                        >
                          Done
                        </button>
                      </>
                    )}
                  </div>
                  <ul className="max-h-48 overflow-y-auto space-y-1.5 rounded-lg border border-surface-700/60 p-2">
                    {githubAvailableRepos.map((repo) => {
                      const id = repo.github_repo_id;
                      const checked = githubSelectedIds.has(id);
                      return (
                        <li key={id} className="flex items-center gap-2">
                          <input
                            type="checkbox"
                            id={`gh-repo-${id}`}
                            checked={checked}
                            onChange={() => toggleRepo(id)}
                            className="rounded border-surface-600 bg-surface-800 text-primary-500 focus:ring-primary-500"
                          />
                          <label htmlFor={`gh-repo-${id}`} className="text-sm text-surface-200 cursor-pointer truncate flex-1 min-w-0">
                            <span className="font-medium">{repo.full_name}</span>
                            {repo.is_private && (
                              <span className="ml-2 text-xs text-surface-500">Private</span>
                            )}
                          </label>
                        </li>
                      );
                    })}
                  </ul>
                  <button
                    type="button"
                    onClick={() => void handleGitHubTrackRepos()}
                    disabled={githubSaving}
                    className="px-3 py-2 text-sm font-medium text-primary-300 border border-primary-500/30 hover:bg-primary-500/10 disabled:opacity-50 rounded-lg"
                  >
                    {githubSaving ? 'Saving…' : 'Save tracked repos'}
                  </button>
                </>
              )}
            </>
          )}
        </div>
      );
    };

    return (
      <div key={integration.id} className={cardClass}>
        <div className="flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-4">
          {/* Icon and name row on mobile */}
          <div className="flex items-center gap-3 sm:gap-4">
            <div className={`${isImageIcon(integration.icon) ? '' : getColorClass(integration.color) + ' p-2.5 sm:p-3 text-white'} rounded-xl ${iconOpacity} flex-shrink-0 w-[52px] h-[52px] sm:w-14 sm:h-14 flex items-center justify-center overflow-hidden`}>
              {renderIcon(integration.icon)}
            </div>

            {/* Content */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <h3 className="font-medium text-surface-100">{integration.name}</h3>
                {badge && (
                  <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${badge.className}`}>
                    {badge.text}
                  </span>
                )}
                {renderSharingBadge()}
              </div>
              <p className="text-sm text-surface-400 mt-0.5 hidden sm:block">{integration.description}</p>
              {state === 'connected' && integration.connectedBy && !integration.isOwner && (
                <p className="text-xs text-surface-500 mt-1 hidden sm:block">
                  Connected by {integration.connectedBy}
                </p>
              )}
              {(state === 'connected' || state === 'org-connected') && integration.lastSyncAt && !isSyncing && (
                <p className="text-xs text-surface-500 mt-1 hidden sm:block">
                  Last synced: {new Date(integration.lastSyncAt).toLocaleString()}
                </p>
              )}
              {(state === 'connected' || state === 'org-connected') && (isStartingSync || syncProgress[integration.provider] !== undefined || integration.syncStats) && (
                <p className="text-xs text-surface-400 mt-1 hidden sm:block">
                  {isStartingSync ? (
                    <span className="text-primary-400">Starting sync…</span>
                  ) : syncProgress[integration.provider] !== undefined ? (
                    <span className="text-primary-400">
                      Syncing{syncStep[integration.provider] ? ` ${syncStep[integration.provider]}` : ''}... {getActivityLabel(integration.provider, syncProgress[integration.provider] ?? 0, syncStep[integration.provider])}
                    </span>
                  ) : integration.syncStats ? (
                    formatSyncStats(integration.syncStats, integration.provider)
                  ) : null}
                </p>
              )}
              {(state === 'connected' || state === 'org-connected') && integration.lastError && !isSyncing && (
                <p className="text-xs text-red-400 mt-1">Error: {integration.lastError}</p>
              )}
              {state === 'org-connected' && (
                <div className="mt-2 text-xs text-surface-400">
                  <p>
                    Connected by {integration.teamConnections.map((tc) => tc.userName).join(', ')}
                  </p>
                </div>
              )}
              {state === 'team-only' && (
                <div className="mt-2 space-y-1 text-xs text-surface-400">
                  <p>
                    Connected by {integration.teamConnections.map((tc) => tc.userName).join(', ')}
                  </p>
                  {integration.shareSyncedData || integration.shareQueryAccess || integration.shareWriteAccess ? (
                    <p className="text-surface-300">
                      Shared with you:{' '}
                      {[
                        integration.shareSyncedData && 'synced data',
                        integration.shareQueryAccess && 'query access',
                        integration.shareWriteAccess && 'write access',
                      ].filter(Boolean).join(', ')}
                    </p>
                  ) : (
                    <p className="text-surface-500">No sharing enabled yet</p>
                  )}
                </div>
              )}
              {state === 'available' && codeSandboxConnectBlocked && (
                <p className="mt-2 text-xs text-amber-400">
                  Requires organization admin or global admin access to connect.
                </p>
              )}
            </div>
          </div>

          {/* Actions - full width on mobile, right-aligned on desktop */}
          <div className="flex items-center gap-2 sm:flex-shrink-0 sm:ml-auto">
            {/* Settings button for owners */}
            {state === 'connected' && integration.isOwner && (
              <button
                onClick={() => handleOpenSharingSettings(integration)}
                title="Sharing settings"
                className="p-2 text-surface-400 hover:text-surface-200 hover:bg-surface-800 rounded-lg transition-colors"
              >
                <HiCog className="w-5 h-5" />
              </button>
            )}
            {!buttonConfig.hidden && (() => {
              const showResyncSplit: boolean =
                (state === 'connected' || state === 'org-connected') &&
                integration.provider !== 'google_drive' &&
                getConnectorDisplay(integration.provider).hasSync !== false;

              if (showResyncSplit) {
                const baseBtn: string =
                  'text-sm font-medium text-surface-200 bg-surface-800 hover:bg-surface-700 disabled:opacity-50 transition-colors flex items-center justify-center gap-2';
                return (
                  <div
                    data-resync-menu-root
                    className="relative z-10 flex flex-1 sm:flex-initial rounded-lg border border-surface-700 overflow-visible"
                  >
                    <button
                      type="button"
                      onClick={() => void handleSync(integration.provider)}
                      disabled={buttonConfig.disabled}
                      className={`${baseBtn} px-3 sm:px-4 py-2 flex-1 sm:flex-initial rounded-l-lg border-0`}
                    >
                      {(isConnecting || isSyncing) && (
                        <svg className="w-4 h-4 animate-spin shrink-0" fill="none" viewBox="0 0 24 24">
                          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                        </svg>
                      )}
                      {isSyncing ? 'Syncing...' : 'Sync'}
                    </button>
                    <div className="relative flex">
                      <button
                        type="button"
                        title="Resync from earlier time"
                        disabled={buttonConfig.disabled}
                        onPointerDown={(e) => {
                          e.stopPropagation();
                        }}
                        onClick={() =>
                          setResyncMenuOpenForId((cur) =>
                            cur === integration.id ? null : integration.id,
                          )}
                        className={`${baseBtn} px-2 py-2 border-l border-surface-700 rounded-r-lg`}
                        aria-expanded={resyncMenuOpenForId === integration.id}
                        aria-haspopup="menu"
                      >
                        <HiChevronDown className="w-4 h-4" />
                      </button>
                      {resyncMenuOpenForId === integration.id && (
                        <div
                          role="menu"
                          className="absolute right-0 top-full mt-1 z-[200] min-w-[11rem] rounded-lg border border-surface-700 bg-surface-900 py-1 shadow-lg"
                        >
                          <button
                            type="button"
                            role="menuitem"
                            className="w-full text-left px-3 py-2 text-sm text-surface-200 hover:bg-surface-800"
                            onClick={() => {
                              setResyncMenuOpenForId(null);
                              void handleSync(integration.provider, isoUtcSubtractMs(RESYNC_OFFSET_MS.hours24));
                            }}
                          >
                            Last 24 hours
                          </button>
                          <button
                            type="button"
                            role="menuitem"
                            className="w-full text-left px-3 py-2 text-sm text-surface-200 hover:bg-surface-800"
                            onClick={() => {
                              setResyncMenuOpenForId(null);
                              void handleSync(integration.provider, isoUtcSubtractMs(RESYNC_OFFSET_MS.days7));
                            }}
                          >
                            Last 7 days
                          </button>
                          <button
                            type="button"
                            role="menuitem"
                            className="w-full text-left px-3 py-2 text-sm text-surface-200 hover:bg-surface-800"
                            onClick={() => {
                              setResyncMenuOpenForId(null);
                              void handleSync(integration.provider, isoUtcSubtractMs(RESYNC_OFFSET_MS.days30));
                            }}
                          >
                            Last 30 days
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                );
              }

              return (
                <button
                  onClick={buttonConfig.action}
                  disabled={buttonConfig.disabled}
                  className={`${buttonConfig.className} flex items-center justify-center gap-2 flex-1 sm:flex-initial`}
                >
                  {(isConnecting || isSyncing) && (
                    <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  )}
                  {buttonConfig.text}
                </button>
              );
            })()}
            {((state === 'connected' && integration.isOwner) || state === 'org-connected') && (
              <button
                onClick={() => void handleDisconnect(integration.provider)}
                disabled={isDisconnecting}
                className="px-3 sm:px-4 py-2 text-sm font-medium text-red-400 hover:text-red-300 hover:bg-red-500/10 disabled:opacity-50 rounded-lg transition-colors flex items-center gap-2"
              >
                {isDisconnecting && (
                  <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                )}
                {isDisconnecting ? 'Disconnecting...' : 'Disconnect'}
              </button>
            )}
          </div>
        </div>

        {/* Team connections footer */}
        {renderTeamInfo()}
        {renderSlackMapping()}
        {renderGitHubRepos()}
      </div>
    );
  };

  if (integrationsLoading && rawIntegrations.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  // Filtered list for the Add Source modal (always starts from full connector list)
  const filteredConnectModalIntegrations: DisplayIntegration[] = allConnectorsForModal.filter(
    (i: DisplayIntegration): boolean => {
      if (!connectSearch.trim()) return true;
      const query: string = connectSearch.toLowerCase();
      return (
        i.name.toLowerCase().includes(query) ||
        i.description.toLowerCase().includes(query) ||
        i.provider.toLowerCase().includes(query)
      );
    }
  );

  return (
    <div className="flex-1 overflow-y-auto overflow-x-hidden">
      {/* Header - hidden on mobile since AppLayout has mobile header */}
      <header className="hidden md:block sticky top-0 z-20 bg-surface-950 border-b border-surface-800 px-4 md:px-8 py-4 md:py-6">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h1 className="text-xl md:text-2xl font-bold text-surface-50">Connectors</h1>
            <p className="text-surface-400 mt-1 text-sm md:text-base">
              Connect your sales tools to unlock AI-powered insights
            </p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {canSyncAllConnectors && (
              <button
                type="button"
                onClick={() => void handleSyncAll()}
                disabled={syncingAll}
                className="px-4 py-2.5 text-sm font-semibold text-surface-100 bg-surface-800 hover:bg-surface-700 border border-surface-600 rounded-lg transition-colors flex items-center gap-2 disabled:opacity-50"
                title="Sync every connected integration for this organization"
              >
                {syncingAll ? (
                  <>
                    <span className="w-4 h-4 border-2 border-surface-400 border-t-transparent rounded-full animate-spin" />
                    Syncing…
                  </>
                ) : (
                  <>
                    <HiLightningBolt className="w-4 h-4 text-amber-400" />
                    Sync all
                  </>
                )}
              </button>
            )}
            <button
              type="button"
              onClick={() => { setShowConnectModal(true); setConnectSearch(''); }}
              className="px-5 py-2.5 text-sm font-semibold text-white bg-primary-600 hover:bg-primary-500 rounded-lg transition-colors flex items-center gap-2 shadow-lg shadow-primary-600/20"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
              </svg>
              Add Source
            </button>
          </div>
        </div>
      </header>

      {/* Connect Source Modal */}
      {showConnectModal && (
        <div className="fixed inset-0 z-50 flex items-start justify-center pt-[10vh]">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setShowConnectModal(false)}
          />
          {/* Modal */}
          <div className="relative bg-surface-900 border border-surface-700 rounded-2xl shadow-2xl w-full max-w-lg mx-4 overflow-hidden">
            <div className="p-5 border-b border-surface-700/50">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-surface-100">Connect a Source</h2>
                <button
                  onClick={() => setShowConnectModal(false)}
                  className="text-surface-400 hover:text-surface-200 transition-colors"
                >
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
              <input
                type="text"
                value={connectSearch}
                onChange={(e) => setConnectSearch(e.target.value)}
                placeholder="Search sources..."
                autoFocus
                className="w-full rounded-lg bg-surface-800 border border-surface-600 px-4 py-2.5 text-sm text-surface-100 placeholder:text-surface-500 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500/30"
              />
            </div>
            <ul className="max-h-[50vh] overflow-y-auto p-2">
              {connectorsLoading ? (
                <li className="px-4 py-8 text-center text-sm text-surface-500">
                  Loading sources...
                </li>
              ) : connectorsError && allConnectorsForModal.length === 0 ? (
                <li className="px-4 py-8 text-center text-sm text-red-400">
                  {connectorsError}
                </li>
              ) : filteredConnectModalIntegrations.length === 0 ? (
                <li className="px-4 py-8 text-center text-sm text-surface-500">
                  No sources match your search.
                </li>
              ) : (
                filteredConnectModalIntegrations.map((integration) => {
                  const isConnecting: boolean = connectingProvider === integration.provider;
                  const codeSandboxBlocked: boolean = integration.provider === 'code_sandbox' && !canConnectCodeSandbox;
                  return (
                    <li key={integration.provider}>
                      <button
                        onClick={() => {
                          setShowConnectModal(false);
                          void handleConnect(integration.provider);
                        }}
                        disabled={isConnecting || codeSandboxBlocked}
                        className="w-full flex items-center gap-4 px-4 py-3 rounded-xl hover:bg-surface-800 transition-colors text-left group disabled:opacity-50"
                      >
                        <div className={`${isImageIcon(integration.icon) ? '' : getColorClass(integration.color) + ' p-2 text-white'} rounded-lg flex-shrink-0 w-10 h-10 flex items-center justify-center overflow-hidden`}>
                          {renderIcon(integration.icon)}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="font-medium text-surface-100 group-hover:text-white transition-colors">
                            {integration.name}
                          </div>
                          <div className="text-xs text-surface-500 truncate mt-0.5">
                            {codeSandboxBlocked
                              ? `${integration.description} • Admin access required to connect`
                              : integration.description}
                          </div>
                        </div>
                        {isConnecting ? (
                          <svg className="w-5 h-5 animate-spin text-primary-400 flex-shrink-0" fill="none" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                          </svg>
                        ) : (
                          <svg className="w-5 h-5 text-surface-600 group-hover:text-surface-400 transition-colors flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                          </svg>
                        )}
                      </button>
                    </li>
                  );
                })
              )}
            </ul>
          </div>
        </div>
      )}

      {/* MCP Connect Form Modal */}
      {showMcpForm && (
        <div className="fixed inset-0 z-50 flex items-start justify-center pt-[10vh]">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => { if (!mcpConnecting) setShowMcpForm(false); }}
          />
          <div className="relative bg-surface-900 border border-surface-700 rounded-2xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
            <div className="p-5 border-b border-surface-700/50">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="bg-gradient-to-br from-cyan-500 to-blue-600 p-2 rounded-lg text-white">
                    <HiLink className="w-5 h-5" />
                  </div>
                  <h2 className="text-lg font-semibold text-surface-100">Connect MCP Server</h2>
                </div>
                <button
                  onClick={() => { if (!mcpConnecting) setShowMcpForm(false); }}
                  className="text-surface-400 hover:text-surface-200 transition-colors"
                >
                  <HiX className="w-5 h-5" />
                </button>
              </div>
            </div>
            <form
              onSubmit={(e) => { e.preventDefault(); void handleMcpConnect(); }}
              className="p-5 space-y-4"
            >
              <div>
                <label htmlFor="mcp-name" className="block text-sm font-medium text-surface-300 mb-1.5">
                  Name <span className="text-red-400">*</span>
                </label>
                <input
                  id="mcp-name"
                  type="text"
                  value={mcpName}
                  onChange={(e) => setMcpName(e.target.value)}
                  placeholder="e.g. SimilarWeb, Stripe, Notion"
                  required
                  disabled={mcpConnecting}
                  autoFocus
                  className="w-full rounded-lg bg-surface-800 border border-surface-600 px-4 py-2.5 text-sm text-surface-100 placeholder:text-surface-500 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500/30 disabled:opacity-50"
                />
              </div>
              <div>
                <label htmlFor="mcp-url" className="block text-sm font-medium text-surface-300 mb-1.5">
                  Endpoint URL <span className="text-red-400">*</span>
                </label>
                <input
                  id="mcp-url"
                  type="url"
                  value={mcpEndpointUrl}
                  onChange={(e) => setMcpEndpointUrl(e.target.value)}
                  placeholder="https://mcp.example.com/mcp"
                  required
                  disabled={mcpConnecting}
                  className="w-full rounded-lg bg-surface-800 border border-surface-600 px-4 py-2.5 text-sm text-surface-100 placeholder:text-surface-500 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500/30 disabled:opacity-50"
                />
              </div>
              <div>
                <label htmlFor="mcp-token" className="block text-sm font-medium text-surface-300 mb-1.5">
                  Auth Header <span className="text-surface-500 font-normal">(optional)</span>
                </label>
                <input
                  id="mcp-token"
                  type="password"
                  value={mcpBearerToken}
                  onChange={(e) => setMcpBearerToken(e.target.value)}
                  placeholder="e.g. api-key: abc123  or  Bearer token"
                  disabled={mcpConnecting}
                  className="w-full rounded-lg bg-surface-800 border border-surface-600 px-4 py-2.5 text-sm text-surface-100 placeholder:text-surface-500 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500/30 disabled:opacity-50"
                />
              </div>
              {mcpError && (
                <div className="text-sm text-red-400 bg-red-400/10 border border-red-400/20 rounded-lg px-3 py-2">
                  {mcpError}
                </div>
              )}
              <div className="flex gap-3 pt-1">
                <button
                  type="button"
                  onClick={() => setShowMcpForm(false)}
                  disabled={mcpConnecting}
                  className="flex-1 px-4 py-2.5 text-sm font-medium text-surface-300 bg-surface-800 hover:bg-surface-700 border border-surface-600 rounded-lg transition-colors disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={mcpConnecting || !mcpName.trim() || !mcpEndpointUrl.trim()}
                  className="flex-1 px-4 py-2.5 text-sm font-semibold text-white bg-primary-600 hover:bg-primary-500 rounded-lg transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                >
                  {mcpConnecting ? (
                    <>
                      <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                      Connecting...
                    </>
                  ) : (
                    'Connect'
                  )}
                </button>
              </div>
              <p className="text-xs text-surface-500">
                We&apos;ll validate the connection and discover available tools from the MCP server.
              </p>
            </form>
          </div>
        </div>
      )}

      {/* Code Sandbox Risk Warning Modal */}
      {showCodeSandboxWarning && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="w-full max-w-lg rounded-2xl border border-amber-500/30 bg-surface-900 shadow-2xl">
            <div className="border-b border-surface-700/60 p-5">
              <div className="flex items-start gap-3">
                <div className="mt-0.5 rounded-xl bg-amber-500/15 p-2 text-amber-300">
                  <HiLightningBolt className="h-5 w-5" />
                </div>
                <div>
                  <h2 className="text-lg font-semibold text-surface-100">
                    Warning: Code Sandbox can run insecure code
                  </h2>
                  <p className="mt-1 text-sm text-surface-400">
                    This connector can execute arbitrary code and shell commands. If misused, it may
                    expose secrets, enable data exfiltration, or lead to a data breach.
                  </p>
                </div>
              </div>
            </div>
            <div className="space-y-4 p-5">
              <div className="rounded-xl border border-amber-500/20 bg-amber-500/10 p-4 text-sm text-amber-100">
                <p className="font-medium text-amber-200">Admin-only connector</p>
                <p className="mt-1 text-amber-100/90">
                  Only organization admins or global admins should connect Code Sandbox. Continue
                  only if you understand the risk and explicitly want to enable it for your org.
                </p>
              </div>
              <p className="text-sm text-surface-400">
                Use this connector only at your own risk.
              </p>
              <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
                <button
                  onClick={() => setShowCodeSandboxWarning(false)}
                  className="rounded-lg border border-surface-600 px-4 py-2 text-sm font-medium text-surface-200 transition-colors hover:bg-surface-800"
                >
                  Cancel
                </button>
                <button
                  onClick={() => void handleConfirmCodeSandboxConnect()}
                  className="rounded-lg bg-amber-500 px-4 py-2 text-sm font-semibold text-surface-950 transition-colors hover:bg-amber-400"
                >
                  Connect at my own risk
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {showIspotForm && (
        <div className="fixed inset-0 z-50 flex items-start justify-center pt-[10vh]">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => { if (!ispotConnecting) setShowIspotForm(false); }}
          />
          <div className="relative bg-surface-900 border border-surface-700 rounded-2xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
            <div className="p-5 border-b border-surface-700/50">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="bg-gradient-to-br from-emerald-500 to-teal-600 p-2 rounded-lg text-white">
                    <HiGlobeAlt className="w-5 h-5" />
                  </div>
                  <h2 className="text-lg font-semibold text-surface-100">Connect iSpot.tv</h2>
                </div>
                <button
                  onClick={() => { if (!ispotConnecting) setShowIspotForm(false); }}
                  className="text-surface-400 hover:text-surface-200 transition-colors"
                >
                  <HiX className="w-5 h-5" />
                </button>
              </div>
            </div>
            <form
              onSubmit={(e) => { e.preventDefault(); void handleIspotConnect(); }}
              className="p-5 space-y-4"
            >
              <p className="text-sm text-surface-400">
                Enter your iSpot.tv OAuth client credentials (from your iSpot account manager). No browser sign-in required.
              </p>
              <div>
                <label htmlFor="ispot-client-id" className="block text-sm font-medium text-surface-300 mb-1.5">
                  OAuth Client ID <span className="text-red-400">*</span>
                </label>
                <input
                  id="ispot-client-id"
                  type="text"
                  value={ispotClientId}
                  onChange={(e) => setIspotClientId(e.target.value)}
                  placeholder="Client ID"
                  required
                  disabled={ispotConnecting}
                  autoFocus
                  className="w-full rounded-lg bg-surface-800 border border-surface-600 px-4 py-2.5 text-sm text-surface-100 placeholder:text-surface-500 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500/30 disabled:opacity-50"
                />
              </div>
              <div>
                <label htmlFor="ispot-client-secret" className="block text-sm font-medium text-surface-300 mb-1.5">
                  OAuth Client Secret <span className="text-red-400">*</span>
                </label>
                <input
                  id="ispot-client-secret"
                  type="password"
                  value={ispotClientSecret}
                  onChange={(e) => setIspotClientSecret(e.target.value)}
                  placeholder="Client Secret"
                  required
                  disabled={ispotConnecting}
                  className="w-full rounded-lg bg-surface-800 border border-surface-600 px-4 py-2.5 text-sm text-surface-100 placeholder:text-surface-500 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500/30 disabled:opacity-50"
                />
              </div>
              {ispotError && (
                <div className="text-sm text-red-400 bg-red-400/10 border border-red-400/20 rounded-lg px-3 py-2">
                  {ispotError}
                </div>
              )}
              <div className="flex gap-3 pt-1">
                <button
                  type="button"
                  onClick={() => setShowIspotForm(false)}
                  disabled={ispotConnecting}
                  className="flex-1 px-4 py-2.5 text-sm font-medium text-surface-300 bg-surface-800 hover:bg-surface-700 border border-surface-600 rounded-lg transition-colors disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={ispotConnecting || !ispotClientId.trim() || !ispotClientSecret.trim()}
                  className="flex-1 px-4 py-2.5 text-sm font-semibold text-white bg-primary-600 hover:bg-primary-500 rounded-lg transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                >
                  {ispotConnecting ? (
                    <>
                      <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                      Connecting...
                    </>
                  ) : (
                    'Connect'
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <div className="max-w-4xl mx-auto px-4 md:px-8 py-4 md:py-8 space-y-6 md:space-y-10">
        {/* My connectors */}
        <section>
          <h2 className="text-lg font-semibold text-surface-100 mb-4 flex items-center gap-2">
            <span className="w-2 h-2 bg-emerald-500 rounded-full" />
            My connectors ({myConnectors.length})
          </h2>

          {myConnectors.length === 0 ? (
            <div className="card p-6 md:p-8">
              <div className="text-center mb-6">
                <div className="w-16 h-16 rounded-full bg-surface-800 flex items-center justify-center mx-auto mb-4">
                  <svg className="w-8 h-8 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                  </svg>
                </div>
                <h3 className="text-surface-200 font-medium mb-2">No connectors connected</h3>
                <p className="text-surface-400 text-sm">
                  Connect your first data source to get started
                </p>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 md:gap-4">
                {COMMON_INTEGRATION_KEYS.filter(
                  (provider) =>
                    connectorSlugs.includes(provider) &&
                    availableIntegrations.some((i) => i.provider === provider),
                ).map((provider) => {
                  const config = getConnectorDisplay(provider);
                  const isConnecting = connectingProvider === provider;
                  return (
                    <button
                      key={provider}
                      type="button"
                      onClick={() => { void handleConnect(provider); }}
                      disabled={isConnecting}
                      className="card p-4 text-left hover:border-surface-600 hover:bg-surface-800/50 transition-colors disabled:opacity-50 flex items-start gap-3 group"
                    >
                      <div className={`${isImageIcon(config.icon) ? '' : getColorClass(config.color) + ' p-2 text-white'} rounded-lg flex-shrink-0 w-10 h-10 flex items-center justify-center overflow-hidden opacity-90 group-hover:opacity-100 transition-opacity`}>
                        {renderIcon(config.icon)}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="font-medium text-surface-100 group-hover:text-white transition-colors">
                          {config.name}
                        </div>
                        <div className="text-xs text-surface-500 mt-0.5 line-clamp-2">
                          {config.description}
                        </div>
                      </div>
                      {isConnecting ? (
                        <svg className="w-5 h-5 animate-spin text-primary-400 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24">
                          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                        </svg>
                      ) : (
                        <svg className="w-5 h-5 text-surface-500 group-hover:text-surface-400 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                        </svg>
                      )}
                    </button>
                  );
                })}
              </div>
              <p className="text-center text-sm text-surface-500 mt-4">
                Looking for something else?{' '}
                <button
                  type="button"
                  onClick={() => { setShowConnectModal(true); setConnectSearch(''); }}
                  className="text-primary-400 hover:text-primary-300 underline underline-offset-2"
                >
                  Browse all connectors
                </button>
              </p>
            </div>
          ) : (
            <div className="grid gap-4">
              {myConnectors.map((integration) => renderIntegrationTile(integration, 'connected'))}
            </div>
          )}
        </section>

        {/* Team Connectors — org-scoped integrations connected by a teammate */}
        {orgConnectors.length > 0 && (
          <section>
            <h2 className="text-lg font-semibold text-surface-100 mb-4 flex items-center gap-2">
              <span className="w-2 h-2 bg-emerald-500 rounded-full" />
              Team Connectors ({orgConnectors.length})
            </h2>
            <p className="text-sm text-surface-400 mb-4">
              Team scoped connectors connected by a teammate. Anyone can sync or disconnect.
            </p>
            <div className="grid gap-4">
              {orgConnectors.map((integration) => renderIntegrationTile(integration, 'org-connected'))}
            </div>
          </section>
        )}

        {/* From your team — user-scoped integrations connected by teammates; prompt to add own. */}
        {fromTeamConnectors.length > 0 && (
          <section>
            <h2 className="text-lg font-semibold text-surface-100 mb-4 flex items-center gap-2">
              <span className="w-2 h-2 bg-surface-500 rounded-full" />
              From your team ({fromTeamConnectors.length})
            </h2>
            <p className="text-sm text-surface-400 mb-4">
              Teammates have connected these personal integrations. Connect your own for Basebase to access your data.
            </p>
            <div className="grid gap-4">
              {fromTeamConnectors.map((integration) => renderIntegrationTile(integration, 'team-only'))}
            </div>
          </section>
        )}

        {/* Available to connect — no one in org has connected yet */}
        {availableIntegrations.length > 0 && (
          <section>
            <h2 className="text-lg font-semibold text-surface-100 mb-4 flex items-center gap-2">
              <span className="w-2 h-2 bg-surface-500 rounded-full opacity-60" />
              More connectors
            </h2>
            <div className="grid gap-4">
              {availableIntegrations.map((integration) => renderIntegrationTile(integration, 'available'))}
            </div>
          </section>
        )}

      </div>

      {/* Disconnect / error / success banners */}
      {syncError && (
        <div className="fixed bottom-4 right-4 z-50 bg-red-500/10 border border-red-500/30 text-red-400 px-4 py-3 rounded-lg text-sm max-w-sm shadow-lg">
          {syncError}
        </div>
      )}
      {disconnectError && (
        <div className="fixed bottom-4 right-4 z-50 bg-red-500/10 border border-red-500/30 text-red-400 px-4 py-3 rounded-lg text-sm max-w-sm shadow-lg">
          {disconnectError}
        </div>
      )}
      {disconnectSuccess && (
        <div className="fixed bottom-4 right-4 z-50 bg-primary-500/10 border border-primary-500/30 text-primary-400 px-4 py-3 rounded-lg text-sm max-w-sm shadow-lg">
          {disconnectSuccess}
        </div>
      )}

      {/* Disconnect Confirmation Modal */}
      {disconnectModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setDisconnectModal(null)}>
          <div className="bg-surface-900 border border-surface-700 rounded-xl shadow-xl w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="p-6">
              {disconnectModal.step === 'confirm' ? (
                <>
                  <h2 className="text-lg font-semibold text-surface-100 mb-2">Disconnect {disconnectModal.provider}?</h2>
                  <p className="text-sm text-surface-400 mb-6">
                    This will remove the connection. You can reconnect later.
                  </p>
                  <div className="flex justify-end gap-3">
                    <button
                      onClick={() => setDisconnectModal(null)}
                      className="px-4 py-2 text-sm font-medium text-surface-300 hover:text-surface-100 transition-colors"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={() => setDisconnectModal({ ...disconnectModal, step: 'ask-delete' })}
                      className="px-4 py-2 text-sm font-medium bg-red-600 hover:bg-red-500 text-white rounded-lg transition-colors"
                    >
                      Disconnect
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <h2 className="text-lg font-semibold text-surface-100 mb-2">Delete synced data?</h2>
                  <p className="text-sm text-surface-400 mb-6">
                    Do you also want to delete all data synced from {disconnectModal.provider}? This includes contacts, companies, deals, pipelines, activities, and meetings imported from this integration.
                  </p>
                  <div className="flex justify-end gap-3">
                    <button
                      onClick={() => void executeDisconnect(disconnectModal.provider, false)}
                      className="px-4 py-2 text-sm font-medium text-surface-300 hover:text-surface-100 transition-colors"
                    >
                      Keep Data
                    </button>
                    <button
                      onClick={() => void executeDisconnect(disconnectModal.provider, true)}
                      className="px-4 py-2 text-sm font-medium bg-red-600 hover:bg-red-500 text-white rounded-lg transition-colors"
                    >
                      Delete Data
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Slack Identity Verification Modal */}
      {showSlackVerificationModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setShowSlackVerificationModal(false)}>
          <div className="bg-surface-900 border border-surface-700 rounded-xl shadow-xl w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="p-6">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-surface-100">Link Slack Account</h2>
                <button
                  onClick={() => setShowSlackVerificationModal(false)}
                  className="p-1 text-surface-400 hover:text-surface-200 rounded"
                >
                  <HiX className="w-5 h-5" />
                </button>
              </div>

              <p className="text-sm text-surface-400 mb-4">
                Enter your Slack email to link your account. We&apos;ll DM you a 6-digit code to confirm.
              </p>

              {/* Email + Send Code */}
              <div className="grid gap-2 sm:grid-cols-[1fr_auto] mb-3">
                <input
                  type="email"
                  value={slackEmailInput}
                  onChange={(event) => setSlackEmailInput(event.target.value)}
                  placeholder="you@company.com"
                  className="w-full rounded-lg bg-surface-800 border border-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500 focus:border-primary-500 focus:outline-none"
                />
                <button
                  onClick={() => void handleSlackRequestCode()}
                  disabled={!slackEmailInput.trim() || slackSendCodeLoading}
                  className="px-4 py-2 text-sm font-medium text-primary-300 border border-primary-500/30 hover:bg-primary-500/10 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg transition-colors"
                >
                  {slackSendCodeLoading ? (
                    <span className="inline-flex items-center justify-center gap-2">
                      <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                      </svg>
                      Sending…
                    </span>
                  ) : (
                    'Send code'
                  )}
                </button>
              </div>

              {/* Code + Verify */}
              <div className="grid gap-2 sm:grid-cols-[1fr_auto] mb-3">
                <input
                  type="text"
                  value={slackCodeInput}
                  onChange={(event) => setSlackCodeInput(event.target.value)}
                  placeholder="Enter 6-digit code"
                  className="w-full rounded-lg bg-surface-800 border border-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500 focus:border-primary-500 focus:outline-none"
                />
                <button
                  onClick={() => void handleSlackVerifyCode()}
                  disabled={!slackEmailInput.trim() || !slackCodeInput.trim() || slackVerifyCodeLoading}
                  className="px-4 py-2 text-sm font-medium text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/10 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg transition-colors"
                >
                  {slackVerifyCodeLoading ? (
                    <span className="inline-flex items-center justify-center gap-2">
                      <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                      </svg>
                      Verifying…
                    </span>
                  ) : (
                    'Verify'
                  )}
                </button>
              </div>

              {slackMappingStatus && (
                <p className="text-xs text-surface-300 mb-2">{slackMappingStatus}</p>
              )}
              {slackMappingsError && (
                <p className="text-xs text-red-400 mb-2">{slackMappingsError}</p>
              )}

              {/* Linked accounts */}
              <div className="mt-4 pt-4 border-t border-surface-700 space-y-2">
                <div className="flex items-center justify-between">
                  <h5 className="text-xs font-semibold uppercase tracking-wide text-surface-400">
                    Linked Slack emails
                  </h5>
                  {slackMappingsLoading && (
                    <span className="text-xs text-surface-500">Loading...</span>
                  )}
                </div>
                {slackMappings.length === 0 ? (
                  <p className="text-xs text-surface-500">No linked Slack emails yet.</p>
                ) : (
                  <ul className="space-y-2">
                    {slackMappings.map((mapping) => (
                      <li
                        key={mapping.id}
                        className="flex items-center justify-between rounded-lg border border-surface-700/60 px-3 py-2 text-xs text-surface-200"
                      >
                        <div className="min-w-0">
                          <div className="truncate">{mapping.external_email ?? 'Unknown email'}</div>
                          <div className="text-[11px] text-surface-500">
                            {mapping.external_userid} · {mapping.match_source}
                          </div>
                        </div>
                        <button
                          onClick={() => void handleSlackDeleteMapping(mapping.id)}
                          className="ml-3 text-red-400 hover:text-red-300 text-xs"
                        >
                          Remove
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {/* Close button */}
              <div className="flex justify-end mt-4 pt-4 border-t border-surface-700">
                <button
                  onClick={() => setShowSlackVerificationModal(false)}
                  className="px-4 py-2 text-sm font-medium text-surface-300 hover:text-surface-100 transition-colors"
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Sharing Preferences Modal */}
      {sharingModal?.isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-surface-900 border border-surface-700 rounded-xl shadow-xl w-full max-w-md mx-4">
            <div className="p-6">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-surface-100">
                  {sharingModal.isInitialSetup
                    ? `${sharingModal.providerName} Connected`
                    : `${sharingModal.providerName} Sharing Settings`}
                </h2>
                <button
                  onClick={() => setSharingModal(null)}
                  className="p-1 text-surface-400 hover:text-surface-200 rounded"
                >
                  <HiX className="w-5 h-5" />
                </button>
              </div>

              <p className="text-sm text-surface-400 mb-6">
                {sharingModal.isInitialSetup
                  ? 'Configure how your team can access data from this connection.'
                  : 'Update sharing settings for this integration.'}
              </p>

              <div className="space-y-4">
                <label className="flex items-start gap-3 cursor-pointer group">
                  <input
                    type="checkbox"
                    checked={sharingModal.shareSyncedData}
                    onChange={(e) => setSharingModal({ ...sharingModal, shareSyncedData: e.target.checked })}
                    className="mt-1 w-4 h-4 rounded border-surface-600 bg-surface-800 text-primary-500 focus:ring-primary-500 focus:ring-offset-0"
                  />
                  <div>
                    <div className="font-medium text-surface-100 group-hover:text-white">
                      Others can read
                    </div>
                    <div className="text-xs text-surface-500 mt-0.5">
                      When on: teammates and Basebase can see synced records (emails, meetings, etc.). When off: only you can see it.
                    </div>
                  </div>
                </label>

                <label className="flex items-start gap-3 cursor-pointer group">
                  <input
                    type="checkbox"
                    checked={sharingModal.shareQueryAccess}
                    onChange={(e) => setSharingModal({ ...sharingModal, shareQueryAccess: e.target.checked })}
                    className="mt-1 w-4 h-4 rounded border-surface-600 bg-surface-800 text-primary-500 focus:ring-primary-500 focus:ring-offset-0"
                  />
                  <div>
                    <div className="font-medium text-surface-100 group-hover:text-white">
                      Allow team to query live data
                    </div>
                    <div className="text-xs text-surface-500 mt-0.5">
                      Team can run queries using your connection (not recommended for personal data)
                    </div>
                  </div>
                </label>

                <label className="flex items-start gap-3 cursor-pointer group">
                  <input
                    type="checkbox"
                    checked={sharingModal.shareWriteAccess}
                    onChange={(e) => setSharingModal({ ...sharingModal, shareWriteAccess: e.target.checked })}
                    className="mt-1 w-4 h-4 rounded border-surface-600 bg-surface-800 text-primary-500 focus:ring-primary-500 focus:ring-offset-0"
                  />
                  <div>
                    <div className="font-medium text-surface-100 group-hover:text-white">
                      Allow team to write data
                    </div>
                    <div className="text-xs text-surface-500 mt-0.5">
                      Team can create/update records as you (rarely needed)
                    </div>
                  </div>
                </label>
              </div>

              {sharingError && (
                <p className="text-sm text-red-400 mt-4">{sharingError}</p>
              )}

              <div className="flex justify-end gap-3 mt-6 pt-4 border-t border-surface-700">
                <button
                  onClick={() => setSharingModal(null)}
                  className="px-4 py-2 text-sm font-medium text-surface-300 hover:text-surface-100 transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={() => void handleSaveSharing()}
                  disabled={sharingSaving}
                  className="px-4 py-2 text-sm font-medium bg-primary-600 hover:bg-primary-500 text-white rounded-lg disabled:opacity-50 transition-colors flex items-center gap-2"
                >
                  {sharingSaving && (
                    <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  )}
                  {sharingModal.isInitialSetup ? 'Save & Start Sync' : 'Save Changes'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
