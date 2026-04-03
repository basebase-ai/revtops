/**
 * User profile management panel (slide-out).
 *
 * Features:
 * - View/edit profile info
 * - Change avatar
 * - Phone verification via modal
 * - Sign out
 */

import { useEffect, useState } from 'react';
import type { UserProfile } from './AppLayout';
import { API_BASE, getAuthenticatedRequestHeaders } from '../lib/api';
import { useUIStore, type UITheme } from '../store/uiStore';
import { Memories } from './Memories';

// ---------------------------------------------------------------------------
// Phone verification modal
// ---------------------------------------------------------------------------

type VerifyStep = 'enter_number' | 'enter_code';

interface PhoneVerifyModalProps {
  userId: string;
  initialNumber: string;
  onVerified: (phone: string) => void;
  onClose: () => void;
}

function PhoneVerifyModal({ userId, initialNumber, onVerified, onClose }: PhoneVerifyModalProps): JSX.Element {
  const [step, setStep] = useState<VerifyStep>('enter_number');
  const [phone, setPhone] = useState(initialNumber);
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [normalizedPhone, setNormalizedPhone] = useState('');

  const handleSendCode = async (): Promise<void> => {
    const raw: string = phone.trim().replace(/[\s\-().]/g, '');
    if (!raw) return;
    const e164: string = raw.startsWith('+') ? raw : `+1${raw}`;
    if (!/^\+\d{10,15}$/.test(e164)) {
      setError('Enter a valid phone number (e.g. +14155551234)');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const authHeaders: Record<string, string> = await getAuthenticatedRequestHeaders();
      const saveRes = await fetch(`${API_BASE}/auth/me?user_id=${userId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', ...authHeaders },
        body: JSON.stringify({ phone_number: e164 }),
      });
      if (!saveRes.ok) {
        const d = await saveRes.json().catch(() => ({})) as { detail?: string };
        throw new Error(d.detail ?? 'Failed to save number');
      }
      const res = await fetch(`${API_BASE}/auth/me/request-phone-verification`, {
        method: 'POST',
        headers: authHeaders,
      });
      const data = await res.json().catch(() => ({})) as { detail?: string };
      if (!res.ok) throw new Error(data.detail ?? 'Failed to send code');
      setNormalizedPhone(e164);
      setStep('enter_code');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong');
    } finally {
      setBusy(false);
    }
  };

  const handleVerify = async (): Promise<void> => {
    if (!code.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const verifyHeaders: Record<string, string> = await getAuthenticatedRequestHeaders();
      const res = await fetch(`${API_BASE}/auth/me/verify-phone`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...verifyHeaders },
        body: JSON.stringify({ code: code.trim() }),
      });
      const data = await res.json().catch(() => ({})) as { detail?: string };
      if (!res.ok) throw new Error(data.detail ?? 'Verification failed');
      onVerified(normalizedPhone);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Verification failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="fixed inset-0 bg-black/60 z-[60]" onClick={onClose} />
      <div className="fixed inset-0 z-[61] flex items-center justify-center p-4">
        <div
          className="bg-surface-900 border border-surface-700 rounded-xl shadow-2xl w-full max-w-sm p-6 space-y-5"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between">
            <h3 className="text-base font-semibold text-surface-100">
              {step === 'enter_number' ? 'Add phone number' : 'Enter verification code'}
            </h3>
            <button onClick={onClose} className="p-1 text-surface-400 hover:text-surface-200 rounded">
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          {step === 'enter_number' && (
            <div className="space-y-3">
              <p className="text-sm text-surface-400">
                We&apos;ll text a verification code to this number.
              </p>
              <input
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+1 415-555-1234"
                className="input-field"
                maxLength={30}
                autoFocus
              />
              {error && <p className="text-sm text-red-400">{error}</p>}
              <button
                onClick={() => void handleSendCode()}
                disabled={busy || !phone.trim()}
                className="w-full btn-primary disabled:opacity-50"
              >
                {busy ? 'Sending…' : 'Send code'}
              </button>
            </div>
          )}

          {step === 'enter_code' && (
            <div className="space-y-3">
              <p className="text-sm text-surface-400">
                Enter the 6-digit code we sent to <span className="text-surface-200 font-mono">{phone.trim()}</span>
              </p>
              <input
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                placeholder="000000"
                className="input-field text-center tracking-[0.3em] text-lg font-mono"
                maxLength={6}
                autoFocus
              />
              {error && <p className="text-sm text-red-400">{error}</p>}
              <button
                onClick={() => void handleVerify()}
                disabled={busy || code.length < 4}
                className="w-full btn-primary disabled:opacity-50"
              >
                {busy ? 'Verifying…' : 'Verify'}
              </button>
              <button
                onClick={() => { setStep('enter_number'); setCode(''); setError(null); }}
                className="w-full text-sm text-surface-400 hover:text-surface-200"
              >
                Use a different number
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Profile panel
// ---------------------------------------------------------------------------

interface ProfilePanelProps {
  user: UserProfile;
  onClose: () => void;
  onLogout: () => void;
  onUpdateUser: (updates: Partial<UserProfile>) => void;
}

export function ProfilePanel({ user, onClose, onLogout, onUpdateUser }: ProfilePanelProps): JSX.Element {
  const theme: UITheme = useUIStore((s) => s.theme);
  const setTheme = useUIStore((s) => s.setTheme);
  const [activeTab, setActiveTab] = useState<'profile' | 'memories'>('profile');
  const [name, setName] = useState(user.name ?? '');
  const [jobTitle, setJobTitle] = useState(user.jobTitle ?? '');
  const [smsConsent, setSmsConsent] = useState(user.smsConsent ?? false);
  const [whatsappConsent, setWhatsappConsent] = useState(user.whatsappConsent ?? false);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => { setName(user.name ?? ''); }, [user.name]);
  useEffect(() => { setJobTitle(user.jobTitle ?? ''); }, [user.jobTitle]);
  useEffect(() => { setSmsConsent(user.smsConsent ?? false); }, [user.smsConsent]);
  useEffect(() => { setWhatsappConsent(user.whatsappConsent ?? false); }, [user.whatsappConsent]);
  const [showPhoneModal, setShowPhoneModal] = useState(false);
  const [removePhoneLoading, setRemovePhoneLoading] = useState(false);
  const [newAvatarFile, setNewAvatarFile] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const avatarPreview: string | null = newAvatarFile ?? user.avatarUrl;

  const handleSave = async (): Promise<void> => {
    setIsSaving(true);
    setError(null);
    try {
      const profileHeaders: Record<string, string> = await getAuthenticatedRequestHeaders();
      const response = await fetch(`${API_BASE}/auth/me?user_id=${user.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', ...profileHeaders },
        body: JSON.stringify({
          name: name || null,
          avatar_url: avatarPreview,
          job_title: jobTitle.trim() || null,
          sms_consent: smsConsent,
          whatsapp_consent: whatsappConsent,
        }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(data.detail ?? 'Failed to update profile');
      }
      const updatedUser = await response.json() as {
        name: string | null;
        avatar_url: string | null;
        phone_number: string | null;
        job_title: string | null;
        sms_consent: boolean;
        whatsapp_consent: boolean;
        phone_number_verified: boolean;
      };
      onUpdateUser({
        name: updatedUser.name,
        avatarUrl: updatedUser.avatar_url,
        phoneNumber: updatedUser.phone_number,
        jobTitle: updatedUser.job_title,
        smsConsent: updatedUser.sms_consent,
        whatsappConsent: updatedUser.whatsapp_consent,
        phoneNumberVerified: updatedUser.phone_number_verified,
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

  const handlePhoneVerified = (verifiedPhone: string): void => {
    onUpdateUser({ phoneNumber: verifiedPhone, phoneNumberVerified: true });
    setShowPhoneModal(false);
  };

  const handleRemovePhone = async (): Promise<void> => {
    if (!window.confirm('Remove this phone number? You can add a new one later.')) return;
    setRemovePhoneLoading(true);
    try {
      const removeHeaders: Record<string, string> = await getAuthenticatedRequestHeaders();
      const res = await fetch(`${API_BASE}/auth/me?user_id=${user.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', ...removeHeaders },
        body: JSON.stringify({ phone_number: '' }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({})) as { detail?: string };
        throw new Error(d.detail ?? 'Failed to remove phone');
      }
      setError(null);
      onUpdateUser({ phoneNumber: null, phoneNumberVerified: false });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong');
    } finally {
      setRemovePhoneLoading(false);
    }
  };

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/50 z-40" onClick={onClose} />

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

        {/* Tabs */}
        <div className="flex border-b border-surface-800">
          {(['profile', 'memories'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`flex-1 px-2 py-3 text-sm font-medium transition-colors ${
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
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {activeTab === 'profile' && (
            <>
          {/* Avatar */}
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
                <input type="file" accept="image/*" onChange={handleAvatarChange} className="hidden" />
              </label>
            </div>
            <p className="text-sm text-surface-400 mt-3">Click camera icon to change photo</p>
          </div>

          {/* Form */}
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-surface-200 mb-2">Display name</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Your name"
                className="input-field"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-surface-200 mb-2">Job title</label>
              <input
                type="text"
                value={jobTitle}
                onChange={(e) => setJobTitle(e.target.value)}
                placeholder="e.g. VP of Sales, Account Executive"
                className="input-field"
                maxLength={255}
              />
            </div>

            {/* Email */}
            <div>
              <label className="block text-sm font-medium text-surface-200 mb-2">Email</label>
              <input
                type="email"
                value={user.email}
                disabled
                className="input-field opacity-60 cursor-not-allowed"
              />
              <p className="text-xs text-surface-500 mt-1">Email cannot be changed</p>
            </div>

            {/* Phone number — read-only display */}
            <div>
              <label className="block text-sm font-medium text-surface-200 mb-2">Phone number</label>
              {(user.phoneNumber ?? '').trim() ? (
                <div className="flex items-center gap-2">
                  <span className="text-sm text-surface-200 font-mono">{user.phoneNumber}</span>
                  <span className="inline-flex items-center gap-1 text-xs text-green-500">
                    <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                      <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                    </svg>
                    Verified
                  </span>
                  <button
                    type="button"
                    onClick={() => void handleRemovePhone()}
                    disabled={removePhoneLoading}
                    className="text-sm text-red-400 hover:text-red-300 ml-auto disabled:opacity-50"
                  >
                    {removePhoneLoading ? 'Removing…' : 'Remove'}
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => setShowPhoneModal(true)}
                  className="text-sm text-primary-400 hover:text-primary-300"
                >
                  Add phone number
                </button>
              )}
            </div>

            {/* Notification consent — only shown when a verified phone number exists */}
            {user.phoneNumberVerified && (
              <div className="pt-4 border-t border-surface-800">
                <h3 className="text-sm font-medium text-surface-200 mb-3">Notification preferences</h3>
                <div className="space-y-2.5">
                  <label className="flex items-center gap-3 cursor-pointer group">
                    <input
                      type="checkbox"
                      checked={smsConsent}
                      onChange={(e) => setSmsConsent(e.target.checked)}
                      className="rounded border-surface-600 bg-surface-800 text-primary-500 focus:ring-primary-500"
                    />
                    <span className="text-sm text-surface-300 group-hover:text-surface-200">
                      I agree to receive SMS from Basebase
                    </span>
                  </label>
                  <label className="flex items-center gap-3 cursor-pointer group">
                    <input
                      type="checkbox"
                      checked={whatsappConsent}
                      onChange={(e) => setWhatsappConsent(e.target.checked)}
                      className="rounded border-surface-600 bg-surface-800 text-primary-500 focus:ring-primary-500"
                    />
                    <span className="text-sm text-surface-300 group-hover:text-surface-200">
                      I agree to receive WhatsApp messages from Basebase
                    </span>
                  </label>
                </div>
              </div>
            )}

            {/* Appearance */}
            <div className="pt-4 border-t border-surface-800">
              <h3 className="text-sm font-medium text-surface-200 mb-1">Appearance</h3>
              <p className="text-xs text-surface-500 mb-3">Choose how Basebase looks on this device.</p>
              <div
                className="flex rounded-lg border border-surface-700 p-0.5 bg-surface-800/50"
                role="group"
                aria-label="Color theme"
              >
                {(['light', 'dark', 'system'] as const).map((option) => (
                  <button
                    key={option}
                    type="button"
                    onClick={() => setTheme(option)}
                    className={`flex-1 px-2 py-2 text-xs font-medium rounded-md transition-colors ${
                      theme === option
                        ? 'bg-surface-700 text-surface-100 shadow-sm'
                        : 'text-surface-400 hover:text-surface-200'
                    }`}
                  >
                    {option === 'system'
                      ? 'System'
                      : `${option.charAt(0).toUpperCase()}${option.slice(1)}`}
                  </button>
                ))}
              </div>
            </div>

          </div>

          {/* Account Info */}
          <div className="pt-4 border-t border-surface-800">
            <h3 className="text-sm font-medium text-surface-200 mb-3">Account</h3>
            <div className="card p-4 space-y-3 text-sm">
              <div className="flex justify-between items-center">
                <span className="text-surface-400">User ID</span>
                <div className="flex items-center gap-2">
                  <span className="text-surface-300 font-mono text-xs">{user.id}</span>
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
            </>
          )}

          {activeTab === 'memories' && (
            <div className="-m-6 h-full">
              <Memories />
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-6 border-t border-surface-800 space-y-3">
          {error && <p className="text-sm text-red-400 text-center">{error}</p>}
          {activeTab === 'profile' && (
            <button
              onClick={() => void handleSave()}
              disabled={isSaving}
              className="w-full btn-primary disabled:opacity-50"
            >
              {isSaving ? 'Saving...' : 'Save changes'}
            </button>
          )}
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

      {/* Phone verify modal */}
      {showPhoneModal && (
        <PhoneVerifyModal
          userId={user.id}
          initialNumber={user.phoneNumber ?? ''}
          onVerified={handlePhoneVerified}
          onClose={() => setShowPhoneModal(false)}
        />
      )}
    </>
  );
}
