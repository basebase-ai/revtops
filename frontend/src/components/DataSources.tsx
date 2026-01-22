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
import Nango from '@nangohq/frontend';
import { API_BASE } from '../lib/api';
import { useAppStore } from '../store';

export function DataSources(): JSX.Element {
  // Get state from Zustand store
  const { 
    organization,
    integrations, 
    integrationsLoading,
    fetchIntegrations,
  } = useAppStore();

  const [syncingProviders, setSyncingProviders] = useState<Set<string>>(new Set());
  const [connectingProvider, setConnectingProvider] = useState<string | null>(null);

  // Fetch integrations on mount
  useEffect(() => {
    void fetchIntegrations();
  }, [fetchIntegrations]);

  const organizationId = organization?.id ?? '';

  const handleConnect = async (provider: string): Promise<void> => {
    if (connectingProvider || !organizationId) return;
    setConnectingProvider(provider);

    try {
      // Get session token from backend
      const response = await fetch(
        `${API_BASE}/auth/connect/${provider}/session?organization_id=${organizationId}`
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
            // Connection successful - reload integrations
            console.log('Connection successful, reloading integrations');
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
    if (!organizationId) return;
    if (!confirm(`Are you sure you want to disconnect ${provider}?`)) return;

    const url = `${API_BASE}/auth/integrations/${provider}?organization_id=${organizationId}`;
    console.log('Disconnecting:', { provider, organizationId, url });

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

      console.log('Disconnect successful, reloading integrations...');
      // Reload integrations to reflect the change
      await fetchIntegrations();
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

          // Refresh integrations to get updated sync status
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

  const connectedIntegrations = integrations.filter((i) => i.connected);
  const availableIntegrations = integrations.filter((i) => !i.connected);

  // Icon renderer based on icon identifier
  const renderIcon = (iconId: string): JSX.Element => {
    switch (iconId) {
      case 'hubspot':
        return (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-8 h-8">
            <path d="M18.164 7.93V5.307a2.31 2.31 0 001.378-2.116 2.31 2.31 0 00-4.622 0c0 .953.588 1.775 1.416 2.116V7.93a6.144 6.144 0 00-3.398 1.606l-6.627-5.148A2.602 2.602 0 006.307 4.2a2.602 2.602 0 00-2.602-2.602 2.602 2.602 0 000 5.204c.497 0 .959-.144 1.354-.383l6.533 5.075a6.093 6.093 0 00-.702 2.863c0 1.024.255 1.988.702 2.837l-2.705 2.704a2.076 2.076 0 00-1.258-.428 2.077 2.077 0 100 4.153c1.147 0 2.078-.93 2.078-2.076 0-.461-.152-.886-.409-1.23l2.664-2.664a6.144 6.144 0 009.2-5.296 6.144 6.144 0 00-3.998-5.427z"/>
          </svg>
        );
      case 'salesforce':
        return (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-8 h-8">
            <path d="M10.006 5.415a4.195 4.195 0 013.045-1.306c1.56 0 2.954.9 3.69 2.205.63-.3 1.35-.45 2.1-.45 2.85 0 5.159 2.34 5.159 5.22s-2.31 5.22-5.16 5.22c-.45 0-.884-.06-1.305-.165a3.975 3.975 0 01-3.63 2.385 3.96 3.96 0 01-2.58-.96 4.65 4.65 0 01-3.66 1.8c-2.595 0-4.695-2.1-4.695-4.695 0-.975.3-1.875.81-2.625A3.92 3.92 0 012 8.835c0-2.19 1.77-3.96 3.96-3.96 1.02 0 1.95.39 2.67 1.02a4.17 4.17 0 011.376-.48z"/>
          </svg>
        );
      case 'slack':
        return (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-8 h-8">
            <path d="M5.042 15.165a2.528 2.528 0 01-2.52 2.523A2.528 2.528 0 010 15.165a2.527 2.527 0 012.522-2.52h2.52v2.52zm1.271 0a2.527 2.527 0 012.521-2.52 2.527 2.527 0 012.521 2.52v6.313A2.528 2.528 0 018.834 24a2.528 2.528 0 01-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 01-2.521-2.52A2.528 2.528 0 018.834 0a2.528 2.528 0 012.521 2.522v2.52H8.834zm0 1.271a2.528 2.528 0 012.521 2.521 2.528 2.528 0 01-2.521 2.521H2.522A2.528 2.528 0 010 8.834a2.528 2.528 0 012.522-2.521h6.312zm10.122 2.521a2.528 2.528 0 012.522-2.521A2.528 2.528 0 0124 8.834a2.528 2.528 0 01-2.522 2.521h-2.522V8.834zm-1.268 0a2.528 2.528 0 01-2.523 2.521 2.527 2.527 0 01-2.52-2.521V2.522A2.527 2.527 0 0115.165 0a2.528 2.528 0 012.523 2.522v6.312zm-2.523 10.122a2.528 2.528 0 012.523 2.522A2.528 2.528 0 0115.165 24a2.527 2.527 0 01-2.52-2.522v-2.522h2.52zm0-1.268a2.527 2.527 0 01-2.52-2.523 2.526 2.526 0 012.52-2.52h6.313A2.527 2.527 0 0124 15.165a2.528 2.528 0 01-2.522 2.523h-6.313z"/>
          </svg>
        );
      case 'google-calendar':
        return (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-8 h-8">
            <path d="M19.5 3h-3V1.5H15V3H9V1.5H7.5V3h-3C3.675 3 3 3.675 3 4.5v15c0 .825.675 1.5 1.5 1.5h15c.825 0 1.5-.675 1.5-1.5v-15c0-.825-.675-1.5-1.5-1.5zm0 16.5h-15V8.25h15v11.25zM7.5 10.5h3v3h-3v-3zm4.5 0h3v3h-3v-3zm4.5 0h3v3h-3v-3z"/>
          </svg>
        );
      case 'microsoft-calendar':
      case 'microsoft_calendar':
        return (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-8 h-8">
            <path d="M19.5 3h-3V1.5H15V3H9V1.5H7.5V3h-3C3.675 3 3 3.675 3 4.5v15c0 .825.675 1.5 1.5 1.5h15c.825 0 1.5-.675 1.5-1.5v-15c0-.825-.675-1.5-1.5-1.5zm0 16.5h-15V8.25h15v11.25zM7.5 10.5h3v3h-3v-3zm4.5 0h3v3h-3v-3zm4.5 0h3v3h-3v-3z"/>
          </svg>
        );
      case 'microsoft-mail':
      case 'microsoft_mail':
        return (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-8 h-8">
            <path d="M20 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/>
          </svg>
        );
      default:
        return (
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-8 h-8">
            <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
          </svg>
        );
    }
  };

  // Color mapper
  const getColorClass = (color: string): string => {
    const colorMap: Record<string, string> = {
      'from-orange-500 to-orange-600': 'bg-orange-500',
      'from-blue-500 to-blue-600': 'bg-blue-500',
      'from-purple-500 to-purple-600': 'bg-purple-500',
      'from-green-500 to-green-600': 'bg-green-500',
      'from-sky-500 to-sky-600': 'bg-sky-500',
    };
    return colorMap[color] ?? 'bg-surface-600';
  };

  if (integrationsLoading && integrations.length === 0) {
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
                  onClick={() => void handleConnect(integration.provider)}
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
                    'Connect'
                  )}
                </button>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
