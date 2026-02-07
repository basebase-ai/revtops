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
  SiGooglesheets,
} from 'react-icons/si';
import { HiOutlineCalendar, HiOutlineMail, HiGlobeAlt, HiUserGroup, HiExclamation, HiDeviceMobile, HiMicrophone, HiUpload } from 'react-icons/hi';
// Custom Apollo.io icon - 8-ray starburst matching their brand
const ApolloIcon: IconType = ({ className, ...props }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className={className} {...props}>
    <line x1="12" y1="2" x2="12" y2="22" />
    <line x1="2" y1="12" x2="22" y2="12" />
    <line x1="4.93" y1="4.93" x2="19.07" y2="19.07" />
    <line x1="19.07" y1="4.93" x2="4.93" y2="19.07" />
  </svg>
);
import { SheetImporter } from './SheetImporter';
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
  google_sheets: SiGooglesheets,
  apollo: ApolloIcon,
};

// User-scoped providers (each user connects individually vs org-wide connection)
const USER_SCOPED_PROVIDERS = new Set([
  'gmail',
  'google_calendar',
  'microsoft_calendar',
  'microsoft_mail',
  'zoom',
  'fireflies',
  'google_sheets',
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
  google_sheets: { name: 'Google Sheets', description: 'Import contacts, accounts, deals from spreadsheets', icon: 'google_sheets', color: 'from-emerald-500 to-emerald-600' },
  apollo: { name: 'Apollo.io', description: 'Data enrichment - Contact titles, companies, emails', icon: 'apollo', color: 'from-yellow-400 to-yellow-500' },
};

// Extended integration type with display info
interface DisplayIntegration extends Integration {
  name: string;
  description: string;
  icon: string;
  color: string;
  connected: boolean;
}

/**
 * Format sync stats into a human-readable summary string.
 * Shows counts for different object types synced.
 * Always shows stats for CRM providers (even zeros) for trust/debugging.
 */
function formatSyncStats(stats: SyncStats | null, provider: string): string | null {
  if (!stats) return null;

  const parts: string[] = [];

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

  // Activity-based connectors (email, calendar, meetings)
  if (stats.activities !== undefined) {
    const activityLabel = getActivityLabel(provider, stats.activities);
    parts.push(activityLabel);
  }

  if (parts.length === 0) return null;

  return parts.join(', ');
}

/**
 * Get a provider-specific label for activities count.
 */
function getActivityLabel(provider: string, count: number): string {
  const formatted = count.toLocaleString();
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
      return `${formatted} records`;
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
  const [showSheetImporter, setShowSheetImporter] = useState(false);
  
  // Live sync progress from WebSocket
  const [syncProgress, setSyncProgress] = useState<Record<string, number>>({});

  const organizationId = organization?.id ?? '';
  const userId = user?.id ?? '';
  
  // Handle WebSocket messages for sync progress
  const handleWsMessage = useCallback((message: string) => {
    try {
      const data = JSON.parse(message) as { type: string; provider?: string; count?: number; status?: string };
      if (data.type === 'sync_progress' && data.provider !== undefined && data.count !== undefined) {
        setSyncProgress((prev) => ({
          ...prev,
          [data.provider as string]: data.count as number,
        }));
        
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

  // Transform raw integrations to display integrations with UI metadata
  // Filter out raw "microsoft" integration - it's a meta-integration from Nango's OAuth.
  // The actual data sources are microsoft_calendar and microsoft_mail.
  const integrations: DisplayIntegration[] = rawIntegrations
    .filter((integration) => integration.provider !== 'microsoft')
    .map((integration) => {
      const config = INTEGRATION_CONFIG[integration.provider] ?? {
        name: integration.provider,
        description: 'Data source',
        icon: integration.provider,
        color: 'from-surface-500 to-surface-600',
      };
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
      `This includes activities, meetings, and other records imported from this integration.\n\n` +
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
          deleted_meetings?: number 
        };
        if (data.deleted_activities !== undefined || data.deleted_meetings !== undefined) {
          const activities = data.deleted_activities ?? 0;
          const meetings = data.deleted_meetings ?? 0;
          alert(`Disconnected ${provider}.\n\nDeleted ${activities} activities and ${meetings} orphaned meetings.`);
        }
      } catch {
        // Response wasn't JSON or didn't have deletion info, that's fine
      }

      console.log('Disconnect successful, invalidating integrations cache...');
      // Invalidate cache to refetch integrations
      void fetchIntegrations();
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
      'from-emerald-500 to-emerald-600': 'bg-emerald-500',
      'from-yellow-400 to-yellow-500': 'bg-yellow-400',
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
        // Google Sheets uses on-demand import, Apollo.io is on-demand enrichment - no regular sync
        if (integration.provider === 'google_sheets' || integration.provider === 'apollo') {
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
                      Syncing... {getActivityLabel(integration.provider, syncProgress[integration.provider] ?? 0)}
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
            {state === 'connected' && integration.provider === 'google_sheets' && (
              <button
                onClick={() => setShowSheetImporter(true)}
                className="px-3 sm:px-4 py-2 text-sm font-medium text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/10 rounded-lg transition-colors flex items-center gap-2"
              >
                <HiUpload className="w-4 h-4" />
                Import
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

  return (
    <div className="flex-1 overflow-y-auto overflow-x-hidden">
      {/* Header - hidden on mobile since AppLayout has mobile header */}
      <header className="hidden md:block sticky top-0 z-20 bg-surface-950 border-b border-surface-800 px-4 md:px-8 py-4 md:py-6">
        <h1 className="text-xl md:text-2xl font-bold text-surface-50">Data Sources</h1>
        <p className="text-surface-400 mt-1 text-sm md:text-base">
          Connect your sales tools to unlock AI-powered insights
        </p>
      </header>

      <div className="max-w-4xl mx-auto px-4 md:px-8 py-4 md:py-8 space-y-6 md:space-y-10">
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

        {/* Available Sources */}
        <section>
          <h2 className="text-lg font-semibold text-surface-100 mb-4">
            Available Sources
          </h2>
          <div className="grid gap-4">
            {availableIntegrations.map((integration) => renderIntegrationTile(integration, 'available'))}
          </div>
        </section>
      </div>

      {/* Sheet Importer Modal */}
      {showSheetImporter && (
        <SheetImporter onClose={() => setShowSheetImporter(false)} />
      )}
    </div>
  );
}
