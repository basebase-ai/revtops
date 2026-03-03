/**
 * Multi-step onboarding wizard for new users with no organization.
 * Steps: Welcome (name + website) → Slack → Data sources → Invite teammates → Free plan → Success
 */

import { useState, useEffect } from 'react';
import Nango from '@nangohq/frontend';
import type { IconType } from 'react-icons';
import { SiSlack, SiHubspot, SiSalesforce, SiGmail, SiGooglecalendar, SiZoom } from 'react-icons/si';
import { HiGlobeAlt, HiUserGroup, HiDeviceMobile, HiLightningBolt } from 'react-icons/hi';
import { APP_NAME } from '../lib/brand';
import { API_BASE } from '../lib/api';
import { supabase } from '../lib/supabase';
import { useAppStore, useIntegrations } from '../store';
import { useIsMobile } from '../hooks';

const TOTAL_STEPS = 6;

interface OnboardingWizardProps {
  emailDomain: string;
  onComplete: () => void;
  onBack: () => void;
}

const INTEGRATION_KEYS_STEP3: ReadonlyArray<string> = [
  'hubspot',
  'salesforce',
  'slack',
  'gmail',
  'google_calendar',
  'zoom',
  'web_search',
  'code_sandbox',
  'twilio',
];

const INTEGRATION_CONFIG: Record<string, { name: string; description: string; icon: string; color: string }> = {
  hubspot: { name: 'HubSpot', description: 'CRM data', icon: 'hubspot', color: 'from-orange-500 to-orange-600' },
  salesforce: { name: 'Salesforce', description: 'CRM', icon: 'salesforce', color: 'from-blue-500 to-blue-600' },
  slack: { name: 'Slack', description: 'Team messages', icon: 'slack', color: 'from-purple-500 to-purple-600' },
  gmail: { name: 'Gmail', description: 'Email', icon: 'gmail', color: 'from-red-500 to-red-600' },
  google_calendar: { name: 'Google Calendar', description: 'Meetings', icon: 'google_calendar', color: 'from-green-500 to-green-600' },
  zoom: { name: 'Zoom', description: 'Meetings', icon: 'zoom', color: 'from-blue-400 to-blue-500' },
  web_search: { name: 'Web Search', description: 'Search the web', icon: 'globe', color: 'from-emerald-500 to-teal-600' },
  code_sandbox: { name: 'Code Sandbox', description: 'Run scripts', icon: 'terminal', color: 'from-amber-500 to-orange-600' },
  twilio: { name: 'Twilio', description: 'Send SMS', icon: 'sms', color: 'from-red-500 to-pink-600' },
};

const BUILTIN_CONNECTORS = new Set<string>(['web_search', 'code_sandbox', 'twilio']);

const ICON_MAP: Record<string, IconType> = {
  hubspot: SiHubspot,
  salesforce: SiSalesforce,
  slack: SiSlack,
  gmail: SiGmail,
  google_calendar: SiGooglecalendar,
  zoom: SiZoom,
  globe: HiGlobeAlt,
  terminal: HiLightningBolt,
  sms: HiDeviceMobile,
};

function generateUUID(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

const SKIP_MESSAGES: Record<number, string> = {
  2: "Without Slack, Penny won't respond in your workspace. You can connect Slack later from Connectors. Skip for now?",
  3: "Fewer connections mean Penny has less context. You can add sources anytime from Connectors. Skip?",
  4: "You can invite teammates later from Organization settings. Skip?",
};

export function OnboardingWizard({ emailDomain, onComplete, onBack }: OnboardingWizardProps): JSX.Element {
  const [step, setStep] = useState<number>(1);
  const [orgName, setOrgName] = useState<string>('');
  const [websiteUrl, setWebsiteUrl] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [connectingProvider, setConnectingProvider] = useState<string | null>(null);
  const [inviteEmail, setInviteEmail] = useState<string>('');
  const [isInviting, setIsInviting] = useState<boolean>(false);
  const [invitedEmails, setInvitedEmails] = useState<ReadonlyArray<string>>([]);
  const [companySummary, setCompanySummary] = useState<string | null>(null);
  const [companySummaryLoading, setCompanySummaryLoading] = useState<boolean>(false);

  const { user, organization, setOrganization, syncUserToBackend, fetchUserOrganizations, fetchIntegrations } =
    useAppStore();
  const integrations = useIntegrations();
  const isMobile = useIsMobile();

  const orgId: string | null = organization?.id ?? null;
  const userId: string | null = user?.id ?? null;

  useEffect(() => {
    if (orgId && userId && step >= 2) {
      void fetchIntegrations();
    }
  }, [orgId, userId, step, fetchIntegrations]);

  useEffect(() => {
    if (step !== 6 || !orgId || !userId) return;
    let cancelled = false;
    let retryTid: ReturnType<typeof setTimeout> | null = null;
    const loadOrg = async (): Promise<void> => {
      setCompanySummaryLoading(true);
      try {
        const { data: { session } } = await supabase.auth.getSession();
        const headers: Record<string, string> = { 'Content-Type': 'application/json' };
        if (session?.access_token) headers['Authorization'] = `Bearer ${session.access_token}`;
        const res = await fetch(`${API_BASE}/auth/organizations/${orgId}?user_id=${encodeURIComponent(userId)}`, {
          headers,
        });
        if (!res.ok || cancelled) return;
        const data = (await res.json()) as { company_summary?: string | null };
        if (data.company_summary) {
          setCompanySummary(data.company_summary);
          setCompanySummaryLoading(false);
          return;
        }
        if (websiteUrl.trim() && !cancelled) {
          retryTid = setTimeout(() => void loadOrg(), 3000);
          return;
        }
      } catch {
        // Ignore
      }
      if (!cancelled) setCompanySummaryLoading(false);
    };
    void loadOrg();
    return () => {
      cancelled = true;
      if (retryTid) clearTimeout(retryTid);
    };
  }, [step, orgId, userId, websiteUrl]);

  const slackConnected: boolean =
    integrations.some((i) => i.provider === 'slack' && i.currentUserConnected) ?? false;

  const suggestedName: string = emailDomain
    .replace(/\.(com|co|io|org|net|ai|app|dev|xyz)(\.[a-z]{2})?$/i, '')
    .split(/[.-]/)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');

  const handleStep1Submit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    if (!orgName.trim() || !user) return;
    setLoading(true);
    setError(null);
    const companyId: string = generateUUID();
    try {
      const { data: { session } } = await supabase.auth.getSession();
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (session?.access_token) headers['Authorization'] = `Bearer ${session.access_token}`;
      const response = await fetch(`${API_BASE}/auth/organizations`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          id: companyId,
          name: orgName.trim(),
          email_domain: emailDomain,
          website_url: websiteUrl.trim() || undefined,
        }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `Failed to create organization: ${response.status}`);
      }
      const data = (await response.json()) as { id: string; name: string; logo_url: string | null };
      setOrganization({ id: data.id, name: data.name, logoUrl: data.logo_url ?? null });
      await syncUserToBackend();
      await fetchUserOrganizations();
      // Fire-and-forget: trigger company research workflow if website URL provided
      const urlTrimmed: string = websiteUrl.trim();
      if (urlTrimmed && user?.id) {
        void (async (): Promise<void> => {
          try {
            const { data: { session } } = await supabase.auth.getSession();
            const headers: Record<string, string> = { 'Content-Type': 'application/json' };
            if (session?.access_token) headers['Authorization'] = `Bearer ${session.access_token}`;
            const wfRes = await fetch(`${API_BASE}/workflows/${data.id}?enabled_only=true`, { headers });
            if (!wfRes.ok) return;
            const wfData = (await wfRes.json()) as { workflows: Array<{ id: string; name: string }> };
            const researchWf = wfData.workflows?.find((w) => w.name === 'Company Research');
            if (!researchWf) return;
            await fetch(`${API_BASE}/workflows/${data.id}/${researchWf.id}/trigger`, {
              method: 'POST',
              headers,
              body: JSON.stringify({
                user_id: user.id,
                trigger_data: {
                  website_url: urlTrimmed,
                  organization_id: data.id,
                  organization_name: orgName.trim(),
                },
              }),
            });
          } catch {
            // Ignore - fire-and-forget
          }
        })();
      }
      setStep(2);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
    } finally {
      setLoading(false);
    }
  };

  const handleConnect = async (provider: string): Promise<void> => {
    if (connectingProvider || !orgId || !userId) return;
    setConnectingProvider(provider);
    try {
      if (BUILTIN_CONNECTORS.has(provider)) {
        const { data: { session } } = await supabase.auth.getSession();
        const headers: Record<string, string> = { 'Content-Type': 'application/json' };
        if (session?.access_token) headers['Authorization'] = `Bearer ${session.access_token}`;
        const res = await fetch(`${API_BASE}/auth/integrations/connect-builtin`, {
          method: 'POST',
          headers,
          body: JSON.stringify({
            organization_id: orgId,
            provider,
            user_id: userId,
          }),
        });
        if (!res.ok) {
          const err = (await res.json().catch(() => ({}))) as { detail?: string };
          throw new Error(err.detail ?? 'Failed to connect');
        }
        void fetchIntegrations();
        setConnectingProvider(null);
        return;
      }
      const params = new URLSearchParams({ organization_id: orgId, user_id: userId });
      const response = await fetch(`${API_BASE}/auth/connect/${provider}/session?${params.toString()}`);
      if (!response.ok) throw new Error('Failed to get session token');
      const sessionData = (await response.json()) as { session_token: string; connection_id: string };
      const { session_token, connection_id } = sessionData;

      const nango = new Nango();
      nango.openConnectUI({
        sessionToken: session_token,
        onEvent: async (event) => {
          const eventType = (event as { type?: string }).type as string;
          if (eventType === 'connect' || eventType === 'connection-created' || eventType === 'success') {
            const eventData = event as { connectionId?: string; connection_id?: string; payload?: { connectionId?: string } };
            const nangoConnectionId =
              eventData.connectionId ?? eventData.connection_id ?? eventData.payload?.connectionId ?? connection_id;
            try {
              const { data: { session } } = await supabase.auth.getSession();
              const headers: Record<string, string> = { 'Content-Type': 'application/json' };
              if (session?.access_token) headers['Authorization'] = `Bearer ${session.access_token}`;
              await fetch(`${API_BASE}/auth/integrations/confirm`, {
                method: 'POST',
                headers,
                body: JSON.stringify({
                  provider,
                  connection_id: nangoConnectionId,
                  organization_id: orgId,
                  user_id: userId,
                }),
              });
              void fetchIntegrations();
            } catch {
              // ignore
            }
            setConnectingProvider(null);
          }
          if (eventType === 'close' || eventType === 'closed') {
            setConnectingProvider(null);
          }
        },
      });
    } catch {
      setConnectingProvider(null);
    }
  };

  const handleInvite = async (): Promise<void> => {
    const email: string = inviteEmail.trim().toLowerCase();
    if (!email || !orgId || !userId) return;
    setIsInviting(true);
    try {
      const { data: { session } } = await supabase.auth.getSession();
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (session?.access_token) headers['Authorization'] = `Bearer ${session.access_token}`;
      const response = await fetch(
        `${API_BASE}/auth/organizations/${orgId}/invitations?user_id=${userId}`,
        {
          method: 'POST',
          headers,
          body: JSON.stringify({ email }),
        }
      );
      if (!response.ok) {
        const errData = (await response.json().catch(() => ({}))) as { detail?: string };
        alert(errData.detail ?? `Failed to invite: ${response.status}`);
        return;
      }
      setInvitedEmails((prev) => [...prev, email]);
      setInviteEmail('');
    } catch (err) {
      alert(`Failed to invite: ${err instanceof Error ? err.message : 'Unknown error'}`);
    } finally {
      setIsInviting(false);
    }
  };

  const handleSkip = (): void => {
    const msg: string | undefined = SKIP_MESSAGES[step];
    if (msg && window.confirm(msg)) {
      setStep((prev) => Math.min(prev + 1, TOTAL_STEPS));
    }
  };

  const handleNext = (): void => {
    if (step < TOTAL_STEPS) setStep((prev) => prev + 1);
    else onComplete();
  };

  const renderFooter = (): JSX.Element => (
    <div className="mt-8 space-y-4">
      {SKIP_MESSAGES[step] !== undefined && (
        <button
          type="button"
          onClick={handleSkip}
          className="text-sm text-surface-500 hover:text-surface-400"
        >
          Skip
        </button>
      )}
      {step === 1 ? null : (
        <button
          type="button"
          onClick={handleNext}
          className="w-full btn-primary py-3 text-base font-medium"
        >
          {step === 5 ? 'Get started for free' : step === 6 ? 'Go to app' : 'Next'}
        </button>
      )}
      <p className="text-center text-surface-500 text-xs">
        Step {step} of {TOTAL_STEPS}
      </p>
    </div>
  );

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-1/2 -right-1/4 w-[800px] h-[800px] rounded-full bg-gradient-to-br from-primary-600/20 to-transparent blur-3xl" />
        <div className="absolute -bottom-1/4 -left-1/4 w-[600px] h-[600px] rounded-full bg-gradient-to-tr from-primary-600/10 to-transparent blur-3xl" />
      </div>

      <div className="relative z-10 w-full max-w-md">
        <button
          onClick={onBack}
          className="flex items-center gap-2 text-surface-400 hover:text-surface-200 transition-colors mb-8"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          Sign out
        </button>

        <div className="bg-surface-900/80 backdrop-blur-sm border border-surface-800 rounded-2xl p-8">
          {/* Step 1: Welcome - name + website */}
          {step === 1 && (
            <>
              <div className="text-center mb-6">
                <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gradient-to-br from-primary-500 to-primary-700 mb-4">
                  <svg className="w-7 h-7 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                  </svg>
                </div>
                <h1 className="text-2xl font-bold text-surface-50">Welcome to {APP_NAME}</h1>
                <p className="text-surface-400 mt-2">
                  You're the first from <span className="text-primary-400 font-medium">@{emailDomain}</span>
                </p>
              </div>
              <form onSubmit={(e) => void handleStep1Submit(e)} className="space-y-4">
                <div>
                  <label htmlFor="orgName" className="block text-sm font-medium text-surface-300 mb-2">
                    Organization / company name
                  </label>
                  <input
                    id="orgName"
                    type="text"
                    value={orgName}
                    onChange={(e) => setOrgName(e.target.value)}
                    className="input"
                    placeholder={suggestedName || 'Acme Corporation'}
                    autoFocus
                  />
                </div>
                <div>
                  <label htmlFor="websiteUrl" className="block text-sm font-medium text-surface-300 mb-2">
                    Website URL
                  </label>
                  <input
                    id="websiteUrl"
                    type="url"
                    value={websiteUrl}
                    onChange={(e) => setWebsiteUrl(e.target.value)}
                    className="input"
                    placeholder="https://company.com"
                  />
                </div>
                {error && (
                  <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
                    {error}
                  </div>
                )}
                <button
                  type="submit"
                  disabled={loading || !orgName.trim()}
                  className="btn-primary w-full py-3.5 disabled:opacity-50"
                >
                  {loading ? (
                    <span className="inline-flex items-center justify-center gap-2">
                      <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                      Setting up...
                    </span>
                  ) : (
                    'Continue'
                  )}
                </button>
              </form>
            </>
          )}

          {/* Step 2: Connect Slack */}
          {step === 2 && (
            <>
              <div className="text-center mb-6">
                <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gradient-to-br from-purple-500 to-purple-700 mb-4">
                  <SiSlack className="w-7 h-7 text-white" />
                </div>
                <h2 className="text-xl font-bold text-surface-50">Connect your Slack workspace</h2>
                <p className="text-surface-400 mt-2 text-sm">
                  Penny will respond in channels and DMs. Invite @Penny to channels to get started.
                </p>
              </div>
              {slackConnected ? (
                <div className="flex items-center gap-3 p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-400">
                  <svg className="w-5 h-5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  <span>Slack connected!</span>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => void handleConnect('slack')}
                  disabled={connectingProvider !== null || isMobile}
                  className="w-full flex items-center justify-center gap-3 p-4 rounded-xl border border-surface-700 bg-surface-800 hover:bg-surface-700 disabled:opacity-50 transition-colors"
                >
                  {connectingProvider === 'slack' ? (
                    <div className="w-5 h-5 border-2 border-primary-400 border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <SiSlack className="w-6 h-6 text-white" />
                  )}
                  {isMobile ? 'Use desktop to connect' : connectingProvider === 'slack' ? 'Connecting...' : 'Connect Slack'}
                </button>
              )}
              {renderFooter()}
            </>
          )}

          {/* Step 3: More data sources */}
          {step === 3 && (
            <>
              <div className="mb-6">
                <h2 className="text-xl font-bold text-surface-50">Connect more data sources</h2>
                <p className="text-surface-400 mt-2 text-sm">
                  Connect your CRM, calendar, and other tools so Penny can answer questions across your data.
                </p>
              </div>
              <div className="grid grid-cols-2 gap-3 max-h-64 overflow-y-auto">
                {INTEGRATION_KEYS_STEP3.map((key) => {
                  const config = INTEGRATION_CONFIG[key];
                  if (!config) return null;
                  const Icon = ICON_MAP[config.icon] ?? HiGlobeAlt;
                  const connected = integrations.some((i) => i.provider === key && i.currentUserConnected);
                  const isConnecting = connectingProvider === key;
                  return (
                    <button
                      key={key}
                      type="button"
                      onClick={() => void handleConnect(key)}
                      disabled={isConnecting || isMobile}
                      className={`flex items-center gap-3 p-3 rounded-xl border text-left transition-colors ${
                        connected
                          ? 'border-emerald-500/30 bg-emerald-500/10'
                          : 'border-surface-700 bg-surface-800 hover:bg-surface-700'
                      } disabled:opacity-50`}
                    >
                      <div
                        className={`p-2 rounded-lg bg-gradient-to-br ${config.color} text-white flex-shrink-0`}
                      >
                        <Icon className="w-4 h-4" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="font-medium text-surface-100 truncate">{config.name}</div>
                        <div className="text-xs text-surface-500 truncate">{config.description}</div>
                      </div>
                      {connected && (
                        <svg className="w-4 h-4 text-emerald-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                        </svg>
                      )}
                      {isConnecting && (
                        <div className="w-4 h-4 border-2 border-primary-400 border-t-transparent rounded-full animate-spin flex-shrink-0" />
                      )}
                    </button>
                  );
                })}
              </div>
              {renderFooter()}
            </>
          )}

          {/* Step 4: Invite teammates */}
          {step === 4 && (
            <>
              <div className="mb-6">
                <h2 className="text-xl font-bold text-surface-50">Invite your teammates</h2>
                <p className="text-surface-400 mt-2 text-sm">
                  Invite teammates to collaborate and share Penny.
                </p>
              </div>
              <div className="flex gap-2 mb-4">
                <input
                  type="email"
                  placeholder="colleague@company.com"
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), void handleInvite())}
                  className="input flex-1"
                />
                <button
                  type="button"
                  onClick={() => void handleInvite()}
                  disabled={isInviting || !inviteEmail.trim()}
                  className="btn-primary px-4 disabled:opacity-50"
                >
                  {isInviting ? 'Sending...' : 'Send'}
                </button>
              </div>
              {invitedEmails.length > 0 && (
                <div className="space-y-2 mb-4">
                  {invitedEmails.map((email) => (
                    <div key={email} className="flex items-center gap-2 text-sm text-surface-300">
                      <svg className="w-4 h-4 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      Invited {email}
                    </div>
                  ))}
                </div>
              )}
              {renderFooter()}
            </>
          )}

          {/* Step 5: Free plan */}
          {step === 5 && (
            <>
              <div className="text-center mb-6">
                <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-primary-500 to-primary-700 mb-6">
                  <svg className="w-8 h-8 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <h2 className="text-2xl font-bold text-white">Your plan</h2>
                <p className="text-surface-400 mt-1">100 credits/month — Get started for free</p>
              </div>
              <div className="space-y-3 mb-6">
                <div className="flex items-center gap-3 text-surface-300">
                  <svg className="w-5 h-5 text-primary-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  <span>Connect your CRM and Slack</span>
                </div>
                <div className="flex items-center gap-3 text-surface-300">
                  <svg className="w-5 h-5 text-primary-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  <span>Invite your team</span>
                </div>
                <div className="flex items-center gap-3 text-surface-300">
                  <svg className="w-5 h-5 text-primary-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  <span>Upgrade anytime for more credits</span>
                </div>
              </div>
              {renderFooter()}
            </>
          )}

          {/* Step 6: Success */}
          {step === 6 && (
            <>
              <div className="text-center mb-6">
                <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-emerald-500 to-emerald-700 mb-6">
                  <svg className="w-8 h-8 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <h2 className="text-2xl font-bold text-white">You&apos;re all set!</h2>
              </div>
              {companySummary ? (
                <div className="mb-6 p-4 rounded-xl bg-primary-500/10 border border-primary-500/20">
                  <p className="text-xs font-medium text-primary-400 uppercase tracking-wider mb-2">
                    From Penny
                  </p>
                  <p className="text-surface-200 text-[15px] leading-relaxed">
                    {companySummary}
                  </p>
                </div>
              ) : companySummaryLoading ? (
                <div className="mb-6 p-4 rounded-xl bg-surface-800/50 border border-surface-700">
                  <p className="text-surface-400 text-sm italic">
                    Penny is researching your company…
                  </p>
                </div>
              ) : null}
              <p className="text-surface-400 mb-4">Here&apos;s what you can do now:</p>
              <div className="space-y-3 mb-6">
                <div className="flex items-center gap-3 text-surface-300">
                  <HiGlobeAlt className="w-5 h-5 text-primary-500 flex-shrink-0" />
                  <span>Chat with Penny in Slack — @mention her in any channel she's in</span>
                </div>
                <div className="flex items-center gap-3 text-surface-300">
                  <HiUserGroup className="w-5 h-5 text-primary-500 flex-shrink-0" />
                  <span>Ask questions here in the {APP_NAME} web app</span>
                </div>
              </div>
              <button
                type="button"
                onClick={onComplete}
                className="w-full btn-primary py-3 text-base font-medium"
              >
                Go to app
              </button>
              <p className="text-center text-surface-500 text-xs mt-4">
                Step {step} of {TOTAL_STEPS}
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
