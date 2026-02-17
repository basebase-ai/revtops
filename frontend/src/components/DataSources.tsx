/**
 * Data Sources management screen.
 * 
 * Features:
 * - View all connected data sources
 * - View available data sources to connect
 * - Sync status and manual sync trigger
 * - Disconnect integrations
 * 
 * Uses React Query for server state (integrations list).
 */

import { useState, useEffect, useCallback } from 'react';
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
} from 'react-icons/si';
import { HiOutlineCalendar, HiOutlineMail, HiGlobeAlt, HiUserGroup, HiExclamation, HiDeviceMobile, HiMicrophone, HiLightningBolt, HiX } from 'react-icons/hi';
// Custom Apollo.io icon - 8-ray starburst matching their brand
const ApolloIcon: IconType = ({ className, ...props }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className={className} {...props}>
    <line x1="12" y1="2" x2="12" y2="22" />
    <line x1="2" y1="12" x2="22" y2="12" />
    <line x1="4.93" y1="4.93" x2="19.07" y2="19.07" />
    <line x1="19.07" y1="4.93" x2="4.93" y2="19.07" />
  </svg>
);
import { API_BASE } from '../lib/api';
import { useAppStore, useIntegrations, useIntegrationsLoading, type Integration, type SyncStats } from '../store';
import { useWebSocket } from '../hooks/useWebSocket';

// Detect if user is on a mobile device
function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(false);
  
  useEffect(() => {
    const checkMobile = (): void => {
      const userAgent = navigator.userAgent || navigator.vendor;
      const mobileRegex = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i;
      const isMobileDevice = mobileRegex.test(userAgent);
      const isSmallScreen = window.innerWidth < 768;
      setIsMobile(isMobileDevice || isSmallScreen);
    };
    
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);
  
  return isMobile;
}

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
};

// User-scoped providers (each user connects individually vs org-wide connection)
const USER_SCOPED_PROVIDERS = new Set([
  'gmail',
  'google_calendar',
  'microsoft_calendar',
  'microsoft_mail',
  'zoom',
  'fireflies',
  'google_drive',
]);

// Integration display config (colors, icons, descriptions)
const INTEGRATION_CONFIG: Record<string, { name: string; description: string; icon: string; color: string }> = {
  hubspot: { name: 'HubSpot', description: 'CRM data including deals, contacts, and companies', icon: 'hubspot', color: 'from-orange-500 to-orange-600' },
  salesforce: { name: 'Salesforce', description: 'CRM - Opportunities, Accounts', icon: 'salesforce', color: 'from-blue-500 to-blue-600' },
  slack: { name: 'Slack', description: 'Team messages and communication history', icon: 'slack', color: 'from-purple-500 to-purple-600' },
  zoom: { name: 'Zoom', description: 'Meeting transcripts and cloud recording insights', icon: 'zoom', color: 'from-blue-400 to-blue-500' },
  google_calendar: { name: 'Google Calendar', description: 'Meetings, events, and scheduling data', icon: 'google_calendar', color: 'from-green-500 to-green-600' },
  gmail: { name: 'Gmail', description: 'Google email communications', icon: 'gmail', color: 'from-red-500 to-red-600' },
  microsoft_calendar: { name: 'Microsoft Calendar', description: 'Outlook calendar events and meetings', icon: 'microsoft_calendar', color: 'from-sky-500 to-sky-600' },
  microsoft_mail: { name: 'Microsoft Mail', description: 'Outlook emails and communications', icon: 'microsoft_mail', color: 'from-sky-500 to-sky-600' },
  fireflies: { name: 'Fireflies', description: 'Meeting transcriptions and notes', icon: 'fireflies', color: 'from-violet-500 to-violet-600' },
  google_drive: { name: 'Google Drive', description: 'Sync files — search and read Docs, Sheets, Slides from Drive', icon: 'google_drive', color: 'from-yellow-500 to-amber-500' },
  apollo: { name: 'Apollo.io', description: 'Data enrichment - Contact titles, companies, emails', icon: 'apollo', color: 'from-yellow-400 to-yellow-500' },
  github: { name: 'GitHub', description: 'Track repos, commits, and pull requests by team', icon: 'github', color: 'from-gray-600 to-gray-700' },
  linear: { name: 'Linear', description: 'Issue tracking - sync and manage teams, projects, and issues', icon: 'linear', color: 'from-indigo-500 to-violet-600' },
};

const SUPPORTED_PROVIDERS = new Set(Object.keys(INTEGRATION_CONFIG));

// Extended integration type with display info
interface DisplayIntegration extends Integration {
  name: string;
  description: string;
  icon: string;
  color: string;
  connected: boolean;
}

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

  // Activity-based connectors (email, calendar, meetings)
  if (stats.activities !== undefined) {
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

export function DataSources(): JSX.Element {
  // Get user/org from Zustand (auth state)
  const { user, organization } = useAppStore();
  
  // Check if on mobile device
  const isMobile = useIsMobile();

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

  const [syncingProviders, setSyncingProviders] = useState<Set<string>>(new Set());
  const [disconnectingProviders, setDisconnectingProviders] = useState<Set<string>>(new Set());
  const [connectingProvider, setConnectingProvider] = useState<string | null>(null);
  const [slackMappings, setSlackMappings] = useState<SlackUserMapping[]>([]);
  const [slackMappingsLoading, setSlackMappingsLoading] = useState(false);
  const [slackMappingsError, setSlackMappingsError] = useState<string | null>(null);
  const [slackEmailInput, setSlackEmailInput] = useState('');
  const [slackCodeInput, setSlackCodeInput] = useState('');
  const [slackMappingStatus, setSlackMappingStatus] = useState<string | null>(null);
  const [slackShowAddForm, setSlackShowAddForm] = useState(false);
  const [showConnectModal, setShowConnectModal] = useState(false);
  const [connectSearch, setConnectSearch] = useState('');
  const [pennyBotCtaDismissed, setPennyBotCtaDismissed] = useState<boolean>(
    () => localStorage.getItem('penny_bot_cta_dismissed') === '1',
  );

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

  const organizationId = organization?.id ?? '';
  const userId = user?.id ?? '';

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
        
        // If sync completed, refresh integrations to get final data
        if (data.status === 'completed') {
          void fetchIntegrations();
          // Clear the progress for this provider after a short delay
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
  useWebSocket(userId ? '/ws/chat' : '', {
    onMessage: handleWsMessage,
  });

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
      console.log('[DataSources] Loaded Slack mappings from user_mappings_for_identity:', mappingsFromIdentityTable.length);
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
      if (!SUPPORTED_PROVIDERS.has(integration.provider)) {
        console.warn('[DataSources] Hiding unsupported integration provider from UI:', integration.provider);
        return false;
      }
      return true;
    })
    .map((integration) => {
      const config = INTEGRATION_CONFIG[integration.provider]!;
      return {
        ...integration,
        ...config,
        connected: integration.isActive,
      };
    });

  // Also include available (not connected) integrations
  const connectedProviders = new Set(integrations.map((i) => i.provider));
  const availableProviders = Object.keys(INTEGRATION_CONFIG).filter((p) => !connectedProviders.has(p));
  const availableIntegrationsDisplay: DisplayIntegration[] = availableProviders
    .filter((provider) => INTEGRATION_CONFIG[provider] !== undefined)
    .map((provider) => {
      const config = INTEGRATION_CONFIG[provider]!;
      const scope = USER_SCOPED_PROVIDERS.has(provider) ? 'user' as const : 'organization' as const;
      return {
        id: provider,
        provider,
        scope,
        isActive: false,
        lastSyncAt: null,
        lastError: null,
        connectedAt: null,
        connectedBy: null,
        currentUserConnected: false,
        teamConnections: [],
        teamTotal: 0,
        syncStats: null,
        name: config.name,
        description: config.description,
        icon: config.icon,
        color: config.color,
        connected: false,
      };
    });
  const allIntegrations: DisplayIntegration[] = [...integrations, ...availableIntegrationsDisplay];

  const handleConnect = async (provider: string, scope: 'organization' | 'user'): Promise<void> => {
    if (connectingProvider || !organizationId) return;
    // User-scoped integrations require user_id
    if (scope === 'user' && !userId) return;
    
    setConnectingProvider(provider);

    try {
      // Get session token from backend
      // For user-scoped integrations, include user_id
      const params = new URLSearchParams({ organization_id: organizationId });
      if (scope === 'user' && userId) {
        params.set('user_id', userId);
      }
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
          console.log('Nango event:', event);
          
          // Handle different possible event types from Nango
          const eventType = event.type as string;
          if (
            eventType === 'connect' ||
            eventType === 'connection-created' ||
            eventType === 'success'
          ) {
            // Connection successful - confirm and create integration record
            // Extract the actual Nango connection_id from the event
            // The event payload contains the real connection_id that Nango created
            const eventData = event as { type: string; connectionId?: string; connection_id?: string; payload?: { connectionId?: string } };
            const nangoConnectionId = eventData.connectionId || eventData.connection_id || eventData.payload?.connectionId || connection_id;
            
            console.log('Connection successful, confirming integration with connectionId:', nangoConnectionId);
            try {
              const confirmResponse = await fetch(`${API_BASE}/auth/integrations/confirm`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  provider,
                  connection_id: nangoConnectionId,  // Use the actual Nango connection_id
                  organization_id: organizationId,
                  user_id: scope === 'user' ? userId : undefined,
                }),
              });
              
              if (!confirmResponse.ok) {
                console.error('Failed to confirm integration:', await confirmResponse.text());
              } else {
                console.log('Integration confirmed successfully');
              }
            } catch (confirmError) {
              console.error('Error confirming integration:', confirmError);
            }
            
            // Invalidate cache to refetch integrations
            void fetchIntegrations();
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
  };

  const handleDisconnect = async (provider: string): Promise<void> => {
    if (!organizationId || disconnectingProviders.has(provider)) return;
    
    // Derive scope from provider (source of truth) rather than trusting passed value
    const isUserScoped = USER_SCOPED_PROVIDERS.has(provider);
    
    // User-scoped integrations require user_id
    if (isUserScoped && !userId) {
      alert('Unable to disconnect: user session not found. Please refresh the page.');
      return;
    }
    
    if (!confirm(`Are you sure you want to disconnect ${provider}?`)) return;
    
    // Ask if user wants to delete all synced data
    const deleteData = confirm(
      `Do you also want to delete all data synced from ${provider}?\n\n` +
      `This includes contacts, companies, deals, pipelines, activities, and meetings imported from this integration.\n\n` +
      `Click OK to delete data, or Cancel to keep the data.`
    );

    // Set disconnecting state immediately for instant UI feedback
    setDisconnectingProviders((prev) => new Set(prev).add(provider));

    const params = new URLSearchParams({ organization_id: organizationId });
    if (isUserScoped && userId) {
      params.set('user_id', userId);
    }
    if (deleteData) {
      params.set('delete_data', 'true');
    }
    const url = `${API_BASE}/auth/integrations/${provider}?${params.toString()}`;
    console.log('Disconnecting:', { provider, organizationId, userId, url });

    try {
      const response = await fetch(url, { method: 'DELETE' });
      
      console.log('Disconnect response:', {
        status: response.status,
        statusText: response.statusText,
        ok: response.ok,
      });

      const responseText = await response.text();
      console.log('Disconnect response body:', responseText);

      if (!response.ok) {
        throw new Error(responseText);
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
          alert(`Disconnected ${provider}.\n\nDeleted ${counts.join(', ')}.`);
        }
      } catch {
        // Response wasn't JSON or didn't have deletion info, that's fine
      }

      console.log('Disconnect successful, invalidating integrations cache...');
      // Invalidate cache to refetch integrations, keep UI in disconnecting state until refreshed
      try {
        await fetchIntegrations();
        console.log('Integrations refreshed after disconnect for provider:', provider);
      } catch (fetchError) {
        console.error('Failed to refresh integrations after disconnect:', fetchError);
      }
      console.log('Disconnect complete, restoring UI state for provider:', provider);
      setDisconnectingProviders((prev) => {
        if (!prev.has(provider)) return prev;
        const next = new Set(prev);
        next.delete(provider);
        return next;
      });
    } catch (error) {
      console.error('Failed to disconnect:', error);
      alert(`Failed to disconnect: ${error instanceof Error ? error.message : 'Unknown error'}`);
      // Remove from disconnecting state on error so user can retry
      setDisconnectingProviders((prev) => {
        const next = new Set(prev);
        next.delete(provider);
        return next;
      });
    }
  };

  const handleSync = async (provider: string): Promise<void> => {
    if (syncingProviders.has(provider) || !organizationId) return;

    setSyncingProviders((prev) => new Set(prev).add(provider));

    try {
      // Google Drive uses its own sync endpoint (user-scoped)
      if (provider === 'google_drive') {
        const params = new URLSearchParams({ organization_id: organizationId, user_id: userId });
        const response = await fetch(`${API_BASE}/drive/sync?${params.toString()}`, { method: 'POST' });
        if (!response.ok) throw new Error('Drive sync failed');
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

      const response = await fetch(`${API_BASE}/sync/${organizationId}/${provider}`, {
        method: 'POST',
      });

      if (!response.ok) throw new Error('Sync failed');

      // Poll for completion
      let attempts = 0;
      const checkStatus = async (): Promise<void> => {
        const statusRes = await fetch(`${API_BASE}/sync/${organizationId}/${provider}/status`);
        const status = await statusRes.json();

        if (status.status === 'completed' || status.status === 'failed' || attempts >= 30) {
          setSyncingProviders((prev) => {
            const next = new Set(prev);
            next.delete(provider);
            return next;
          });

          // Invalidate cache to get updated sync status
          if (status.status === 'completed' || status.status === 'failed') {
            void fetchIntegrations();
          }
        } else {
          attempts++;
          setTimeout(() => void checkStatus(), 1000);
        }
      };

      void checkStatus();
    } catch (error) {
      console.error('Sync error:', error);
      setSyncingProviders((prev) => {
        const next = new Set(prev);
        next.delete(provider);
        return next;
      });
    }
  };

  const handleSlackRequestCode = async (): Promise<void> => {
    if (!organizationId || !userId || !slackEmailInput.trim()) return;
    setSlackMappingStatus(null);
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
    }
  };

  const handleSlackVerifyCode = async (): Promise<void> => {
    if (!organizationId || !userId || !slackEmailInput.trim() || !slackCodeInput.trim()) return;
    setSlackMappingStatus(null);
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
      setSlackShowAddForm(false);
      void fetchSlackMappings();
    } catch (error) {
      console.error('[DataSources] Failed to verify Slack code:', error);
      setSlackMappingStatus(
        error instanceof Error ? error.message : 'Failed to verify code.',
      );
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

  // Separate integrations into three categories:
  // 1. Action Required: user-scoped where team has connected but current user hasn't
  // 2. Connected: org-scoped, or user-scoped where current user has connected
  // 3. Available: not connected by anyone
  const actionRequiredIntegrations = allIntegrations.filter(
    (i) => i.scope === 'user' && i.connected && !i.currentUserConnected
  );
  const connectedIntegrations = allIntegrations.filter(
    (i) => i.connected && (i.scope === 'organization' || i.currentUserConnected)
  );
  const availableIntegrations = allIntegrations.filter((i) => !i.connected);

  // Icon renderer based on icon identifier
  const renderIcon = (iconId: string): JSX.Element => {
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
    };
    return colorMap[color] ?? 'bg-surface-600';
  };

  // Tile state type for unified rendering
  type TileState = 'connected' | 'action-required' | 'available';

  // Unified integration tile component
  const renderIntegrationTile = (
    integration: DisplayIntegration,
    state: TileState
  ): JSX.Element => {
    const isConnecting = connectingProvider === integration.provider;
    const isSyncing = syncingProviders.has(integration.provider);
    const isDisconnecting = disconnectingProviders.has(integration.provider);

    // State-specific styling - fade card when disconnecting
    const cardClass = state === 'action-required'
      ? 'card p-4 border-amber-500/30 bg-amber-500/5'
      : isDisconnecting
        ? 'card p-4 opacity-50 pointer-events-none transition-opacity duration-200'
        : 'card p-4';

    const iconOpacity = state === 'available' ? 'opacity-60' : '';

    // Badge config by state
    const badgeConfig: Record<TileState, { text: string; className: string } | null> = {
      'connected': { text: 'Connected', className: 'bg-emerald-500/20 text-emerald-400' },
      'action-required': { text: 'Your account not connected', className: 'bg-amber-500/20 text-amber-400' },
      'available': null,
    };
    const badge = badgeConfig[state];

    // Button config by state
    const getButtonConfig = (): { text: string; className: string; action: () => void; disabled: boolean; hidden?: boolean } => {
      if (state === 'connected') {
        // Apollo.io is on-demand enrichment - no regular sync
        if (integration.provider === 'apollo') {
          return {
            text: '',
            className: '',
            action: () => {},
            disabled: true,
            hidden: true,
          };
        }
        return {
          text: isSyncing ? 'Syncing...' : 'Sync',
          className: 'px-4 py-2 text-sm font-medium text-surface-200 bg-surface-800 hover:bg-surface-700 disabled:opacity-50 rounded-lg transition-colors',
          action: () => void handleSync(integration.provider),
          disabled: isSyncing,
        };
      }
      if (state === 'action-required') {
        return {
          text: isMobile ? 'Use desktop to connect' : (isConnecting ? 'Connecting...' : `Connect Your ${integration.name}`),
          className: isMobile 
            ? 'px-4 py-2 text-sm font-medium text-surface-500 border border-surface-700 rounded-lg cursor-not-allowed'
            : 'px-4 py-2 text-sm font-medium text-amber-400 border border-amber-500/30 hover:bg-amber-500/10 disabled:opacity-50 rounded-lg transition-colors',
          action: () => { if (!isMobile) void handleConnect(integration.provider, integration.scope); },
          disabled: isMobile || isConnecting,
        };
      }
      return {
        text: isMobile ? 'Use desktop to connect' : (isConnecting ? 'Connecting...' : (integration.scope === 'user' ? 'Connect your account' : 'Connect')),
        className: isMobile
          ? 'px-4 py-2 text-sm font-medium text-surface-500 border border-surface-700 rounded-lg cursor-not-allowed'
          : 'px-4 py-2 text-sm font-medium text-primary-400 border border-primary-500/30 hover:bg-primary-500/10 disabled:opacity-50 rounded-lg transition-colors',
        action: () => { if (!isMobile) void handleConnect(integration.provider, integration.scope); },
        disabled: isMobile || isConnecting,
      };
    };
    const buttonConfig = getButtonConfig();

    // Team connections info for user-scoped integrations
    const renderTeamInfo = (): JSX.Element | null => {
      if (integration.scope !== 'user' || integration.teamTotal === 0) return null;

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

    const renderPennyBotCta = (): JSX.Element | null => {
      if (integration.provider !== 'slack' || state !== 'connected' || pennyBotCtaDismissed) return null;

      const handleDismiss = (): void => {
        setPennyBotCtaDismissed(true);
        localStorage.setItem('penny_bot_cta_dismissed', '1');
      };

      return (
        <div className="mt-4 pt-4 border-t border-surface-700/50">
          <div className="relative rounded-lg border border-purple-500/20 bg-purple-500/5 p-3.5">
            <button
              type="button"
              onClick={handleDismiss}
              className="absolute top-2 right-2 text-surface-500 hover:text-surface-300 transition-colors"
              aria-label="Dismiss"
            >
              <HiX className="w-4 h-4" />
            </button>
            <div className="flex items-start gap-3 pr-4">
              <div className="mt-0.5 flex-shrink-0 rounded-lg bg-purple-500/15 p-1.5">
                <HiLightningBolt className="w-4 h-4 text-purple-400" />
              </div>
              <div className="space-y-1.5">
                <h4 className="text-sm font-semibold text-surface-100">
                  Add the Penny bot to Slack
                </h4>
                <p className="text-xs leading-relaxed text-surface-400">
                  This integration syncs your Slack messages so Penny can search and reference them.
                  To <span className="text-surface-300">DM Penny</span> or <span className="text-surface-300">@mention</span> her
                  directly in channels, ask your workspace admin to install the
                  {' '}<span className="font-medium text-purple-300">Penny</span> bot app from the Slack App Directory.
                </p>
                <a
                  href="https://slack.com/apps"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs font-medium text-purple-400 hover:text-purple-300 transition-colors"
                >
                  Browse Slack App Directory
                  <span aria-hidden="true">&rarr;</span>
                </a>
              </div>
            </div>
          </div>
        </div>
      );
    };

    const renderSlackMapping = (): JSX.Element | null => {
      if (integration.provider !== 'slack' || state !== 'connected') return null;

      const hasExistingMappings: boolean = slackMappings.length > 0;
      const showForm: boolean = !hasExistingMappings || slackShowAddForm;

      return (
        <div className="mt-4 pt-4 border-t border-surface-700/50 space-y-3">
          {showForm && (
            <>
              <div>
                <h4 className="text-sm font-semibold text-surface-100">
                  Connect your Slack email (it&apos;s on your profile in Slack!)
                </h4>
                <p className="text-xs text-surface-400 mt-1">
                  Add your Slack email to link your RevTops account. We&apos;ll DM a 6-digit code to confirm.
                </p>
              </div>

              <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
                <input
                  type="email"
                  value={slackEmailInput}
                  onChange={(event) => setSlackEmailInput(event.target.value)}
                  placeholder="you@company.com"
                  className="w-full rounded-lg bg-surface-900 border border-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500 focus:border-primary-500 focus:outline-none"
                />
                <button
                  onClick={() => void handleSlackRequestCode()}
                  disabled={!slackEmailInput.trim()}
                  className="px-4 py-2 text-sm font-medium text-primary-300 border border-primary-500/30 hover:bg-primary-500/10 disabled:opacity-50 rounded-lg transition-colors"
                >
                  Send code
                </button>
              </div>

              <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
                <input
                  type="text"
                  value={slackCodeInput}
                  onChange={(event) => setSlackCodeInput(event.target.value)}
                  placeholder="Enter 6-digit code"
                  className="w-full rounded-lg bg-surface-900 border border-surface-700 px-3 py-2 text-sm text-surface-100 placeholder:text-surface-500 focus:border-primary-500 focus:outline-none"
                />
                <button
                  onClick={() => void handleSlackVerifyCode()}
                  disabled={!slackEmailInput.trim() || !slackCodeInput.trim()}
                  className="px-4 py-2 text-sm font-medium text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/10 disabled:opacity-50 rounded-lg transition-colors"
                >
                  Verify
                </button>
              </div>

              {slackMappingStatus && (
                <p className="text-xs text-surface-300">{slackMappingStatus}</p>
              )}
              {slackMappingsError && (
                <p className="text-xs text-red-400">{slackMappingsError}</p>
              )}
            </>
          )}

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <h5 className="text-xs font-semibold uppercase tracking-wide text-surface-400">
                Linked Slack emails
              </h5>
              {slackMappingsLoading && (
                <span className="text-xs text-surface-500">Loading...</span>
              )}
            </div>
            {slackMappings.length === 0 && !showForm ? (
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
            {hasExistingMappings && !slackShowAddForm && (
              <button
                onClick={() => setSlackShowAddForm(true)}
                className="text-xs text-primary-400 hover:text-primary-300 transition-colors"
              >
                + Add another Slack email
              </button>
            )}
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
                  : 'Select which repositories to sync. Tracking for this organization.'}
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
            <div className={`${getColorClass(integration.color)} p-2.5 sm:p-3 rounded-xl text-white ${iconOpacity} relative flex-shrink-0`}>
              {renderIcon(integration.icon)}
              {state === 'action-required' && (
                <div className="absolute -top-1 -right-1 w-5 h-5 bg-amber-500 rounded-full flex items-center justify-center">
                  <HiExclamation className="w-3 h-3 text-white" />
                </div>
              )}
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
              </div>
              <p className="text-sm text-surface-400 mt-0.5 hidden sm:block">{integration.description}</p>
              {state === 'connected' && integration.lastSyncAt && (
                <p className="text-xs text-surface-500 mt-1 hidden sm:block">
                  Last synced: {new Date(integration.lastSyncAt).toLocaleString()}
                </p>
              )}
              {state === 'connected' && (syncProgress[integration.provider] !== undefined || integration.syncStats) && (
                <p className="text-xs text-surface-400 mt-1 hidden sm:block">
                  {syncProgress[integration.provider] !== undefined ? (
                    <span className="text-primary-400">
                      Syncing{syncStep[integration.provider] ? ` ${syncStep[integration.provider]}` : ''}... {getActivityLabel(integration.provider, syncProgress[integration.provider] ?? 0, syncStep[integration.provider])}
                    </span>
                  ) : integration.syncStats ? (
                    formatSyncStats(integration.syncStats, integration.provider)
                  ) : null}
                </p>
              )}
              {state === 'connected' && integration.lastError && (
                <p className="text-xs text-red-400 mt-1">Error: {integration.lastError}</p>
              )}
              {state === 'action-required' && (
                <p className="text-xs text-amber-400 mt-1 hidden sm:block">
                  Connect yours to include your data in team insights.
                </p>
              )}
            </div>
          </div>

          {/* Actions - full width on mobile */}
          <div className="flex items-center gap-2 sm:flex-shrink-0">
            {!buttonConfig.hidden && (
              <button
                onClick={buttonConfig.action}
                disabled={buttonConfig.disabled}
                className={`${buttonConfig.className} flex items-center justify-center gap-2 flex-1 sm:flex-initial`}
              >
                {(isConnecting || isSyncing) && !isMobile && (
                  <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                )}
                {buttonConfig.text}
              </button>
            )}
            {state === 'connected' && (
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
        {renderPennyBotCta()}
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

  // Filtered available sources for the connect modal
  const filteredAvailableIntegrations: DisplayIntegration[] = availableIntegrations.filter((i) => {
    if (!connectSearch.trim()) return true;
    const query: string = connectSearch.toLowerCase();
    return (
      i.name.toLowerCase().includes(query) ||
      i.description.toLowerCase().includes(query) ||
      i.provider.toLowerCase().includes(query)
    );
  });

  return (
    <div className="flex-1 overflow-y-auto overflow-x-hidden">
      {/* Header - hidden on mobile since AppLayout has mobile header */}
      <header className="hidden md:block sticky top-0 z-20 bg-surface-950 border-b border-surface-800 px-4 md:px-8 py-4 md:py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl md:text-2xl font-bold text-surface-50">Data Sources</h1>
            <p className="text-surface-400 mt-1 text-sm md:text-base">
              Connect your sales tools to unlock AI-powered insights
            </p>
          </div>
          <button
            onClick={() => { setShowConnectModal(true); setConnectSearch(''); }}
            className="px-5 py-2.5 text-sm font-semibold text-white bg-primary-600 hover:bg-primary-500 rounded-lg transition-colors flex items-center gap-2 shadow-lg shadow-primary-600/20"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
            </svg>
            Add Source
          </button>
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
              {filteredAvailableIntegrations.length === 0 ? (
                <li className="px-4 py-8 text-center text-sm text-surface-500">
                  {availableIntegrations.length === 0
                    ? 'All sources are already connected!'
                    : 'No sources match your search.'}
                </li>
              ) : (
                filteredAvailableIntegrations.map((integration) => {
                  const isConnecting: boolean = connectingProvider === integration.provider;
                  return (
                    <li key={integration.provider}>
                      <button
                        onClick={() => {
                          if (!isMobile) {
                            setShowConnectModal(false);
                            void handleConnect(integration.provider, integration.scope);
                          }
                        }}
                        disabled={isMobile || isConnecting}
                        className="w-full flex items-center gap-4 px-4 py-3 rounded-xl hover:bg-surface-800 transition-colors text-left group disabled:opacity-50"
                      >
                        <div className={`${getColorClass(integration.color)} p-2 rounded-lg text-white flex-shrink-0`}>
                          {renderIcon(integration.icon)}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="font-medium text-surface-100 group-hover:text-white transition-colors">
                            {integration.name}
                          </div>
                          <div className="text-xs text-surface-500 truncate mt-0.5">
                            {integration.description}
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

      <div className="max-w-4xl mx-auto px-4 md:px-8 py-4 md:py-8 space-y-6 md:space-y-10">
        {/* Mobile: Connect Source button (since header is hidden) */}
        {isMobile && (
          <button
            onClick={() => { setShowConnectModal(true); setConnectSearch(''); }}
            className="w-full px-5 py-3 text-sm font-semibold text-white bg-primary-600 hover:bg-primary-500 rounded-lg transition-colors flex items-center justify-center gap-2 shadow-lg shadow-primary-600/20"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
            </svg>
            Connect Source
          </button>
        )}

        {/* Mobile notice banner */}
        {isMobile && (
          <div className="bg-surface-800/50 border border-surface-700 rounded-xl p-4 flex items-start gap-3">
            <div className="flex-shrink-0 w-10 h-10 rounded-lg bg-primary-500/20 flex items-center justify-center">
              <HiDeviceMobile className="w-5 h-5 text-primary-400" />
            </div>
            <div>
              <h3 className="font-medium text-surface-100">Connect from your computer</h3>
              <p className="text-sm text-surface-400 mt-1">
                For the best experience connecting data sources, please visit this page from a desktop or laptop computer. 
                OAuth sign-in works more reliably on larger screens.
              </p>
            </div>
          </div>
        )}
        {/* Action Required - User-scoped integrations where current user hasn't connected */}
        {actionRequiredIntegrations.length > 0 && (
          <section>
            <h2 className="text-lg font-semibold text-surface-100 mb-4 flex items-center gap-2">
              <span className="w-2 h-2 bg-amber-500 rounded-full animate-pulse" />
              <span className="text-amber-400">Action Required ({actionRequiredIntegrations.length})</span>
            </h2>
            <div className="grid gap-4">
              {actionRequiredIntegrations.map((integration) => renderIntegrationTile(integration, 'action-required'))}
            </div>
          </section>
        )}

        {/* Connected Sources */}
        <section>
          <h2 className="text-lg font-semibold text-surface-100 mb-4 flex items-center gap-2">
            <span className="w-2 h-2 bg-emerald-500 rounded-full" />
            Connected ({connectedIntegrations.length})
          </h2>

          {connectedIntegrations.length === 0 ? (
            <div className="card text-center py-12">
              <div className="w-16 h-16 rounded-full bg-surface-800 flex items-center justify-center mx-auto mb-4">
                <svg className="w-8 h-8 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
              </div>
              <h3 className="text-surface-200 font-medium mb-2">No data sources connected</h3>
              <p className="text-surface-400 text-sm">
                Connect your first data source to get started
              </p>
            </div>
          ) : (
            <div className="grid gap-4">
              {connectedIntegrations.map((integration) => renderIntegrationTile(integration, 'connected'))}
            </div>
          )}
        </section>

      </div>

    </div>
  );
}
