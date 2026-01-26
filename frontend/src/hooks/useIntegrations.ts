/**
 * React Query hooks for integrations data.
 * 
 * Handles fetching and caching integration status.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { API_BASE } from '../lib/api';

// Types matching the backend response
export interface TeamConnection {
  userId: string;
  userName: string;
}

export interface Integration {
  id: string;
  provider: string;
  scope: 'organization' | 'user';
  isActive: boolean;
  lastSyncAt: string | null;
  lastError: string | null;
  connectedAt: string | null;
  connectedBy: string | null;
  currentUserConnected: boolean;
  teamConnections: TeamConnection[];
  teamTotal: number;
}

interface IntegrationApiResponse {
  id: string;
  provider: string;
  scope: string;
  is_active: boolean;
  last_sync_at: string | null;
  last_error: string | null;
  connected_at: string | null;
  connected_by: string | null;
  current_user_connected: boolean;
  team_connections: Array<{ user_id: string; user_name: string }>;
  team_total: number;
}

interface IntegrationsListResponse {
  integrations: IntegrationApiResponse[];
}

// Query keys
export const integrationKeys = {
  all: ['integrations'] as const,
  list: (orgId: string) => ['integrations', 'list', orgId] as const,
};

// Fetch integrations for an organization
async function fetchIntegrations(orgId: string, userId: string): Promise<Integration[]> {
  const response = await fetch(
    `${API_BASE}/auth/integrations?organization_id=${orgId}&user_id=${userId}`
  );
  
  if (!response.ok) {
    throw new Error(`Failed to fetch integrations: ${response.status}`);
  }
  
  const data = (await response.json()) as IntegrationsListResponse;
  
  return data.integrations.map((i) => ({
    id: i.id,
    provider: i.provider,
    scope: i.scope as 'organization' | 'user',
    isActive: i.is_active,
    lastSyncAt: i.last_sync_at,
    lastError: i.last_error,
    connectedAt: i.connected_at,
    connectedBy: i.connected_by,
    currentUserConnected: i.current_user_connected,
    teamConnections: i.team_connections.map((tc) => ({
      userId: tc.user_id,
      userName: tc.user_name,
    })),
    teamTotal: i.team_total,
  }));
}

/**
 * Hook to fetch integrations for an organization.
 * Automatically caches and refetches on window focus.
 */
export function useIntegrations(orgId: string | null, userId: string | null) {
  return useQuery({
    queryKey: orgId ? integrationKeys.list(orgId) : ['disabled'],
    queryFn: () => {
      if (!orgId || !userId) throw new Error('Missing orgId or userId');
      return fetchIntegrations(orgId, userId);
    },
    enabled: Boolean(orgId && userId),
  });
}

/**
 * Hook to get the query client for manual cache invalidation.
 * Use this after connecting/disconnecting integrations.
 */
export function useInvalidateIntegrations() {
  const queryClient = useQueryClient();
  
  return (orgId: string) => {
    void queryClient.invalidateQueries({ queryKey: integrationKeys.list(orgId) });
  };
}
