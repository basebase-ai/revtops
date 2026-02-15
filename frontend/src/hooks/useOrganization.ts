/**
 * React Query hooks for organization data.
 * 
 * Handles fetching, caching, and mutations for organization data.
 * Automatically refetches on window focus and invalidates cache on mutations.
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { API_BASE } from '../lib/api';

// Types
export interface Organization {
  id: string;
  name: string;
  logoUrl: string | null;
  emailDomain: string | null;
}

export interface IdentityMapping {
  id: string;
  source: string;
  externalUserid: string | null;
  externalEmail: string | null;
  matchSource: string;
  updatedAt: string | null;
}

export interface TeamMember {
  id: string;
  name: string | null;
  email: string;
  role: string | null;
  avatarUrl: string | null;
  jobTitle: string | null;
  status: string | null;
  identities: IdentityMapping[];
}

interface OrganizationApiResponse {
  id: string;
  name: string;
  logo_url: string | null;
  email_domain: string | null;
}

interface IdentityMappingApiResponse {
  id: string;
  source: string;
  external_userid: string | null;
  external_email: string | null;
  match_source: string;
  updated_at: string | null;
}

interface TeamMembersApiResponse {
  members: Array<{
    id: string;
    name: string | null;
    email: string;
    role: string | null;
    avatar_url: string | null;
    job_title: string | null;
    status: string | null;
    identities: IdentityMappingApiResponse[];
  }>;
  unmapped_identities: IdentityMappingApiResponse[];
}

export interface TeamMembersResult {
  members: TeamMember[];
  unmappedIdentities: IdentityMapping[];
}

interface UpdateOrganizationParams {
  orgId: string;
  userId: string;
  name?: string;
  logoUrl?: string;
}

// Query keys - centralized for easy invalidation
export const organizationKeys = {
  all: ['organization'] as const,
  detail: (orgId: string) => ['organization', orgId] as const,
  members: (orgId: string) => ['organization', orgId, 'members'] as const,
};

// Fetch team members
async function fetchTeamMembers(orgId: string, userId: string): Promise<TeamMembersResult> {
  const response = await fetch(
    `${API_BASE}/auth/organizations/${orgId}/members?user_id=${userId}`
  );
  
  if (!response.ok) {
    throw new Error(`Failed to fetch team members: ${response.status}`);
  }
  
  const data = (await response.json()) as TeamMembersApiResponse;

  const mapIdentity = (i: IdentityMappingApiResponse): IdentityMapping => ({
    id: i.id,
    source: i.source,
    externalUserid: i.external_userid,
    externalEmail: i.external_email,
    matchSource: i.match_source,
    updatedAt: i.updated_at,
  });
  
  return {
    members: data.members.map((m) => ({
      id: m.id,
      name: m.name,
      email: m.email,
      role: m.role,
      avatarUrl: m.avatar_url,
      jobTitle: m.job_title ?? null,
      status: m.status ?? null,
      identities: (m.identities ?? []).map(mapIdentity),
    })),
    unmappedIdentities: (data.unmapped_identities ?? []).map(mapIdentity),
  };
}

// Update organization
async function updateOrganization(params: UpdateOrganizationParams): Promise<Organization> {
  const body: Record<string, string> = {};
  if (params.name !== undefined) body.name = params.name;
  if (params.logoUrl !== undefined) body.logo_url = params.logoUrl;
  
  const response = await fetch(
    `${API_BASE}/auth/organizations/${params.orgId}?user_id=${params.userId}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }
  );
  
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({})) as { detail?: string };
    throw new Error(errorData.detail ?? `Failed to update organization: ${response.status}`);
  }
  
  const data = (await response.json()) as OrganizationApiResponse;
  
  return {
    id: data.id,
    name: data.name,
    logoUrl: data.logo_url,
    emailDomain: data.email_domain,
  };
}

/**
 * Hook to fetch team members for an organization.
 * Automatically caches and refetches on window focus.
 */
export function useTeamMembers(orgId: string | null, userId: string | null) {
  return useQuery({
    queryKey: orgId ? organizationKeys.members(orgId) : ['disabled'],
    queryFn: () => {
      if (!orgId || !userId) throw new Error('Missing orgId or userId');
      return fetchTeamMembers(orgId, userId);
    },
    enabled: Boolean(orgId && userId),
  });
}

/**
 * Hook to link an identity mapping to a user (admin action).
 */
export function useLinkIdentity() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (params: { orgId: string; userId: string; targetUserId: string; mappingId: string }) => {
      const response = await fetch(
        `${API_BASE}/auth/organizations/${params.orgId}/members/link-identity?user_id=${params.userId}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            target_user_id: params.targetUserId,
            mapping_id: params.mappingId,
          }),
        }
      );
      if (!response.ok) {
        const err = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(err.detail ?? `Failed to link identity: ${response.status}`);
      }
      return (await response.json()) as { status: string };
    },
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({
        queryKey: organizationKeys.members(variables.orgId),
      });
    },
  });
}

/**
 * Hook to unlink an identity mapping from any user (admin action).
 */
export function useUnlinkIdentity() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (params: { orgId: string; userId: string; mappingId: string }) => {
      const response = await fetch(
        `${API_BASE}/auth/organizations/${params.orgId}/members/unlink-identity?user_id=${params.userId}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            mapping_id: params.mappingId,
          }),
        }
      );
      if (!response.ok) {
        const err = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(err.detail ?? `Failed to unlink identity: ${response.status}`);
      }
      return (await response.json()) as { status: string };
    },
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({
        queryKey: organizationKeys.members(variables.orgId),
      });
    },
  });
}

/**
 * Hook to update organization settings.
 * Automatically invalidates the organization cache on success.
 */
export function useUpdateOrganization() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: updateOrganization,
    onSuccess: (_data, variables) => {
      // Invalidate organization queries to refetch fresh data
      void queryClient.invalidateQueries({ 
        queryKey: organizationKeys.detail(variables.orgId) 
      });
    },
  });
}
