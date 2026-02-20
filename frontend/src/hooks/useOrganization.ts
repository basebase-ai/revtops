/**
 * React Query hooks for organization data.
 * 
 * Handles fetching, caching, and mutations for organization data.
 * Automatically refetches on window focus and invalidates cache on mutations.
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from '../lib/api';

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

// Fetch team members (uses apiRequest so JWT is sent; backend may require it)
async function fetchTeamMembers(orgId: string, userId: string): Promise<TeamMembersResult> {
  const { data, error } = await apiRequest<TeamMembersApiResponse>(
    `/auth/organizations/${encodeURIComponent(orgId)}/members?user_id=${encodeURIComponent(userId)}`
  );

  if (error || !data) {
    throw new Error(error ?? 'Failed to fetch team members');
  }

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

  const { data, error } = await apiRequest<OrganizationApiResponse>(
    `/auth/organizations/${encodeURIComponent(params.orgId)}?user_id=${encodeURIComponent(params.userId)}`,
    { method: 'PATCH', body: JSON.stringify(body) }
  );

  if (error || !data) {
    throw new Error(error ?? 'Failed to update organization');
  }

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
      const { data, error } = await apiRequest<{ status: string }>(
        `/auth/organizations/${encodeURIComponent(params.orgId)}/members/link-identity?user_id=${encodeURIComponent(params.userId)}`,
        {
          method: 'POST',
          body: JSON.stringify({
            target_user_id: params.targetUserId,
            mapping_id: params.mappingId,
          }),
        }
      );
      if (error || !data) throw new Error(error ?? 'Failed to link identity');
      return data;
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
      const { data, error } = await apiRequest<{ status: string }>(
        `/auth/organizations/${encodeURIComponent(params.orgId)}/members/unlink-identity?user_id=${encodeURIComponent(params.userId)}`,
        {
          method: 'POST',
          body: JSON.stringify({ mapping_id: params.mappingId }),
        }
      );
      if (error || !data) throw new Error(error ?? 'Failed to unlink identity');
      return data;
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
