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

import { useState } from 'react';
import Nango from '@nangohq/frontend';
import type { IconType } from 'react-icons';
import {
  SiSalesforce,
  SiHubspot,
  SiSlack,
  SiGooglecalendar,
  SiGmail,
} from 'react-icons/si';
import { HiOutlineCalendar, HiOutlineMail, HiGlobeAlt, HiUserGroup } from 'react-icons/hi';
import { API_BASE } from '../lib/api';
import { useAppStore } from '../store';
import { useIntegrations, useInvalidateIntegrations, type Integration } from '../hooks';

// Icon map for integration providers
const ICON_MAP: Record<string, IconType> = {
  hubspot: SiHubspot,
  salesforce: SiSalesforce,
  slack: SiSlack,
  'google-calendar': SiGooglecalendar,
  google_calendar: SiGooglecalendar,
  gmail: SiGmail,
  'microsoft-calendar': HiOutlineCalendar,
  microsoft_calendar: HiOutlineCalendar,
  'microsoft-mail': HiOutlineMail,
  microsoft_mail: HiOutlineMail,
};

// Integration display config (colors, icons, descriptions)
const INTEGRATION_CONFIG: Record<string, { name: string; description: string; icon: string; color: string }> = {
  hubspot: { name: 'HubSpot', description: 'CRM data including deals, contacts, and companies', icon: 'hubspot', color: 'from-orange-500 to-orange-600' },
  salesforce: { name: 'Salesforce', description: 'CRM - Opportunities, Accounts', icon: 'salesforce', color: 'from-blue-500 to-blue-600' },
  slack: { name: 'Slack', description: 'Team messages and communication history', icon: 'slack', color: 'from-purple-500 to-purple-600' },
  google_calendar: { name: 'Google Calendar', description: 'Meetings, events, and scheduling data', icon: 'google_calendar', color: 'from-green-500 to-green-600' },
  gmail: { name: 'Gmail', description: 'Google email communications', icon: 'gmail', color: 'from-red-500 to-red-600' },
  microsoft_calendar: { name: 'Microsoft Calendar', description: 'Outlook calendar events and meetings', icon: 'microsoft_calendar', color: 'from-sky-500 to-sky-600' },
  microsoft_mail: { name: 'Microsoft Mail', description: 'Outlook emails and communications', icon: 'microsoft_mail', color: 'from-sky-500 to-sky-600' },
};

// Extended integration type with display info
interface DisplayIntegration extends Integration {
  name: string;
  description: string;
  icon: string;
  color: string;
  connected: boolean;
}

export function DataSources(): JSX.Element {
  // Get user/org from Zustand (auth state)
  const { user, organization } = useAppStore();

  // React Query: Fetch integrations with automatic caching and refetch
  const { 
    data: rawIntegrations = [], 
    isLoading: integrationsLoading,
  } = useIntegrations(organization?.id ?? null, user?.id ?? null);

  // Get invalidation function for manual refetch after connect/disconnect
  const invalidateIntegrations = useInvalidateIntegrations();

  const [syncingProviders, setSyncingProviders] = useState<Set<string>>(new Set());
  const [connectingProvider, setConnectingProvider] = useState<string | null>(null);

  const organizationId = organization?.id ?? '';
  const userId = user?.id ?? '';

  // Transform raw integrations to display integrations with UI metadata
  const integrations: DisplayIntegration[] = rawIntegrations.map((integration) => {
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
      const scope = ['gmail', 'google_calendar', 'microsoft_calendar', 'microsoft_mail'].includes(provider) 
        ? 'user' as const 
        : 'organization' as const;
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

      const data: { session_token: string } = await response.json();

      // Initialize Nango and open connect UI in popup
      const nango = new Nango();
      
      nango.openConnectUI({
        sessionToken: data.session_token,
        onEvent: (event) => {
          console.log('Nango event:', event);
          
          // Handle different possible event types from Nango
          const eventType = event.type as string;
          if (
            eventType === 'connect' ||
            eventType === 'connection-created' ||
            eventType === 'success'
          ) {
            // Connection successful - invalidate cache to refetch integrations
            console.log('Connection successful, invalidating integrations cache');
            invalidateIntegrations(organizationId);
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

  const handleDisconnect = async (provider: string, scope: 'organization' | 'user'): Promise<void> => {
    if (!organizationId) return;
    // User-scoped integrations require user_id
    if (scope === 'user' && !userId) return;
    
    if (!confirm(`Are you sure you want to disconnect ${provider}?`)) return;

    const params = new URLSearchParams({ organization_id: organizationId });
    if (scope === 'user' && userId) {
      params.set('user_id', userId);
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

      console.log('Disconnect successful, invalidating integrations cache...');
      // Invalidate cache to refetch integrations
      invalidateIntegrations(organizationId);
    } catch (error) {
      console.error('Failed to disconnect:', error);
      alert(`Failed to disconnect: ${error instanceof Error ? error.message : 'Unknown error'}`);
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
            invalidateIntegrations(organizationId);
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

  const connectedIntegrations = allIntegrations.filter((i) => i.connected);
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
      'from-purple-500 to-purple-600': 'bg-purple-500',
      'from-green-500 to-green-600': 'bg-green-500',
      'from-sky-500 to-sky-600': 'bg-sky-500',
      'from-red-500 to-red-600': 'bg-red-500',
    };
    return colorMap[color] ?? 'bg-surface-600';
  };

  // Team connections footer for user-scoped integrations
  const renderTeamConnections = (integration: DisplayIntegration): JSX.Element | null => {
    if (integration.scope !== 'user' || integration.teamTotal === 0) return null;

    const connectedCount = integration.teamConnections.length;
    const names = integration.teamConnections.map((tc) => tc.userName);
    
    // Show up to 3 names, then "+X more"
    const displayNames = names.slice(0, 3);
    const remaining = names.length - 3;
    const nameText = remaining > 0
      ? `${displayNames.join(', ')}, +${remaining} more`
      : displayNames.join(', ');

    return (
      <div className="mt-3 pt-3 border-t border-surface-700/50">
        <div className="flex items-center gap-2 text-sm text-surface-400">
          <HiUserGroup className="w-4 h-4" />
          <span>
            {connectedCount}/{integration.teamTotal} team connected
          </span>
        </div>
        {connectedCount > 0 && (
          <p className="text-xs text-surface-500 mt-1 pl-6">
            {nameText}
          </p>
        )}
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
    <div className="flex-1 overflow-y-auto">
      {/* Header */}
      <header className="sticky top-0 bg-surface-950 border-b border-surface-800 px-8 py-6">
        <h1 className="text-2xl font-bold text-surface-50">Data Sources</h1>
        <p className="text-surface-400 mt-1">
          Connect your sales tools to unlock AI-powered insights
        </p>
      </header>

      <div className="max-w-4xl mx-auto px-8 py-8 space-y-10">
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
              {connectedIntegrations.map((integration) => (
                <div
                  key={integration.id}
                  className="card p-4"
                >
                  <div className="flex items-center gap-4">
                    <div className={`${getColorClass(integration.color)} p-3 rounded-xl text-white`}>
                      {renderIcon(integration.icon)}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <h3 className="font-medium text-surface-100">{integration.name}</h3>
                        <span className="px-2 py-0.5 text-xs font-medium bg-emerald-500/20 text-emerald-400 rounded-full">
                          Connected
                        </span>
                      </div>
                      <p className="text-sm text-surface-400 mt-0.5">
                        {integration.description}
                      </p>
                      {integration.lastSyncAt && (
                        <p className="text-xs text-surface-500 mt-1">
                          Last synced: {new Date(integration.lastSyncAt).toLocaleString()}
                        </p>
                      )}
                      {integration.lastError && (
                        <p className="text-xs text-red-400 mt-1">
                          Error: {integration.lastError}
                        </p>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => void handleSync(integration.provider)}
                        disabled={syncingProviders.has(integration.provider)}
                        className="px-4 py-2 text-sm font-medium text-surface-200 bg-surface-800 hover:bg-surface-700 disabled:opacity-50 rounded-lg transition-colors flex items-center gap-2"
                      >
                        {syncingProviders.has(integration.provider) ? (
                          <>
                            <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                            </svg>
                            Syncing...
                          </>
                        ) : (
                          <>
                            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                            </svg>
                            Sync
                          </>
                        )}
                      </button>
                      <button
                        onClick={() => void handleDisconnect(integration.provider, integration.scope)}
                        className="px-4 py-2 text-sm font-medium text-red-400 hover:text-red-300 hover:bg-red-500/10 rounded-lg transition-colors"
                      >
                        Disconnect
                      </button>
                    </div>
                  </div>
                  {/* Team connections footer for user-scoped integrations */}
                  {renderTeamConnections(integration)}
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Available Sources */}
        <section>
          <h2 className="text-lg font-semibold text-surface-100 mb-4">
            Available Sources
          </h2>

          <div className="grid gap-4 sm:grid-cols-2">
            {availableIntegrations.map((integration) => (
              <div
                key={integration.id}
                className="card p-4 hover:border-surface-700 transition-colors"
              >
                <div className="flex items-start gap-4">
                  <div className={`${getColorClass(integration.color)} p-3 rounded-xl text-white opacity-60`}>
                    {renderIcon(integration.icon)}
                  </div>
                  <div className="flex-1 min-w-0">
                    <h3 className="font-medium text-surface-100">{integration.name}</h3>
                    <p className="text-sm text-surface-400 mt-0.5">
                      {integration.description}
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => void handleConnect(integration.provider, integration.scope)}
                  disabled={connectingProvider === integration.provider}
                  className="w-full mt-4 px-4 py-2 text-sm font-medium text-primary-400 border border-primary-500/30 hover:bg-primary-500/10 disabled:opacity-50 rounded-lg transition-colors flex items-center justify-center gap-2"
                >
                  {connectingProvider === integration.provider ? (
                    <>
                      <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                      Connecting...
                    </>
                  ) : (
                    integration.scope === 'user' ? 'Connect your account' : 'Connect'
                  )}
                </button>
                {/* Team connections footer for user-scoped integrations */}
                {renderTeamConnections(integration)}
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
