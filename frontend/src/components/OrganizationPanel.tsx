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

import { useState, useRef } from 'react';
import type { OrganizationInfo, UserProfile } from './AppLayout';
import { supabase } from '../lib/supabase';
import { useAppStore } from '../store';
import { useTeamMembers, useUpdateOrganization, useLinkIdentity, useUnlinkIdentity } from '../hooks';
import type { TeamMember, IdentityMapping } from '../hooks';

interface OrganizationPanelProps {
  organization: OrganizationInfo;
  currentUser: UserProfile;
  onClose: () => void;
}

export function OrganizationPanel({ organization, currentUser, onClose }: OrganizationPanelProps): JSX.Element {
  const setOrganization = useAppStore((state) => state.setOrganization);
  const [activeTab, setActiveTab] = useState<'team' | 'billing' | 'settings'>('team');
  const [inviteEmail, setInviteEmail] = useState('');
  const [isInviting, setIsInviting] = useState(false);
  const [orgName, setOrgName] = useState(organization.name);
  const [logoUrl, setLogoUrl] = useState(organization.logoUrl);
  const [settingsSaved, setSettingsSaved] = useState(false);
  const [isUploadingLogo, setIsUploadingLogo] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [expandedMemberId, setExpandedMemberId] = useState<string | null>(null);

  // React Query: Fetch team members with automatic caching and refetch
  const { 
    data: teamData,
    isLoading: isLoadingMembers 
  } = useTeamMembers(organization.id, currentUser.id);

  const members: TeamMember[] = teamData?.members ?? [];
  const unmappedIdentities: IdentityMapping[] = teamData?.unmappedIdentities ?? [];
  const canManageIdentityLinks: boolean = members.some((member) => member.id === currentUser.id && member.role === 'admin');

  // React Query: Mutation for updating organization
  const updateOrgMutation = useUpdateOrganization();
  const linkIdentityMutation = useLinkIdentity();
  const unlinkIdentityMutation = useUnlinkIdentity();

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

  const handleInvite = async (): Promise<void> => {
    if (!inviteEmail.trim()) return;
    
    setIsInviting(true);
    try {
      // TODO: Call invite API
      await new Promise((r) => setTimeout(r, 1000));
      setInviteEmail('');
      alert('Invitation sent!');
    } catch (error) {
      console.error('Failed to invite:', error);
    } finally {
      setIsInviting(false);
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
              <p className="text-xs text-surface-400">Organization settings</p>
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
                    {members.map((member) => {
                      const displayName: string = member.name ?? member.email.split('@')[0] ?? 'Unknown';
                      const isAdmin: boolean = member.role === 'admin' || member.id === currentUser.id;
                      const isExpanded: boolean = expandedMemberId === member.id;
                      const identities: IdentityMapping[] = member.identities;

                      return (
                        <div key={member.id} className="rounded-lg bg-surface-800/50 overflow-hidden">
                          {/* Member row */}
                          <button
                            onClick={() => setExpandedMemberId(isExpanded ? null : member.id)}
                            className="flex items-center gap-3 p-3 w-full text-left hover:bg-surface-800/80 transition-colors"
                          >
                            {member.avatarUrl ? (
                              <img
                                src={member.avatarUrl}
                                alt={displayName}
                                className="w-10 h-10 rounded-full object-cover"
                              />
                            ) : (
                              <div className="w-10 h-10 rounded-full bg-primary-600 flex items-center justify-center text-white font-medium">
                                {displayName.charAt(0).toUpperCase()}
                              </div>
                            )}
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="font-medium text-surface-100 truncate">
                                  {displayName}
                                </span>
                                {isAdmin && (
                                  <span className="px-2 py-0.5 text-xs font-medium bg-primary-500/20 text-primary-400 rounded-full">
                                    Admin
                                  </span>
                                )}
                              </div>
                              <p className="text-sm text-surface-400 truncate">{member.email}</p>
                            </div>
                            {/* Identity count badges */}
                            <div className="flex items-center gap-1">
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
                                <span className="text-xs text-surface-500">No links</span>
                              )}
                            </div>
                            <svg
                              className={`w-4 h-4 text-surface-400 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
                              fill="none"
                              viewBox="0 0 24 24"
                              stroke="currentColor"
                            >
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                            </svg>
                          </button>

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
                                        {canManageIdentityLinks && (
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
              {/* Current Plan */}
              <div className="p-4 rounded-xl bg-gradient-to-r from-primary-600/20 to-primary-500/10 border border-primary-500/30">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="font-medium text-surface-100">Current Plan</h3>
                  <span className="px-2 py-1 text-xs font-medium bg-primary-500 text-white rounded-full">
                    Pro Trial
                  </span>
                </div>
                <p className="text-sm text-surface-400 mb-4">
                  Your trial expires in 14 days
                </p>
                <button className="w-full btn-primary">
                  Upgrade to Pro
                </button>
              </div>

              {/* Billing Details */}
              <div>
                <h3 className="text-sm font-medium text-surface-200 mb-3">Billing details</h3>
                <div className="card p-4 space-y-3">
                  <div className="flex justify-between text-sm">
                    <span className="text-surface-400">Payment method</span>
                    <span className="text-surface-200">Not set</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-surface-400">Billing email</span>
                    <span className="text-surface-200">{currentUser.email}</span>
                  </div>
                  <button className="w-full mt-2 px-4 py-2 text-sm font-medium text-primary-400 border border-primary-500/30 hover:bg-primary-500/10 rounded-lg transition-colors">
                    Add payment method
                  </button>
                </div>
              </div>

              {/* Usage */}
              <div>
                <h3 className="text-sm font-medium text-surface-200 mb-3">Usage this month</h3>
                <div className="card p-4 space-y-3">
                  <div>
                    <div className="flex justify-between text-sm mb-1">
                      <span className="text-surface-400">AI Queries</span>
                      <span className="text-surface-200">47 / 500</span>
                    </div>
                    <div className="h-2 bg-surface-700 rounded-full overflow-hidden">
                      <div className="h-full bg-primary-500 rounded-full" style={{ width: '9.4%' }} />
                    </div>
                  </div>
                  <div>
                    <div className="flex justify-between text-sm mb-1">
                      <span className="text-surface-400">Data Sources</span>
                      <span className="text-surface-200">2 / 5</span>
                    </div>
                    <div className="h-2 bg-surface-700 rounded-full overflow-hidden">
                      <div className="h-full bg-primary-500 rounded-full" style={{ width: '40%' }} />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'settings' && (
            <div className="space-y-6">
              {/* Organization Name */}
              <div>
                <label className="block text-sm font-medium text-surface-200 mb-2">
                  Organization name
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
                <h3 className="text-sm font-medium text-surface-200 mb-3">Organization Info</h3>
                <div className="card p-4 space-y-3 text-sm">
                  <div className="flex justify-between items-center">
                    <span className="text-surface-400">Organization ID</span>
                    <div className="flex items-center gap-2">
                      <span className="text-surface-300 font-mono text-xs">
                        {organization.id}
                      </span>
                      <button
                        onClick={() => void navigator.clipboard.writeText(organization.id)}
                        className="p-1 text-surface-400 hover:text-surface-200 hover:bg-surface-700 rounded transition-colors"
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
                <button className="px-4 py-2 text-sm font-medium text-red-400 border border-red-500/30 hover:bg-red-500/10 rounded-lg transition-colors">
                  Delete organization
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
