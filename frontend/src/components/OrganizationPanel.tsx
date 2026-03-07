/**
 * Organization management panel (slide-out).
 * 
 * Features:
 * - View team members
 * - Invite new members
 * - Manage subscription/billing
 * - Organization settings
 * 
 * Uses React Query for server state (team members, org updates).
 */

import { useState, useRef, useEffect, useMemo } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { OrganizationInfo, UserProfile } from './AppLayout';
import { supabase } from '../lib/supabase';
import { useAppStore } from '../store';
import { useTeamMembers, useUpdateOrganization, useLinkIdentity, useUnlinkIdentity, useUpdateGuestUser, useUpdateMemberRole, useDeleteMember, useDeleteOrganization, organizationKeys } from '../hooks';
import type { TeamMember, IdentityMapping } from '../hooks';
import { apiRequest } from '../lib/api';
import { Avatar } from './Avatar';
import { SubscriptionSetup } from './SubscriptionSetup';

interface BillingStatus {
  subscription_tier: string | null;
  subscription_status: string | null;
  credits_balance: number;
  credits_included: number;
  current_period_end: string | null;
  cancel_at_period_end: string | null;
  cancel_scheduled: boolean;
  subscription_required: boolean;
}

interface PlanItem {
  tier: string;
  name: string;
  price_cents: number;
  credits_included: number;
}

interface CreditTransaction {
  timestamp: string;
  amount: number;
  balance_after: number;
  reason: string;
  user_email: string | null;
}

interface UserUsage {
  user_id: string;
  user_email: string;
  user_name: string | null;
  total_credits_used: number;
}

interface CreditDetails {
  transactions: CreditTransaction[];
  usage_by_user: UserUsage[];
  period_start: string | null;
  period_end: string | null;
  starting_balance: number;
}

interface OrganizationPanelProps {
  organization: OrganizationInfo;
  currentUser: UserProfile;
  initialTab?: 'team' | 'billing' | 'settings';
  onClose: () => void;
}

export function OrganizationPanel({ organization, currentUser, initialTab = 'team', onClose }: OrganizationPanelProps): JSX.Element {
  const queryClient = useQueryClient();
  const setOrganization = useAppStore((state) => state.setOrganization);
  const logout = useAppStore((state) => state.logout);
  const fetchUserOrganizations = useAppStore((state) => state.fetchUserOrganizations);
  const switchActiveOrganization = useAppStore((state) => state.switchActiveOrganization);
  const setCurrentView = useAppStore((state) => state.setCurrentView);
  const [activeTab, setActiveTab] = useState<'team' | 'billing' | 'settings'>(initialTab);

  useEffect(() => {
    setActiveTab(initialTab);
  }, [initialTab]);

  const [billing, setBilling] = useState<BillingStatus | null>(null);
  const [billingRefresh, setBillingRefresh] = useState(0);
  const [showChangePlan, setShowChangePlan] = useState(false);
  const [changePlanLoading, setChangePlanLoading] = useState<string | null>(null);
  const [cancelLoading, setCancelLoading] = useState(false);
  const [plans, setPlans] = useState<PlanItem[]>([]);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const { data } = await apiRequest<BillingStatus>('/billing/status');
      if (!cancelled && data) setBilling(data);
    })();
    return () => { cancelled = true; };
  }, [organization.id, activeTab, billingRefresh]);

  useEffect(() => {
    if (!showChangePlan) return;
    let cancelled = false;
    (async () => {
      const { data } = await apiRequest<{ plans: PlanItem[] }>('/billing/plans');
      if (!cancelled && data?.plans?.length) setPlans(data.plans);
    })();
    return () => { cancelled = true; };
  }, [showChangePlan]);

  const [showSubscriptionSetup, setShowSubscriptionSetup] = useState(false);
  const [showCreditDetails, setShowCreditDetails] = useState(false);
  const [creditDetails, setCreditDetails] = useState<CreditDetails | null>(null);
  const [creditDetailsLoading, setCreditDetailsLoading] = useState(false);

  useEffect(() => {
    if (!showCreditDetails) return;
    let cancelled = false;
    setCreditDetailsLoading(true);
    (async () => {
      const { data } = await apiRequest<CreditDetails>('/billing/credit-details');
      if (!cancelled) {
        setCreditDetails(data);
        setCreditDetailsLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [showCreditDetails]);

  const [inviteEmail, setInviteEmail] = useState('');
  const [isInviting, setIsInviting] = useState(false);
  const [isInvitingMissingFromSlack, setIsInvitingMissingFromSlack] = useState(false);
  const [orgName, setOrgName] = useState(organization.name);
  const [logoUrl, setLogoUrl] = useState(organization.logoUrl);
  const [settingsSaved, setSettingsSaved] = useState(false);
  const [isUploadingLogo, setIsUploadingLogo] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [expandedMemberId, setExpandedMemberId] = useState<string | null>(null);
  const [resendingMemberId, setResendingMemberId] = useState<string | null>(null);
  const [revokingInviteMemberId, setRevokingInviteMemberId] = useState<string | null>(null);
  const [menuOpenMemberId, setMenuOpenMemberId] = useState<string | null>(null);

  useEffect(() => {
    setOrgName(organization.name);
    setLogoUrl(organization.logoUrl);
    setSettingsSaved(false);
    setExpandedMemberId(null);
    setMenuOpenMemberId(null);
  }, [organization.id, organization.name, organization.logoUrl]);

  useEffect(() => {
    if (!menuOpenMemberId) return;
    const close = (): void => setMenuOpenMemberId(null);
    window.addEventListener('click', close);
    return () => window.removeEventListener('click', close);
  }, [menuOpenMemberId]);

  // React Query: Fetch team members with automatic caching and refetch
  const { 
    data: teamData,
    isLoading: isLoadingMembers 
  } = useTeamMembers(organization.id, currentUser.id);

  const members: TeamMember[] = teamData?.members ?? [];
  const sortedMembers: TeamMember[] = [...members].sort((a, b) => {
    const aInvited: boolean = a.status === 'invited';
    const bInvited: boolean = b.status === 'invited';
    if (aInvited !== bInvited) return aInvited ? -1 : 1;

    if (a.isGuest !== b.isGuest) return a.isGuest ? -1 : 1;

    const aName: string = (a.name ?? a.email).toLowerCase();
    const bName: string = (b.name ?? b.email).toLowerCase();
    return aName.localeCompare(bName);
  });
  const unmappedIdentities: IdentityMapping[] = teamData?.unmappedIdentities ?? [];
  const guestUserEnabled: boolean = Boolean(teamData?.guestUserEnabled);
  const canLinkIdentityInOrg: boolean = members.some((member) => member.id === currentUser.id);
  const isGlobalAdmin: boolean = currentUser.roles.includes('global_admin');
  const myMembership = members.find((member) => member.id === currentUser.id);
  const isOrgAdminForCurrentOrg: boolean = Boolean(myMembership?.role === 'admin');
  const canAdministerOrg: boolean = isGlobalAdmin || isOrgAdminForCurrentOrg;
  const canInviteOrRevokeInvites: boolean = isGlobalAdmin || Boolean(myMembership?.status === 'active');

  // React Query: Mutation for updating organization
  const updateOrgMutation = useUpdateOrganization();
  const linkIdentityMutation = useLinkIdentity();
  const unlinkIdentityMutation = useUnlinkIdentity();
  const updateGuestUserMutation = useUpdateGuestUser();
  const updateMemberRoleMutation = useUpdateMemberRole();
  const deleteMemberMutation = useDeleteMember();
  const deleteOrganizationMutation = useDeleteOrganization();

  const sourceLabel = (source: string): string => {
    const labels: Record<string, string> = { slack: 'Slack', hubspot: 'HubSpot', salesforce: 'Salesforce' };
    return labels[source] ?? source;
  };

  const sourceColor = (source: string): string => {
    const colors: Record<string, string> = {
      slack: 'bg-purple-500/20 text-purple-400',
      hubspot: 'bg-orange-500/20 text-orange-400',
      salesforce: 'bg-blue-500/20 text-blue-400',
    };
    return colors[source] ?? 'bg-surface-600/20 text-surface-300';
  };

  const handleLinkIdentity = async (targetUserId: string, mappingId: string): Promise<void> => {
    try {
      await linkIdentityMutation.mutateAsync({
        orgId: organization.id,
        userId: currentUser.id,
        targetUserId,
        mappingId,
      });
    } catch (error) {
      alert(`Link failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };



  const handleUnlinkIdentity = async (mappingId: string): Promise<void> => {
    try {
      await unlinkIdentityMutation.mutateAsync({
        orgId: organization.id,
        userId: currentUser.id,
        mappingId,
      });
    } catch (error) {
      alert(`Unlink failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };



  const handleUpdateMemberRole = async (targetUserId: string, role: 'admin' | 'member'): Promise<void> => {
    try {
      await updateMemberRoleMutation.mutateAsync({
        orgId: organization.id,
        userId: currentUser.id,
        targetUserId,
        role,
      });
    } catch (error) {
      alert(`Failed to update role: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleDeleteMember = async (targetUserId: string): Promise<void> => {
    const confirmed = window.confirm(
      'Delete this user from the team? This will unlink all identities and remove team access.'
    );
    if (!confirmed) return;

    try {
      await deleteMemberMutation.mutateAsync({
        orgId: organization.id,
        userId: currentUser.id,
        targetUserId,
      });
      if (expandedMemberId === targetUserId) {
        setExpandedMemberId(null);
      }
    } catch (error) {
      alert(`Failed to delete user: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleToggleGuestUser = async (): Promise<void> => {
    const nextEnabled = !guestUserEnabled;
    if (nextEnabled) {
      const confirmed = window.confirm(
        "Enabling guest user allows people in connected surfaces (usually Slack) to make queries via your team's tokens and resources."
      );
      if (!confirmed) return;
    }

    try {
      await updateGuestUserMutation.mutateAsync({
        orgId: organization.id,
        userId: currentUser.id,
        enabled: nextEnabled,
      });
    } catch (error) {
      alert(`Failed to update guest user: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleInvite = async (): Promise<void> => {
    const email: string = inviteEmail.trim().toLowerCase();
    if (!email) return;

    setIsInviting(true);
    try {
      const { API_BASE } = await import('../lib/api');
      const response = await fetch(
        `${API_BASE}/auth/organizations/${organization.id}/invitations?user_id=${currentUser.id}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email }),
        },
      );

      if (!response.ok) {
        const errData = (await response.json().catch(() => ({}))) as { detail?: string };
        alert(errData.detail ?? `Failed to invite: ${response.status}`);
        return;
      }

      setInviteEmail('');
      await queryClient.invalidateQueries({ queryKey: organizationKeys.members(organization.id) });
      await queryClient.refetchQueries({ queryKey: organizationKeys.members(organization.id), type: 'active' });
    } catch (error) {
      console.error('Failed to invite:', error);
      alert(`Failed to invite: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setIsInviting(false);
    }
  };

  const handleResendInvite = async (email: string, memberId: string): Promise<void> => {
    setResendingMemberId(memberId);
    try {
      const { API_BASE } = await import('../lib/api');
      const response = await fetch(
        `${API_BASE}/auth/organizations/${organization.id}/invitations?user_id=${currentUser.id}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email }),
        },
      );
      if (!response.ok) {
        const errData = (await response.json().catch(() => ({}))) as { detail?: string };
        alert(errData.detail ?? `Failed to resend: ${response.status}`);
        return;
      }
      alert('Invitation resent!');
    } catch (error) {
      console.error('Failed to resend invite:', error);
      alert(`Failed to resend: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setResendingMemberId(null);
    }
  };

  const handleRevokeInvite = async (targetUserId: string): Promise<void> => {
    const confirmed = window.confirm('Revoke this pending invitation? The user will no longer be able to join from this invite.');
    if (!confirmed) return;

    setRevokingInviteMemberId(targetUserId);
    try {
      await deleteMemberMutation.mutateAsync({
        orgId: organization.id,
        userId: currentUser.id,
        targetUserId,
      });
    } catch (error) {
      alert(`Failed to revoke invite: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setRevokingInviteMemberId((currentId) => (currentId === targetUserId ? null : currentId));
    }
  };

  interface SlackMissingInviteSummary {
    total_slack_users_with_email: number;
    already_in_org: number;
    missing_users: number;
    invited_count: number;
    requires_confirmation: boolean;
    invited_emails: string[];
  }

  const handleInviteMissingFromSlack = async (): Promise<void> => {
    setIsInvitingMissingFromSlack(true);
    try {
      const { API_BASE } = await import('../lib/api');
      const endpoint = `${API_BASE}/auth/organizations/${organization.id}/invitations/slack-missing?user_id=${currentUser.id}`;

      const previewResponse = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dry_run: true }),
      });

      if (!previewResponse.ok) {
        const errData = (await previewResponse.json().catch(() => ({}))) as { detail?: string };
        if (previewResponse.status === 404 && (errData.detail ?? '').toLowerCase().includes('slack integration not connected')) {
          alert('Slack is not connected for this org. You will be redirected to Connectors to connect Slack.');
          onClose();
          setCurrentView('data-sources');
          return;
        }
        alert(errData.detail ?? `Failed to check Slack users: ${previewResponse.status}`);
        return;
      }

      const previewData = (await previewResponse.json()) as SlackMissingInviteSummary;
      if (previewData.missing_users === 0) {
        alert('No missing Slack users to invite.');
        return;
      }

      if (previewData.requires_confirmation) {
        const confirmed = window.confirm(
          `This will invite ${previewData.missing_users} users from Slack who are not in this team. Continue?`
        );
        if (!confirmed) return;
      }

      const inviteResponse = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          dry_run: false,
          confirm_large_invite: previewData.requires_confirmation,
        }),
      });

      if (!inviteResponse.ok) {
        const errData = (await inviteResponse.json().catch(() => ({}))) as { detail?: string };
        alert(errData.detail ?? `Failed to invite missing Slack users: ${inviteResponse.status}`);
        return;
      }

      const inviteData = (await inviteResponse.json()) as SlackMissingInviteSummary;
      await queryClient.invalidateQueries({ queryKey: organizationKeys.members(organization.id) });
      await queryClient.refetchQueries({ queryKey: organizationKeys.members(organization.id), type: 'active' });
      alert(`Sent ${inviteData.invited_count} invitation${inviteData.invited_count === 1 ? '' : 's'} from Slack users.`);
    } catch (error) {
      console.error('Failed to invite missing Slack users:', error);
      alert(`Failed to invite missing Slack users: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setIsInvitingMissingFromSlack(false);
    }
  };

  const handleSaveSettings = async (): Promise<void> => {
    if (!orgName.trim() || orgName === organization.name) return;

    try {
      await updateOrgMutation.mutateAsync({
        orgId: organization.id,
        userId: currentUser.id,
        name: orgName,
      });
      
      setSettingsSaved(true);
      // Update Zustand store so sidebar reflects the change immediately
      setOrganization({ ...organization, name: orgName });
      setTimeout(() => setSettingsSaved(false), 2000);
    } catch (error) {
      alert(`Failed to save: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const currentMember = members.find((member) => member.id === currentUser.id);
  const isOrgAdmin = currentMember?.role === 'admin';

  const handleDeleteOrganization = async (): Promise<void> => {
    console.info('[OrganizationPanel] Delete organization clicked', {
      organizationId: organization.id,
      userId: currentUser.id,
      isOrgAdmin,
      isGlobalAdmin,
    });

    if (!canAdministerOrg) {
      console.warn('[OrganizationPanel] Delete organization blocked: user is not an org admin/global admin', {
        organizationId: organization.id,
        userId: currentUser.id,
      });
      alert("You can't do that. Only team admins can delete teams.");
      return;
    }

    const confirmed = window.confirm(
      `Delete ${organization.name}? This permanently removes the team and its data.`
    );
    if (!confirmed) return;

    try {
      console.info('[OrganizationPanel] Confirmed organization deletion', {
        organizationId: organization.id,
        userId: currentUser.id,
      });
      await deleteOrganizationMutation.mutateAsync({
        orgId: organization.id,
      });

      onClose();
      await fetchUserOrganizations();
      const remainingOrgs: { id: string; name: string; logoUrl: string | null }[] =
        useAppStore.getState().organizations;
      const nextOrg: { id: string } | undefined = remainingOrgs[0];
      if (nextOrg) {
        await switchActiveOrganization(nextOrg.id);
        alert('Team deleted.');
      } else {
        await supabase.auth.signOut();
        logout();
        localStorage.clear();
        sessionStorage.clear();
        alert('Team deleted. You will be signed out.');
        window.location.href = '/auth';
      }
    } catch (error) {
      const message: string = error instanceof Error ? error.message : 'Unknown error';
      const isNotFound: boolean = message.toLowerCase().includes('not found');
      if (isNotFound) {
        onClose();
        await fetchUserOrganizations();
        const remainingOrgs: { id: string }[] = useAppStore.getState().organizations;
        const nextOrg: { id: string } | undefined = remainingOrgs[0];
        if (nextOrg) {
          await switchActiveOrganization(nextOrg.id);
          alert('Team was already removed.');
        } else {
          await supabase.auth.signOut();
          logout();
          localStorage.clear();
          sessionStorage.clear();
          window.location.href = '/auth';
        }
        return;
      }
      console.error('[OrganizationPanel] Failed to delete organization', {
        organizationId: organization.id,
        userId: currentUser.id,
        error,
      });
      alert(`Failed to delete team: ${message}`);
    }
  };

  const hasUnsavedChanges = orgName !== organization.name;

  const handleLogoUpload = async (event: React.ChangeEvent<HTMLInputElement>): Promise<void> => {
    const file = event.target.files?.[0];
    if (!file) return;

    // Validate file type
    if (!file.type.startsWith('image/')) {
      alert('Please select an image file');
      return;
    }

    // Validate file size (max 2MB)
    if (file.size > 2 * 1024 * 1024) {
      alert('Image must be less than 2MB');
      return;
    }

    setIsUploadingLogo(true);
    try {
      // Generate unique filename
      const fileExt = file.name.split('.').pop() ?? 'png';
      const fileName = `${organization.id}/logo-${Date.now()}.${fileExt}`;

      // Upload to Supabase Storage
      const { error: uploadError } = await supabase.storage
        .from('org-logos')
        .upload(fileName, file, { upsert: true });

      if (uploadError) {
        console.error('Upload error:', uploadError);
        alert(`Failed to upload: ${uploadError.message}`);
        return;
      }

      // Get public URL
      const { data: urlData } = supabase.storage
        .from('org-logos')
        .getPublicUrl(fileName);

      const newLogoUrl = urlData.publicUrl;

      // Update organization with new logo URL using React Query mutation
      await updateOrgMutation.mutateAsync({
        orgId: organization.id,
        userId: currentUser.id,
        logoUrl: newLogoUrl,
      });

      setLogoUrl(newLogoUrl);
      // Update Zustand store so sidebar reflects the change immediately
      setOrganization({ ...organization, logoUrl: newLogoUrl });
    } catch (error) {
      console.error('Failed to upload logo:', error);
      alert('Failed to upload logo');
    } finally {
      setIsUploadingLogo(false);
      // Reset file input
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50 z-40"
        onClick={onClose}
      />

      {/* Panel */}
      <div className="fixed right-0 top-0 bottom-0 w-full max-w-lg bg-surface-900 border-l border-surface-800 z-50 flex flex-col shadow-2xl">
        {/* Header */}
        <header className="flex items-center justify-between px-6 py-4 border-b border-surface-800">
          <div className="flex items-center gap-3">
            {logoUrl ? (
              <img
                src={logoUrl}
                alt={organization.name}
                className="w-10 h-10 rounded-lg object-cover"
              />
            ) : (
              <div className="w-10 h-10 rounded-lg bg-surface-700 flex items-center justify-center text-surface-300 font-bold text-lg">
                {organization.name.charAt(0).toUpperCase()}
              </div>
            )}
            <div>
              <h2 className="font-semibold text-surface-100">{organization.name}</h2>
              <p className="text-xs text-surface-400">Team settings</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-surface-400 hover:text-surface-200 hover:bg-surface-800 rounded-lg transition-colors"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </header>

        {/* Tabs */}
        <div className="flex border-b border-surface-800">
          {(['team', 'billing', 'settings'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`flex-1 px-4 py-3 text-sm font-medium transition-colors ${
                activeTab === tab
                  ? 'text-primary-400 border-b-2 border-primary-500'
                  : 'text-surface-400 hover:text-surface-200'
              }`}
            >
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          {activeTab === 'team' && (
            <div className="space-y-6">
              {/* Invite Section */}
              <div>
                <h3 className="text-sm font-medium text-surface-200 mb-3">Invite team member</h3>
                <p className="mb-2 text-xs text-surface-500">
                  Inviting someone grants access to your org&apos;s data and credit usage.
                </p>
                <div className="flex gap-2">
                  <input
                    type="email"
                    placeholder="colleague@company.com"
                    value={inviteEmail}
                    onChange={(e) => setInviteEmail(e.target.value)}
                    className="input-field flex-1"
                  />
                  <button
                    onClick={() => void handleInvite()}
                    disabled={isInviting || !inviteEmail.trim()}
                    className="btn-primary whitespace-nowrap disabled:opacity-50"
                  >
                    {isInviting ? 'Sending...' : 'Send Invite'}
                  </button>
                </div>
                <button
                  type="button"
                  onClick={() => void handleInviteMissingFromSlack()}
                  disabled={isInvitingMissingFromSlack}
                  className="mt-2 text-sm text-primary-400 hover:text-primary-300 disabled:opacity-50"
                >
                  {isInvitingMissingFromSlack
                    ? 'Checking Slack users...'
                    : "Invite all Slack users that aren't yet in Basebase"}
                </button>
              </div>

              {/* Team List */}
              <div>
                <h3 className="text-sm font-medium text-surface-200 mb-3">
                  Team members ({members.length})
                </h3>
                {isLoadingMembers ? (
                  <div className="flex items-center justify-center py-8">
                    <div className="animate-spin w-6 h-6 border-2 border-primary-500 border-t-transparent rounded-full" />
                  </div>
                ) : (
                  <div className="space-y-2">
                    {sortedMembers.map((member) => {
                      const displayName: string = member.name ?? member.email.split('@')[0] ?? 'Unknown';
                      const isGuest: boolean = member.isGuest;
                      const isInvited: boolean = member.status === 'invited';
                      const isAdmin: boolean = member.role === 'admin'
                        || member.role === 'global_admin'
                        || member.canLoginAsAdmin;
                      const isExpanded: boolean = expandedMemberId === member.id;
                      const identities: IdentityMapping[] = [...member.identities].sort((a, b) => {
                        const sourceCompare = sourceLabel(a.source).localeCompare(sourceLabel(b.source));
                        if (sourceCompare !== 0) return sourceCompare;
                        const aTarget = (a.externalEmail ?? a.externalUserid ?? '').toLowerCase();
                        const bTarget = (b.externalEmail ?? b.externalUserid ?? '').toLowerCase();
                        return aTarget.localeCompare(bTarget);
                      });
                      const canUnlinkForMember: boolean = member.id === currentUser.id || canLinkIdentityInOrg;

                      if (isInvited) {
                        return (
                          <div key={member.id} className="rounded-lg bg-surface-800/50 overflow-hidden">
                            <div className="flex items-center gap-3 p-3">
                              <Avatar user={member} size="lg" />
                              <div className="flex-1 min-w-0">
                                <span className="font-medium text-surface-100 truncate block">
                                  {displayName}
                                </span>
                                <p className="text-sm text-surface-400 truncate">{member.email}</p>
                                <p className="text-xs text-amber-400/80 mt-0.5 italic">Invitation pending</p>
                              </div>
                              <button
                                type="button"
                                onClick={() => void handleResendInvite(member.email, member.id)}
                                disabled={resendingMemberId === member.id}
                                className="px-3 py-1.5 text-sm font-medium rounded-lg border border-surface-600 text-surface-300 hover:text-surface-100 hover:border-surface-500 hover:bg-surface-700/50 transition-colors disabled:opacity-50 flex-shrink-0"
                              >
                                {resendingMemberId === member.id ? 'Sending...' : 'Resend'}
                              </button>
                              {canInviteOrRevokeInvites && (
                                <button
                                  type="button"
                                  onClick={() => void handleRevokeInvite(member.id)}
                                  disabled={revokingInviteMemberId === member.id}
                                  className="px-3 py-1.5 text-sm font-medium rounded-lg border border-rose-700/70 text-rose-300 hover:text-rose-100 hover:border-rose-600 hover:bg-rose-900/30 transition-colors disabled:opacity-50 flex-shrink-0"
                                >
                                  {revokingInviteMemberId === member.id ? 'Revoking...' : 'Revoke'}
                                </button>
                              )}
                            </div>
                          </div>
                        );
                      }

                      const isMenuOpen: boolean = menuOpenMemberId === member.id;

                      return (
                        <div key={member.id} className="rounded-lg bg-surface-800/50">
                          {/* Member row */}
                          <div className="flex items-center gap-3 p-3">
                            <Avatar user={member} size="lg" />
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="font-medium text-surface-100 truncate">
                                  {displayName}
                                </span>
                                {isAdmin && (
                                  <span className="px-2 py-0.5 text-xs font-medium bg-primary-500/20 text-primary-400 rounded-full">
                                    admin
                                  </span>
                                )}
                                {isGuest && (
                                  <span className="px-2 py-0.5 text-xs font-medium bg-sky-500/20 text-sky-200 rounded-full">
                                    guest
                                  </span>
                                )}
                              </div>
                              {member.jobTitle && (
                                <p className="text-sm text-surface-300 truncate">{member.jobTitle}</p>
                              )}
                              <p className="text-sm text-surface-400 truncate">{member.email}</p>
                            </div>
                            {/* Identity badges */}
                            <div className="flex items-center gap-1.5">
                              {isGuest && (
                                <button
                                  type="button"
                                  onClick={() => void handleToggleGuestUser()}
                                  disabled={updateGuestUserMutation.isPending}
                                  className={`px-2 py-1 text-[10px] font-medium rounded transition-colors disabled:opacity-50 ${
                                    guestUserEnabled
                                      ? 'bg-amber-500/20 text-amber-100 hover:bg-amber-500/30'
                                      : 'bg-surface-700/40 text-surface-200 hover:bg-surface-700/60'
                                  }`}
                                >
                                  {updateGuestUserMutation.isPending
                                    ? 'Saving...'
                                    : guestUserEnabled
                                      ? 'Enabled'
                                      : 'Disabled'}
                                </button>
                              )}
                              {identities.length > 0 ? (
                                [...new Set(identities.map((i) => i.source))].map((src) => (
                                  <span
                                    key={src}
                                    className={`px-1.5 py-0.5 text-[10px] font-medium rounded ${sourceColor(src)}`}
                                  >
                                    {sourceLabel(src)}
                                  </span>
                                ))
                              ) : (
                                !isGuest && <span className="text-xs text-surface-500">No links</span>
                              )}
                            </div>
                            {/* Three-dots menu */}
                            {!isGuest && (
                              <div className="relative flex-shrink-0">
                                <button
                                  type="button"
                                  onClick={(e) => { e.stopPropagation(); setMenuOpenMemberId(isMenuOpen ? null : member.id); }}
                                  className="p-1 rounded hover:bg-surface-700/60 transition-colors text-surface-400 hover:text-surface-200"
                                >
                                  <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                                    <path d="M10 6a2 2 0 110-4 2 2 0 010 4zm0 6a2 2 0 110-4 2 2 0 010 4zm0 6a2 2 0 110-4 2 2 0 010 4z" />
                                  </svg>
                                </button>
                                {isMenuOpen && (
                                  <div className="absolute right-0 top-full mt-1 w-40 rounded-lg bg-surface-700 border border-surface-600 shadow-xl z-50 py-1">
                                    <button
                                      type="button"
                                      onClick={() => {
                                        setMenuOpenMemberId(null);
                                        setExpandedMemberId(isExpanded ? null : member.id);
                                      }}
                                      className="w-full text-left px-3 py-2 text-sm text-surface-200 hover:bg-surface-600/60 transition-colors"
                                    >
                                      Link accounts
                                    </button>
                                    {canAdministerOrg && (
                                      <>
                                        <button
                                          type="button"
                                          onClick={() => {
                                            setMenuOpenMemberId(null);
                                            const nextRole: 'admin' | 'member' = member.role === 'admin' ? 'member' : 'admin';
                                            void handleUpdateMemberRole(member.id, nextRole);
                                          }}
                                          disabled={updateMemberRoleMutation.isPending}
                                          className="w-full text-left px-3 py-2 text-sm text-surface-200 hover:bg-surface-600/60 transition-colors disabled:opacity-50"
                                        >
                                          {member.role === 'admin' ? 'Remove admin' : 'Make admin'}
                                        </button>
                                        <button
                                          type="button"
                                          onClick={() => {
                                            setMenuOpenMemberId(null);
                                            void handleDeleteMember(member.id);
                                          }}
                                          disabled={deleteMemberMutation.isPending}
                                          className="w-full text-left px-3 py-2 text-sm text-red-400 hover:bg-surface-600/60 transition-colors disabled:opacity-50"
                                        >
                                          Delete User
                                        </button>
                                      </>
                                    )}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>

                          {/* Expanded identity details */}
                          {isExpanded && (
                            <div className="px-3 pb-3 pt-1 border-t border-surface-700/50">
                              <p className="text-xs text-surface-500 mb-2">Linked identities</p>
                              {identities.length > 0 ? (
                                <div className="space-y-1.5">
                                  {identities.map((identity) => (
                                    <div
                                      key={identity.id}
                                      className="flex items-center gap-2 text-xs px-2 py-1.5 rounded bg-surface-700/30"
                                    >
                                      <span className={`px-1.5 py-0.5 font-medium rounded ${sourceColor(identity.source)}`}>
                                        {sourceLabel(identity.source)}
                                      </span>
                                      <span className="text-surface-300 truncate">
                                        {identity.externalEmail ?? identity.externalUserid ?? 'Unknown'}
                                      </span>
                                      <div className="ml-auto flex items-center gap-2 whitespace-nowrap">
                                        <span className="text-surface-500">
                                          {identity.matchSource.replace(/_/g, ' ')}
                                        </span>
                                        {canUnlinkForMember && (
                                          <button
                                            onClick={() => void handleUnlinkIdentity(identity.id)}
                                            disabled={unlinkIdentityMutation.isPending}
                                            className="text-primary-400 hover:text-primary-300 disabled:opacity-50"
                                          >
                                            Unlink
                                          </button>
                                        )}
                                      </div>
                                    </div>
                                  ))}
                                </div>
                              ) : (
                                <p className="text-xs text-surface-500 italic">
                                  No external accounts linked yet.
                                </p>
                              )}

                              {isGuest && (
                                <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3 mt-3">
                                  <h4 className="text-xs font-medium text-amber-200">Guest user</h4>
                                  <p className="text-xs text-amber-100 mt-1">
                                    The guest user is the identity anonymous Slack entities run as when they are not linked yet.
                                  </p>
                                  <p className="text-[11px] text-amber-300/80 mt-2">
                                    Guest users cannot sign in, connect integrations, or be masqueraded as.
                                  </p>
                                </div>
                              )}

                              {/* Show unmapped identities that could be linked to this user */}
                              {unmappedIdentities.length > 0 && (
                                <div className="mt-3">
                                  <p className="text-xs text-surface-500 mb-1.5">Link an unmatched account:</p>
                                  <div className="space-y-1">
                                    {unmappedIdentities.map((ui) => (
                                      <button
                                        key={ui.id}
                                        onClick={() => void handleLinkIdentity(member.id, ui.id)}
                                        disabled={linkIdentityMutation.isPending || unlinkIdentityMutation.isPending}
                                        className="flex items-center gap-2 text-xs px-2 py-1.5 rounded bg-surface-700/20 hover:bg-surface-700/50 transition-colors w-full text-left disabled:opacity-50"
                                      >
                                        <span className={`px-1.5 py-0.5 font-medium rounded ${sourceColor(ui.source)}`}>
                                          {sourceLabel(ui.source)}
                                        </span>
                                        <span className="text-surface-400 truncate">
                                          {ui.externalEmail ?? ui.externalUserid}
                                        </span>
                                        <span className="ml-auto text-primary-400 whitespace-nowrap">+ Link</span>
                                      </button>
                                    ))}
                                  </div>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Unmapped external identities */}
              {unmappedIdentities.length > 0 && (
                <div>
                  <h3 className="text-sm font-medium text-surface-200 mb-1">
                    Unmatched accounts ({unmappedIdentities.length})
                  </h3>
                  <p className="text-xs text-surface-500 mb-3">
                    Found during sync but not yet linked to a team member. Expand a member above to link.
                  </p>
                  <div className="space-y-1.5">
                    {unmappedIdentities.map((ui) => (
                      <div
                        key={ui.id}
                        className="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface-800/30 border border-surface-700/50"
                      >
                        <span className={`px-1.5 py-0.5 text-[10px] font-medium rounded ${sourceColor(ui.source)}`}>
                          {sourceLabel(ui.source)}
                        </span>
                        <span className="text-sm text-surface-300 truncate">
                          {ui.externalEmail ?? ui.externalUserid}
                        </span>
                        <span className="px-2 py-0.5 text-[10px] font-medium bg-yellow-500/20 text-yellow-400 rounded-full ml-auto">
                          Unmatched
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

            </div>
          )}

          {activeTab === 'billing' && (
            <div className="space-y-6">
              {showSubscriptionSetup ? (
                <SubscriptionSetup
                  onComplete={() => {
                    setShowSubscriptionSetup(false);
                    setBillingRefresh((k) => k + 1);
                  }}
                  onBack={() => setShowSubscriptionSetup(false)}
                  currentTier={billing?.subscription_tier ?? null}
                />
              ) : (
                <>
                  {/* Current Plan & Credits */}
                  <div className="p-4 rounded-xl bg-gradient-to-r from-primary-600/20 to-primary-500/10 border border-primary-500/30">
                    <div className="mb-3">
                      <p className="text-xs text-surface-400 uppercase tracking-wide mb-1">Current Plan</p>
                      <h3 className="text-2xl font-bold text-white capitalize">
                        {billing?.subscription_tier ?? 'None'}
                      </h3>
                    </div>
                    {/* Free tier - show upgrade CTA */}
                    {billing?.subscription_tier === 'free' && (
                      <>
                        <p className="text-sm text-surface-400 mb-3">
                          {billing?.credits_included ?? 100} credits/month included.
                          Upgrade to unlock more.
                        </p>
                        <button
                          type="button"
                          onClick={() => setShowSubscriptionSetup(true)}
                          className="px-4 py-2 text-sm font-medium text-white bg-primary-500 hover:bg-primary-600 rounded-lg transition-colors"
                        >
                          Upgrade plan
                        </button>
                      </>
                    )}
                    {/* No subscription at all (legacy) */}
                    {billing?.subscription_required && !billing?.subscription_tier && (
                      <>
                        <p className="text-sm text-surface-400 mb-3">
                          Add a payment method to use credits.
                        </p>
                        <button
                          type="button"
                          onClick={() => setShowSubscriptionSetup(true)}
                          className="px-4 py-2 text-sm font-medium text-white bg-primary-500 hover:bg-primary-600 rounded-lg transition-colors"
                        >
                          Select plan
                        </button>
                      </>
                    )}
                    {/* Payment pending for paid tier */}
                    {billing?.subscription_required && billing?.subscription_tier && billing?.subscription_tier !== 'free' && (
                      <p className="text-sm text-surface-400">
                        Payment pending. Credits will be available once your first payment is confirmed.
                      </p>
                    )}
                    {!billing?.subscription_required && billing?.current_period_end && !billing?.cancel_at_period_end && !billing?.cancel_scheduled && (
                      <p className="text-sm text-surface-400 mb-2">
                        Period ends {new Date(billing.current_period_end).toLocaleDateString()}
                      </p>
                    )}
                    {(billing?.cancel_at_period_end || billing?.cancel_scheduled) && (
                      <p className="text-sm text-amber-400/90 mb-2">
                        Your subscription will not renew.
                        {billing?.cancel_at_period_end
                          ? ` Access until ${new Date(billing.cancel_at_period_end).toLocaleDateString()}.`
                          : ''}
                      </p>
                    )}
                    {/* Change/Cancel buttons only for paid tiers (not free) */}
                    {billing?.subscription_tier && billing?.subscription_tier !== 'free' && !showChangePlan && (
                      <div className="flex flex-wrap gap-2 mt-3">
                        <button
                          type="button"
                          onClick={() => setShowChangePlan(true)}
                          className="px-3 py-1.5 text-sm font-medium text-surface-200 bg-surface-600 hover:bg-surface-500 rounded-lg transition-colors"
                        >
                          Change plan
                        </button>
                        {!billing?.cancel_at_period_end && !billing?.cancel_scheduled && (
                          <button
                            type="button"
                            onClick={async () => {
                              if (!window.confirm('Your subscription will end at the end of the current period. You\'ll keep access until then. Continue?')) return;
                              setCancelLoading(true);
                              try {
                                const { error } = await apiRequest('/billing/cancel', { method: 'POST' });
                                if (!error) {
                                  setBillingRefresh((k) => k + 1);
                                }
                              } finally {
                                setCancelLoading(false);
                              }
                            }}
                            disabled={cancelLoading}
                            className="px-3 py-1.5 text-sm font-medium text-red-300 hover:text-red-200 bg-surface-600 hover:bg-surface-500 rounded-lg transition-colors disabled:opacity-50"
                          >
                            {cancelLoading ? 'Cancelling…' : 'Cancel subscription'}
                          </button>
                        )}
                      </div>
                    )}
                  </div>

                  {showChangePlan && billing?.subscription_tier && (
                    <div className="card p-4 space-y-3">
                      <div className="flex items-center justify-between">
                        <h3 className="text-sm font-medium text-surface-200">Change plan</h3>
                        <button
                          type="button"
                          onClick={() => setShowChangePlan(false)}
                          className="text-sm text-surface-400 hover:text-surface-200"
                        >
                          Close
                        </button>
                      </div>
                      <div className="space-y-2">
                        {plans.map((plan) => {
                          const isCurrent = plan.tier === billing?.subscription_tier;
                          return (
                            <div
                              key={plan.tier}
                              className="flex items-center justify-between py-2 px-3 rounded-lg bg-surface-700/50"
                            >
                              <span className="text-sm text-surface-200">
                                {plan.name} — ${(plan.price_cents / 100).toFixed(2)}/mo, {plan.credits_included} credits
                              </span>
                              <button
                                type="button"
                                onClick={async () => {
                                  if (isCurrent) return;
                                  setChangePlanLoading(plan.tier);
                                  try {
                                    const { error } = await apiRequest('/billing/subscription', {
                                      method: 'PATCH',
                                      body: JSON.stringify({ tier: plan.tier }),
                                    });
                                    if (!error) {
                                      setBillingRefresh((k) => k + 1);
                                      setShowChangePlan(false);
                                    }
                                  } finally {
                                    setChangePlanLoading(null);
                                  }
                                }}
                                disabled={isCurrent || changePlanLoading !== null}
                                className="text-sm font-medium text-primary-400 hover:text-primary-300 disabled:opacity-50 disabled:cursor-default"
                              >
                                {isCurrent ? 'Current' : changePlanLoading === plan.tier ? 'Updating…' : `Switch to ${plan.name}`}
                              </button>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* Billing Details */}
                  <div>
                    <h3 className="text-sm font-medium text-surface-200 mb-3">Billing details</h3>
                    <div className="card p-4 space-y-3">
                      <div className="flex justify-between text-sm">
                        <span className="text-surface-400">Billing email</span>
                        <span className="text-surface-200">{currentUser.email}</span>
                      </div>
                    </div>
                  </div>

                  {/* Credits usage */}
                  <div>
                    <div className="flex items-center justify-between mb-3">
                      <h3 className="text-sm font-medium text-surface-200">Credits this period</h3>
                      <button
                        onClick={() => setShowCreditDetails(true)}
                        className="text-xs text-primary-400 hover:text-primary-300 transition-colors"
                      >
                        More information
                      </button>
                    </div>
                    <div className="card p-4 space-y-3">
                      <div>
                        <div className="flex justify-between text-sm mb-1">
                          <span className="text-surface-400">Remaining</span>
                          <span className="text-surface-200">
                            {billing != null ? `${billing.credits_balance} / ${billing.credits_included}` : '—'}
                          </span>
                        </div>
                        <div className="h-2 bg-surface-700 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-primary-500 rounded-full"
                            style={{
                              width:
                                billing && billing.credits_included > 0
                                  ? `${Math.min(100, (billing.credits_balance / billing.credits_included) * 100)}%`
                                  : '0%',
                            }}
                          />
                        </div>
                      </div>
                      {billing?.current_period_end && (
                        <div className="flex justify-between text-sm pt-2 border-t border-surface-700">
                          <span className="text-surface-400">Resets</span>
                          <span className="text-surface-300">
                            {(() => {
                              const resetDate = new Date(billing.current_period_end);
                              const now = new Date();
                              const daysRemaining = Math.ceil((resetDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
                              if (daysRemaining <= 0) return 'Today';
                              if (daysRemaining === 1) return 'Tomorrow';
                              if (daysRemaining <= 7) return `in ${daysRemaining} days`;
                              return resetDate.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
                            })()}
                          </span>
                        </div>
                      )}
                    </div>
                  </div>
                </>
              )}
            </div>
          )}

          {activeTab === 'settings' && (
            <div className="space-y-6">
              {/* Organization Name */}
              <div>
                <label className="block text-sm font-medium text-surface-200 mb-2">
                  Team name
                </label>
                <input
                  type="text"
                  value={orgName}
                  onChange={(e) => setOrgName(e.target.value)}
                  className="input-field"
                />
              </div>

              {/* Logo */}
              <div>
                <label className="block text-sm font-medium text-surface-200 mb-2">
                  Logo
                </label>
                <div className="flex items-center gap-4">
                  {logoUrl ? (
                    <img
                      src={logoUrl}
                      alt={organization.name}
                      className="w-16 h-16 rounded-xl object-cover"
                    />
                  ) : (
                    <div className="w-16 h-16 rounded-xl bg-surface-800 flex items-center justify-center text-surface-400 font-bold text-2xl">
                      {organization.name.charAt(0).toUpperCase()}
                    </div>
                  )}
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/*"
                    onChange={(e) => void handleLogoUpload(e)}
                    className="hidden"
                  />
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    disabled={isUploadingLogo}
                    className="px-4 py-2 text-sm font-medium text-surface-200 bg-surface-800 hover:bg-surface-700 rounded-lg transition-colors disabled:opacity-50"
                  >
                    {isUploadingLogo ? 'Uploading...' : 'Upload logo'}
                  </button>
                </div>
              </div>

              {/* Save Button */}
              <div className="flex items-center gap-3">
                <button
                  onClick={() => void handleSaveSettings()}
                  disabled={updateOrgMutation.isPending || !hasUnsavedChanges}
                  className="btn-primary disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {updateOrgMutation.isPending ? 'Saving...' : 'Save Changes'}
                </button>
                {settingsSaved && (
                  <span className="text-sm text-green-400 flex items-center gap-1">
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                    Saved
                  </span>
                )}
              </div>

              {/* Organization Info */}
              <div className="pt-6 border-t border-surface-800">
                <h3 className="text-sm font-medium text-surface-200 mb-3">Team Info</h3>
                <div className="card p-4 space-y-3 text-sm">
                  {organization.handle != null && (
                    <div className="flex justify-between items-center gap-2">
                      <span className="text-surface-400">Workspace URL</span>
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="text-surface-300 font-mono text-xs truncate">
                          {window.location.origin}/{organization.handle}
                        </span>
                        <button
                          onClick={() => void navigator.clipboard.writeText(`${window.location.origin}/${organization.handle}`)}
                          className="flex-shrink-0 p-1 text-surface-400 hover:text-surface-200 hover:bg-surface-700 rounded transition-colors"
                          title="Copy URL"
                        >
                          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                          </svg>
                        </button>
                      </div>
                    </div>
                  )}
                  <div className="flex justify-between items-center gap-2">
                    <span className="text-surface-400">Team ID</span>
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="text-surface-300 font-mono text-xs truncate">
                        {organization.id}
                      </span>
                      <button
                        onClick={() => void navigator.clipboard.writeText(organization.id)}
                        className="flex-shrink-0 p-1 text-surface-400 hover:text-surface-200 hover:bg-surface-700 rounded transition-colors"
                        title="Copy ID"
                      >
                        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                        </svg>
                      </button>
                    </div>
                  </div>
                </div>
              </div>

              {/* Danger Zone */}
              <div className="pt-6 border-t border-surface-800">
                <h3 className="text-sm font-medium text-red-400 mb-3">Danger zone</h3>
                <button
                  onClick={() => void handleDeleteOrganization()}
                  disabled={deleteOrganizationMutation.isPending}
                  className="px-4 py-2 text-sm font-medium text-red-400 border border-red-500/30 hover:bg-red-500/10 rounded-lg transition-colors disabled:opacity-50"
                >
                  {deleteOrganizationMutation.isPending ? 'Deleting...' : 'Delete team'}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Credit Details Modal */}
      {showCreditDetails && (
        <CreditDetailsModal
          details={creditDetails}
          loading={creditDetailsLoading}
          onClose={() => setShowCreditDetails(false)}
        />
      )}
    </>
  );
}


interface CreditDetailsModalProps {
  details: CreditDetails | null;
  loading: boolean;
  onClose: () => void;
}

function CreditDetailsModal({ details, loading, onClose }: CreditDetailsModalProps): JSX.Element {
  const [PlotComponent, setPlotComponent] = useState<typeof import('react-plotly.js').default | null>(null);
  const [chartRange, setChartRange] = useState<[string, string] | null>(null);

  useEffect(() => {
    import('react-plotly.js')
      .then((mod) => setPlotComponent(() => mod.default))
      .catch(() => console.error('Failed to load chart library'));
  }, []);

  const burndownData = useMemo(() => {
    if (!details?.transactions.length) return null;
    
    const timestamps: string[] = [];
    const balances: number[] = [];
    
    // Start with starting_balance (which is credits_included when showing all-time)
    let runningBalance = details.starting_balance;
    
    // Add starting point (use first transaction time if no period_start)
    const startTime = details.period_start || details.transactions[0]?.timestamp || new Date().toISOString();
    timestamps.push(startTime);
    balances.push(runningBalance);
    
    // Add each transaction point, recalculating balance from starting point
    for (const tx of details.transactions) {
      runningBalance += tx.amount; // amount is negative for deductions
      timestamps.push(tx.timestamp);
      balances.push(runningBalance);
    }
    
    return { timestamps, balances };
  }, [details]);

  const fullBillingPeriodRange = useMemo<[string, string] | null>(() => {
    if (!burndownData || burndownData.timestamps.length === 0) return null;

    const start = details?.period_start || burndownData.timestamps[0];
    const end = details?.period_end || burndownData.timestamps[burndownData.timestamps.length - 1];
    if (!start || !end) return null;

    return [start, end];
  }, [burndownData, details?.period_end, details?.period_start]);

  useEffect(() => {
    setChartRange(fullBillingPeriodRange);
  }, [fullBillingPeriodRange]);

  const userUsageData = useMemo(() => {
    if (!details?.usage_by_user.length) return null;
    
    const labels = details.usage_by_user.map(u => u.user_name || u.user_email.split('@')[0]);
    const values = details.usage_by_user.map(u => u.total_credits_used);
    const emails = details.usage_by_user.map(u => u.user_email);
    
    return { labels, values, emails };
  }, [details]);

  return (
    <div 
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div 
        className="bg-surface-900 border border-surface-700 rounded-xl shadow-2xl w-full max-w-4xl max-h-[85vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-6 py-4 border-b border-surface-800 flex items-center justify-between shrink-0">
          <h2 className="text-lg font-semibold text-surface-100">Credit Usage Details</h2>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-surface-800 text-surface-400 hover:text-surface-200 transition-colors"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-8">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-500" />
            </div>
          ) : !details ? (
            <div className="text-center py-12 text-surface-400">
              Failed to load credit details
            </div>
          ) : (
            <>
              {/* Burndown Chart */}
              <div>
                <div className="mb-4 flex items-center justify-between gap-3">
                  <h3 className="text-sm font-medium text-surface-200">Credit Balance Over Time</h3>
                  <button
                    type="button"
                    onClick={() => {
                      console.debug('[CreditDetails] Resetting credit usage chart range to full billing period');
                      setChartRange(fullBillingPeriodRange);
                    }}
                    disabled={!fullBillingPeriodRange}
                    className="px-3 py-1.5 rounded-md border border-surface-600 text-xs font-medium text-surface-200 hover:bg-surface-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    Reset graph
                  </button>
                </div>
                {burndownData && PlotComponent ? (
                  <div className="bg-surface-800/50 rounded-lg p-4">
                    <PlotComponent
                      data={[
                        {
                          x: burndownData.timestamps,
                          y: burndownData.balances,
                          type: 'scatter',
                          mode: 'lines+markers',
                          fill: 'tozeroy',
                          fillcolor: 'rgba(99, 102, 241, 0.1)',
                          line: { color: '#6366f1', width: 2 },
                          marker: { color: '#6366f1', size: 6 },
                          hovertemplate: '%{y} credits<br>%{x|%b %d, %H:%M}<extra></extra>',
                        },
                      ]}
                      layout={{
                        autosize: true,
                        height: 280,
                        margin: { l: 50, r: 20, t: 20, b: 50 },
                        paper_bgcolor: 'transparent',
                        plot_bgcolor: 'transparent',
                        font: { color: '#a1a1aa' },
                        xaxis: {
                          gridcolor: 'rgba(255,255,255,0.05)',
                          tickformat: '%b %d',
                          range: chartRange ?? undefined,
                        },
                        yaxis: {
                          gridcolor: 'rgba(255,255,255,0.05)',
                          title: { text: 'Credits', standoff: 10 },
                          rangemode: 'tozero',
                        },
                        hovermode: 'x unified',
                      }}
                      config={{ displayModeBar: false, responsive: true }}
                      onRelayout={(event) => {
                        const eventWithRange = event as Record<string, unknown>;
                        const start = eventWithRange['xaxis.range[0]'];
                        const end = eventWithRange['xaxis.range[1]'];
                        if (typeof start === 'string' && typeof end === 'string') {
                          setChartRange([start, end]);
                          return;
                        }
                        if (eventWithRange['xaxis.autorange'] === true) {
                          setChartRange(fullBillingPeriodRange);
                        }
                      }}
                      style={{ width: '100%' }}
                    />
                  </div>
                ) : burndownData ? (
                  <div className="bg-surface-800/50 rounded-lg p-8 text-center text-surface-400">
                    Loading chart...
                  </div>
                ) : (
                  <div className="bg-surface-800/50 rounded-lg p-8 text-center text-surface-400">
                    No usage data for this period yet
                  </div>
                )}
              </div>

              {/* Usage by User */}
              <div>
                <h3 className="text-sm font-medium text-surface-200 mb-4">Usage by Team Member</h3>
                {userUsageData && userUsageData.values.length > 0 ? (
                  <div className="space-y-4">
                    {/* Bar visualization */}
                    <div className="space-y-3">
                      {details.usage_by_user.map((user, idx) => {
                        const maxUsage = Math.max(...details.usage_by_user.map(u => u.total_credits_used));
                        const percentage = maxUsage > 0 ? (user.total_credits_used / maxUsage) * 100 : 0;
                        return (
                          <div key={user.user_id} className="group">
                            <div className="flex items-center justify-between text-sm mb-1">
                              <span className="text-surface-300 truncate max-w-[200px]" title={user.user_email}>
                                {user.user_name || user.user_email.split('@')[0]}
                              </span>
                              <span className="text-surface-200 font-medium">{user.total_credits_used} credits</span>
                            </div>
                            <div className="h-2 bg-surface-700 rounded-full overflow-hidden">
                              <div
                                className="h-full rounded-full transition-all duration-300"
                                style={{
                                  width: `${percentage}%`,
                                  backgroundColor: `hsl(${240 - idx * 30}, 70%, 60%)`,
                                }}
                              />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    
                    {/* Total */}
                    <div className="pt-3 border-t border-surface-700 flex justify-between text-sm">
                      <span className="text-surface-400">Total used this period</span>
                      <span className="text-surface-200 font-medium">
                        {details.usage_by_user.reduce((sum, u) => sum + u.total_credits_used, 0)} credits
                      </span>
                    </div>
                  </div>
                ) : (
                  <div className="bg-surface-800/50 rounded-lg p-8 text-center text-surface-400">
                    No usage data for this period yet
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
