/**
 * Integrations management panel.
 *
 * Allows users to connect and disconnect integrations via Nango.
 */

import { useCallback, useEffect, useState } from 'react';
import {
  getAvailableIntegrations,
  getConnectedIntegrations,
  getConnectUrl,
  disconnectIntegration,
  triggerSync,
  type Integration,
  type AvailableIntegration,
} from '../api/client';

interface IntegrationsPanelProps {
  customerId: string;
  onClose: () => void;
}

export function IntegrationsPanel({
  customerId,
  onClose,
}: IntegrationsPanelProps): JSX.Element {
  const [available, setAvailable] = useState<AvailableIntegration[]>([]);
  const [connected, setConnected] = useState<Integration[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [syncing, setSyncing] = useState<Set<string>>(new Set());

  const loadIntegrations = useCallback(async () => {
    setIsLoading(true);

    const [availableRes, connectedRes] = await Promise.all([
      getAvailableIntegrations(),
      getConnectedIntegrations(),
    ]);

    if (availableRes.data) {
      setAvailable(availableRes.data.integrations);
    }
    if (connectedRes.data) {
      setConnected(connectedRes.data.integrations);
    }

    setIsLoading(false);
  }, []);

  useEffect(() => {
    void loadIntegrations();
  }, [loadIntegrations]);

  const handleConnect = async (provider: string): Promise<void> => {
    const response = await getConnectUrl(provider);
    if (response.data?.connect_url) {
      // Redirect to Nango OAuth flow
      window.location.href = response.data.connect_url;
    }
  };

  const handleDisconnect = async (provider: string): Promise<void> => {
    if (!confirm(`Disconnect ${provider}? You'll need to reconnect to sync data.`)) {
      return;
    }

    await disconnectIntegration(provider);
    await loadIntegrations();
  };

  const handleSync = async (provider: string): Promise<void> => {
    setSyncing((prev) => new Set([...prev, provider]));

    await triggerSync(customerId, provider);

    // Wait a bit then refresh status
    setTimeout(async () => {
      await loadIntegrations();
      setSyncing((prev) => {
        const next = new Set(prev);
        next.delete(provider);
        return next;
      });
    }, 2000);
  };

  const isConnected = (providerId: string): Integration | undefined => {
    return connected.find((c) => c.provider === providerId && c.is_active);
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-surface-900 rounded-xl border border-surface-800 max-w-lg w-full max-h-[80vh] overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-surface-800">
          <h2 className="text-lg font-semibold text-surface-100">Integrations</h2>
          <button
            onClick={onClose}
            className="text-surface-400 hover:text-surface-200 transition-colors"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="p-4 overflow-y-auto max-h-[60vh]">
          {isLoading ? (
            <div className="flex items-center justify-center py-8">
              <div className="w-6 h-6 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : (
            <div className="space-y-3">
              {available.map((integration) => {
                const connection = isConnected(integration.id);
                const isSyncing = syncing.has(integration.id);

                return (
                  <div
                    key={integration.id}
                    className="p-4 rounded-lg bg-surface-800 border border-surface-700"
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex items-center gap-3">
                        <IntegrationIcon provider={integration.id} />
                        <div>
                          <h3 className="font-medium text-surface-100">
                            {integration.name}
                          </h3>
                          <p className="text-sm text-surface-400">
                            {integration.description}
                          </p>
                        </div>
                      </div>

                      <div className="flex items-center gap-2">
                        {connection ? (
                          <>
                            <button
                              onClick={() => handleSync(integration.id)}
                              disabled={isSyncing}
                              className="px-3 py-1.5 text-sm bg-surface-700 text-surface-200 rounded-lg hover:bg-surface-600 transition-colors disabled:opacity-50"
                            >
                              {isSyncing ? 'Syncing...' : 'Sync'}
                            </button>
                            <button
                              onClick={() => handleDisconnect(integration.id)}
                              className="px-3 py-1.5 text-sm text-red-400 hover:text-red-300 transition-colors"
                            >
                              Disconnect
                            </button>
                          </>
                        ) : (
                          <button
                            onClick={() => handleConnect(integration.id)}
                            className="btn-primary text-sm py-1.5"
                          >
                            Connect
                          </button>
                        )}
                      </div>
                    </div>

                    {connection && (
                      <div className="mt-3 pt-3 border-t border-surface-700 text-xs text-surface-500">
                        <div className="flex items-center gap-4">
                          <span className="flex items-center gap-1">
                            <span className="w-2 h-2 rounded-full bg-green-500" />
                            Connected
                          </span>
                          {connection.last_sync_at && (
                            <span>
                              Last sync: {new Date(connection.last_sync_at).toLocaleString()}
                            </span>
                          )}
                          {connection.last_error && (
                            <span className="text-red-400">Error: {connection.last_error}</span>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function IntegrationIcon({ provider }: { provider: string }): JSX.Element {
  const iconClass = 'w-10 h-10 rounded-lg flex items-center justify-center text-white';

  switch (provider) {
    case 'hubspot':
      return (
        <div className={`${iconClass} bg-orange-500`}>
          <span className="text-lg font-bold">H</span>
        </div>
      );
    case 'slack':
      return (
        <div className={`${iconClass} bg-purple-600`}>
          <span className="text-lg font-bold">#</span>
        </div>
      );
    case 'google_calendar':
      return (
        <div className={`${iconClass} bg-blue-500`}>
          <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
            <path d="M19 4h-1V2h-2v2H8V2H6v2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 16H5V10h14v10zm0-12H5V6h14v2z" />
          </svg>
        </div>
      );
    case 'salesforce':
      return (
        <div className={`${iconClass} bg-sky-500`}>
          <span className="text-lg font-bold">SF</span>
        </div>
      );
    default:
      return (
        <div className={`${iconClass} bg-surface-600`}>
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101"
            />
          </svg>
        </div>
      );
  }
}
