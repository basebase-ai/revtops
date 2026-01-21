/**
 * Data Sources management screen.
 * 
 * Features:
 * - View all connected data sources
 * - View available data sources to connect
 * - Sync status and manual sync trigger
 * - Disconnect integrations
 */

import { useState, useEffect } from 'react';

interface Integration {
  id: string;
  provider: string;
  name: string;
  description: string;
  connected: boolean;
  lastSyncAt: string | null;
  lastError: string | null;
  icon: JSX.Element;
  color: string;
}

interface DataSourcesProps {
  organizationId: string;
}

const AVAILABLE_INTEGRATIONS: Omit<Integration, 'connected' | 'lastSyncAt' | 'lastError'>[] = [
  {
    id: 'hubspot',
    provider: 'hubspot',
    name: 'HubSpot',
    description: 'CRM data including deals, contacts, and companies',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="w-8 h-8">
        <path d="M18.164 7.93V5.307a2.31 2.31 0 001.378-2.116 2.31 2.31 0 00-4.622 0c0 .953.588 1.775 1.416 2.116V7.93a6.144 6.144 0 00-3.398 1.606l-6.627-5.148A2.602 2.602 0 006.307 4.2a2.602 2.602 0 00-2.602-2.602 2.602 2.602 0 000 5.204c.497 0 .959-.144 1.354-.383l6.533 5.075a6.093 6.093 0 00-.702 2.863c0 1.024.255 1.988.702 2.837l-2.705 2.704a2.076 2.076 0 00-1.258-.428 2.077 2.077 0 100 4.153c1.147 0 2.078-.93 2.078-2.076 0-.461-.152-.886-.409-1.23l2.664-2.664a6.144 6.144 0 009.2-5.296 6.144 6.144 0 00-3.998-5.427z"/>
      </svg>
    ),
    color: 'bg-orange-500',
  },
  {
    id: 'salesforce',
    provider: 'salesforce',
    name: 'Salesforce',
    description: 'Opportunities, accounts, contacts, and activities',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="w-8 h-8">
        <path d="M10.006 5.415a4.195 4.195 0 013.045-1.306c1.56 0 2.954.9 3.69 2.205.63-.3 1.35-.45 2.1-.45 2.85 0 5.159 2.34 5.159 5.22s-2.31 5.22-5.16 5.22c-.45 0-.884-.06-1.305-.165a3.975 3.975 0 01-3.63 2.385 3.96 3.96 0 01-2.58-.96 4.65 4.65 0 01-3.66 1.8c-2.595 0-4.695-2.1-4.695-4.695 0-.975.3-1.875.81-2.625A3.92 3.92 0 012 8.835c0-2.19 1.77-3.96 3.96-3.96 1.02 0 1.95.39 2.67 1.02a4.17 4.17 0 011.376-.48z"/>
      </svg>
    ),
    color: 'bg-blue-500',
  },
  {
    id: 'slack',
    provider: 'slack',
    name: 'Slack',
    description: 'Team messages and communication history',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="w-8 h-8">
        <path d="M5.042 15.165a2.528 2.528 0 01-2.52 2.523A2.528 2.528 0 010 15.165a2.527 2.527 0 012.522-2.52h2.52v2.52zm1.271 0a2.527 2.527 0 012.521-2.52 2.527 2.527 0 012.521 2.52v6.313A2.528 2.528 0 018.834 24a2.528 2.528 0 01-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 01-2.521-2.52A2.528 2.528 0 018.834 0a2.528 2.528 0 012.521 2.522v2.52H8.834zm0 1.271a2.528 2.528 0 012.521 2.521 2.528 2.528 0 01-2.521 2.521H2.522A2.528 2.528 0 010 8.834a2.528 2.528 0 012.522-2.521h6.312zm10.122 2.521a2.528 2.528 0 012.522-2.521A2.528 2.528 0 0124 8.834a2.528 2.528 0 01-2.522 2.521h-2.522V8.834zm-1.268 0a2.528 2.528 0 01-2.523 2.521 2.527 2.527 0 01-2.52-2.521V2.522A2.527 2.527 0 0115.165 0a2.528 2.528 0 012.523 2.522v6.312zm-2.523 10.122a2.528 2.528 0 012.523 2.522A2.528 2.528 0 0115.165 24a2.527 2.527 0 01-2.52-2.522v-2.522h2.52zm0-1.268a2.527 2.527 0 01-2.52-2.523 2.526 2.526 0 012.52-2.52h6.313A2.527 2.527 0 0124 15.165a2.528 2.528 0 01-2.522 2.523h-6.313z"/>
      </svg>
    ),
    color: 'bg-purple-500',
  },
  {
    id: 'google_calendar',
    provider: 'google_calendar',
    name: 'Google Calendar',
    description: 'Meetings, events, and scheduling data',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="w-8 h-8">
        <path d="M19.5 3h-3V1.5H15V3H9V1.5H7.5V3h-3C3.675 3 3 3.675 3 4.5v15c0 .825.675 1.5 1.5 1.5h15c.825 0 1.5-.675 1.5-1.5v-15c0-.825-.675-1.5-1.5-1.5zm0 16.5h-15V8.25h15v11.25zM7.5 10.5h3v3h-3v-3zm4.5 0h3v3h-3v-3zm4.5 0h3v3h-3v-3z"/>
      </svg>
    ),
    color: 'bg-green-500',
  },
  {
    id: 'gmail',
    provider: 'gmail',
    name: 'Gmail',
    description: 'Email communication with prospects and customers',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="w-8 h-8">
        <path d="M24 5.457v13.909c0 .904-.732 1.636-1.636 1.636h-3.819V11.73L12 16.64l-6.545-4.91v9.273H1.636A1.636 1.636 0 010 19.366V5.457c0-2.023 2.309-3.178 3.927-1.964L5.455 4.64 12 9.548l6.545-4.91 1.528-1.145C21.69 2.28 24 3.434 24 5.457z"/>
      </svg>
    ),
    color: 'bg-red-500',
  },
  {
    id: 'linkedin',
    provider: 'linkedin',
    name: 'LinkedIn Sales Navigator',
    description: 'Lead and account insights from LinkedIn',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="w-8 h-8">
        <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/>
      </svg>
    ),
    color: 'bg-blue-600',
  },
];

export function DataSources({ organizationId }: DataSourcesProps): JSX.Element {
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [syncingProviders, setSyncingProviders] = useState<Set<string>>(new Set());

  useEffect(() => {
    loadIntegrations();
  }, [organizationId]);

  const loadIntegrations = async (): Promise<void> => {
    setIsLoading(true);
    try {
      // TODO: Fetch actual integration status from API
      // For now, mock some connected integrations
      const mockConnected = ['hubspot', 'slack'];
      
      const integrationsWithStatus: Integration[] = AVAILABLE_INTEGRATIONS.map((i) => ({
        ...i,
        connected: mockConnected.includes(i.id),
        lastSyncAt: mockConnected.includes(i.id) ? new Date(Date.now() - 1000 * 60 * 30).toISOString() : null,
        lastError: null,
      }));

      setIntegrations(integrationsWithStatus);
    } catch (error) {
      console.error('Failed to load integrations:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const handleConnect = async (provider: string): Promise<void> => {
    // TODO: Redirect to OAuth flow
    window.location.href = `/api/auth/connect/${provider}/redirect?user_id=${organizationId}`;
  };

  const handleDisconnect = async (provider: string): Promise<void> => {
    if (!confirm(`Are you sure you want to disconnect ${provider}?`)) return;

    try {
      // TODO: Call disconnect API
      setIntegrations((prev) =>
        prev.map((i) =>
          i.id === provider ? { ...i, connected: false, lastSyncAt: null } : i
        )
      );
    } catch (error) {
      console.error('Failed to disconnect:', error);
    }
  };

  const handleSync = async (provider: string): Promise<void> => {
    if (syncingProviders.has(provider)) return;

    setSyncingProviders((prev) => new Set(prev).add(provider));

    try {
      const response = await fetch(`/api/sync/${organizationId}/${provider}`, {
        method: 'POST',
      });

      if (!response.ok) throw new Error('Sync failed');

      // Poll for completion
      let attempts = 0;
      const checkStatus = async (): Promise<void> => {
        const statusRes = await fetch(`/api/sync/${organizationId}/${provider}/status`);
        const status = await statusRes.json();

        if (status.status === 'completed' || status.status === 'failed' || attempts >= 30) {
          setSyncingProviders((prev) => {
            const next = new Set(prev);
            next.delete(provider);
            return next;
          });

          if (status.status === 'completed') {
            setIntegrations((prev) =>
              prev.map((i) =>
                i.id === provider
                  ? { ...i, lastSyncAt: new Date().toISOString(), lastError: null }
                  : i
              )
            );
          } else if (status.status === 'failed') {
            setIntegrations((prev) =>
              prev.map((i) =>
                i.id === provider ? { ...i, lastError: status.error } : i
              )
            );
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

  const connectedIntegrations = integrations.filter((i) => i.connected);
  const availableIntegrations = integrations.filter((i) => !i.connected);

  if (isLoading) {
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
                  className="card flex items-center gap-4 p-4"
                >
                  <div className={`${integration.color} p-3 rounded-xl text-white`}>
                    {integration.icon}
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
                      onClick={() => void handleDisconnect(integration.provider)}
                      className="px-4 py-2 text-sm font-medium text-red-400 hover:text-red-300 hover:bg-red-500/10 rounded-lg transition-colors"
                    >
                      Disconnect
                    </button>
                  </div>
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
                  <div className={`${integration.color} p-3 rounded-xl text-white opacity-60`}>
                    {integration.icon}
                  </div>
                  <div className="flex-1 min-w-0">
                    <h3 className="font-medium text-surface-100">{integration.name}</h3>
                    <p className="text-sm text-surface-400 mt-0.5">
                      {integration.description}
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => void handleConnect(integration.provider)}
                  className="w-full mt-4 px-4 py-2 text-sm font-medium text-primary-400 border border-primary-500/30 hover:bg-primary-500/10 rounded-lg transition-colors"
                >
                  Connect
                </button>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
