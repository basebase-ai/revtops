/**
 * User profile management panel (slide-out).
 * 
 * Features:
 * - View/edit profile info
 * - Change avatar
 * - Sign out
 */

import { useState } from 'react';
import type { UserProfile } from './AppLayout';
import { API_BASE } from '../lib/api';

interface ProfilePanelProps {
  user: UserProfile;
  onClose: () => void;
  onLogout: () => void;
  onUpdateUser: (updates: Partial<UserProfile>) => void;
}

export function ProfilePanel({ user, onClose, onLogout, onUpdateUser }: ProfilePanelProps): JSX.Element {
  const [name, setName] = useState(user.name ?? '');
  const [jobTitle, setJobTitle] = useState(user.jobTitle ?? '');
  const [phoneNumber, setPhoneNumber] = useState(user.phoneNumber ?? '');
  const [isSaving, setIsSaving] = useState(false);
  // Only use local state for a NEW avatar selection, otherwise use the user prop directly
  const [newAvatarFile, setNewAvatarFile] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  
  // Use new file if selected, otherwise use the user's current avatar
  const avatarPreview = newAvatarFile ?? user.avatarUrl;

  const handleSave = async (): Promise<void> => {
    setIsSaving(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/auth/me?user_id=${user.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name || null,
          avatar_url: avatarPreview,
          agent_global_commands: user.agentGlobalCommands,
          phone_number: phoneNumber.trim() || null,
          job_title: jobTitle.trim() || null,
        }),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(data.detail ?? 'Failed to update profile');
      }

      const updatedUser = await response.json() as {
        name: string | null;
        avatar_url: string | null;
        agent_global_commands: string | null;
        phone_number: string | null;
        job_title: string | null;
      };
      
      // Update the store
      onUpdateUser({
        name: updatedUser.name,
        avatarUrl: updatedUser.avatar_url,
        agentGlobalCommands: updatedUser.agent_global_commands,
        phoneNumber: updatedUser.phone_number,
        jobTitle: updatedUser.job_title,
      });
      
      onClose();
    } catch (err) {
      console.error('Failed to save:', err);
      setError(err instanceof Error ? err.message : 'Failed to save profile');
    } finally {
      setIsSaving(false);
    }
  };

  const handleAvatarChange = (e: React.ChangeEvent<HTMLInputElement>): void => {
    const file = e.target.files?.[0];
    if (file) {
      // Check file size (limit to 500KB for base64 storage)
      if (file.size > 500 * 1024) {
        setError('Image too large. Please choose an image under 500KB.');
        return;
      }
      const reader = new FileReader();
      reader.onloadend = () => {
        setNewAvatarFile(reader.result as string);
        setError(null);
      };
      reader.readAsDataURL(file);
    }
  };

  const handleLogout = (): void => {
    onClose();
    onLogout();
  };

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50 z-40"
        onClick={onClose}
      />

      {/* Panel */}
      <div className="fixed right-0 top-0 bottom-0 w-full max-w-md bg-surface-900 border-l border-surface-800 z-50 flex flex-col shadow-2xl">
        {/* Header */}
        <header className="flex items-center justify-between px-6 py-4 border-b border-surface-800">
          <h2 className="font-semibold text-surface-100">Profile</h2>
          <button
            onClick={onClose}
            className="p-2 text-surface-400 hover:text-surface-200 hover:bg-surface-800 rounded-lg transition-colors"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </header>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Avatar Section */}
          <div className="flex flex-col items-center">
            <div className="relative">
              {avatarPreview ? (
                <img
                  src={avatarPreview}
                  alt={name || user.email}
                  className="w-24 h-24 rounded-full object-cover"
                  referrerPolicy="no-referrer"
                />
              ) : (
                <div className="w-24 h-24 rounded-full bg-primary-600 flex items-center justify-center text-white font-bold text-3xl">
                  {(name || user.email).charAt(0).toUpperCase()}
                </div>
              )}
              <label className="absolute bottom-0 right-0 p-2 bg-surface-800 hover:bg-surface-700 rounded-full cursor-pointer transition-colors">
                <svg className="w-4 h-4 text-surface-200" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 13a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
                <input
                  type="file"
                  accept="image/*"
                  onChange={handleAvatarChange}
                  className="hidden"
                />
              </label>
            </div>
            <p className="text-sm text-surface-400 mt-3">
              Click camera icon to change photo
            </p>
          </div>

          {/* Form Fields */}
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-surface-200 mb-2">
                Display name
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Your name"
                className="input-field"
              />
            </div>


            <div>
              <label className="block text-sm font-medium text-surface-200 mb-2">
                Job title
              </label>
              <input
                type="text"
                value={jobTitle}
                onChange={(e) => setJobTitle(e.target.value)}
                placeholder="e.g. VP of Sales, Account Executive"
                className="input-field"
                maxLength={255}
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-surface-200 mb-2">
                Phone number
              </label>
              <input
                type="tel"
                value={phoneNumber}
                onChange={(e) => setPhoneNumber(e.target.value)}
                placeholder="e.g. +1 415-555-1234"
                className="input-field"
                maxLength={30}
              />
              <p className="text-xs text-surface-500 mt-1">
                Used for urgent SMS alerts from workflows
              </p>
            </div>

            <div>
              <label className="block text-sm font-medium text-surface-200 mb-2">
                Email
              </label>
              <input
                type="email"
                value={user.email}
                disabled
                className="input-field opacity-60 cursor-not-allowed"
              />
              <p className="text-xs text-surface-500 mt-1">
                Email cannot be changed
              </p>
            </div>
          </div>

          {/* Account Info */}
          <div className="pt-4 border-t border-surface-800">
            <h3 className="text-sm font-medium text-surface-200 mb-3">Account</h3>
            <div className="card p-4 space-y-3 text-sm">
              <div className="flex justify-between items-center">
                <span className="text-surface-400">User ID</span>
                <div className="flex items-center gap-2">
                  <span className="text-surface-300 font-mono text-xs">
                    {user.id}
                  </span>
                  <button
                    onClick={() => void navigator.clipboard.writeText(user.id)}
                    className="p-1 text-surface-400 hover:text-surface-200 hover:bg-surface-700 rounded transition-colors"
                    title="Copy ID"
                  >
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                    </svg>
                  </button>
                </div>
              </div>
              <div className="flex justify-between">
                <span className="text-surface-400">Sign-in method</span>
                <span className="text-surface-300">Google OAuth</span>
              </div>
            </div>
          </div>
        </div>

        {/* Footer - always visible */}
        <div className="p-6 border-t border-surface-800 space-y-3">
          {error && (
            <p className="text-sm text-red-400 text-center">{error}</p>
          )}
          <button
            onClick={() => void handleSave()}
            disabled={isSaving}
            className="w-full btn-primary disabled:opacity-50"
          >
            {isSaving ? 'Saving...' : 'Save changes'}
          </button>
          <button
            onClick={handleLogout}
            className="w-full flex items-center justify-center gap-2 px-4 py-3 text-red-400 hover:text-red-300 hover:bg-red-500/10 rounded-lg transition-colors font-medium"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
            </svg>
            Sign out
          </button>
        </div>
      </div>
    </>
  );
}
