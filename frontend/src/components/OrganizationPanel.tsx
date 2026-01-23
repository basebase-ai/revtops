/**
 * Organization management panel (slide-out).
 * 
 * Features:
 * - View team members
 * - Invite new members
 * - Manage subscription/billing
 * - Organization settings
 */

import { useState, useEffect, useRef } from 'react';
import type { OrganizationInfo, UserProfile } from './AppLayout';
import { apiRequest } from '../lib/api';
import { supabase } from '../lib/supabase';
import { useAppStore } from '../store';

interface TeamMember {
  id: string;
  name: string | null;
  email: string;
  role: string | null;
  avatarUrl: string | null;
}

interface TeamMembersResponse {
  members: Array<{
    id: string;
    name: string | null;
    email: string;
    role: string | null;
    avatar_url: string | null;
  }>;
}

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
  const [teamMembers, setTeamMembers] = useState<TeamMember[]>([]);
  const [isLoadingMembers, setIsLoadingMembers] = useState(true);
  const [orgName, setOrgName] = useState(organization.name);
  const [logoUrl, setLogoUrl] = useState(organization.logoUrl);
  const [isSavingSettings, setIsSavingSettings] = useState(false);
  const [settingsSaved, setSettingsSaved] = useState(false);
  const [isUploadingLogo, setIsUploadingLogo] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Fetch team members on mount
  useEffect(() => {
    const fetchMembers = async (): Promise<void> => {
      if (!organization.id) {
        setIsLoadingMembers(false);
        return;
      }

      const response = await apiRequest<TeamMembersResponse>(
        `/auth/organizations/${organization.id}/members?user_id=${currentUser.id}`
      );

      if (response.data) {
        setTeamMembers(
          response.data.members.map((m) => ({
            id: m.id,
            name: m.name,
            email: m.email,
            role: m.role,
            avatarUrl: m.avatar_url,
          }))
        );
      }
      setIsLoadingMembers(false);
    };

    void fetchMembers();
  }, [organization.id, currentUser.id]);

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

    setIsSavingSettings(true);
    setSettingsSaved(false);
    try {
      const response = await apiRequest<{ id: string; name: string }>(
        `/auth/organizations/${organization.id}?user_id=${currentUser.id}`,
        {
          method: 'PATCH',
          body: JSON.stringify({ name: orgName }),
        }
      );

      if (response.error) {
        alert(`Failed to save: ${response.error}`);
      } else {
        setSettingsSaved(true);
        // Update the store so sidebar reflects the change
        setOrganization({ ...organization, name: orgName });
        // Reset saved indicator after 2 seconds
        setTimeout(() => setSettingsSaved(false), 2000);
      }
    } catch (error) {
      console.error('Failed to save settings:', error);
    } finally {
      setIsSavingSettings(false);
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

      // Update organization with new logo URL
      const response = await apiRequest<{ id: string; logo_url: string }>(
        `/auth/organizations/${organization.id}?user_id=${currentUser.id}`,
        {
          method: 'PATCH',
          body: JSON.stringify({ logo_url: newLogoUrl }),
        }
      );

      if (response.error) {
        alert(`Failed to save logo: ${response.error}`);
      } else {
        setLogoUrl(newLogoUrl);
        // Update the store so sidebar reflects the change
        setOrganization({ ...organization, logoUrl: newLogoUrl });
      }
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
                  Team members ({teamMembers.length})
                </h3>
                {isLoadingMembers ? (
                  <div className="flex items-center justify-center py-8">
                    <div className="animate-spin w-6 h-6 border-2 border-primary-500 border-t-transparent rounded-full" />
                  </div>
                ) : (
                  <div className="space-y-2">
                    {teamMembers.map((member) => {
                      const displayName = member.name ?? member.email.split('@')[0] ?? 'Unknown';
                      const isAdmin = member.role === 'admin' || member.id === currentUser.id;
                      return (
                        <div
                          key={member.id}
                          className="flex items-center gap-3 p-3 rounded-lg bg-surface-800/50"
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
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
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
                  disabled={isSavingSettings || !hasUnsavedChanges}
                  className="btn-primary disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isSavingSettings ? 'Saving...' : 'Save Changes'}
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
