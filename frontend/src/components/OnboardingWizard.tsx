/**
 * Multi-step onboarding wizard for new users with no organization.
 * Steps: Welcome (name + website) → Slack → Data sources → Invite teammates → Free plan → Success
 */

import React, { useState, useEffect } from 'react';
import Nango from '@nangohq/frontend';
import type { IconType } from 'react-icons';
import { SiHubspot, SiSalesforce, SiSlack, SiGmail, SiGooglecalendar, SiZoom } from 'react-icons/si';
import { HiGlobeAlt, HiUserGroup, HiDeviceMobile, HiLightningBolt } from 'react-icons/hi';

import { API_BASE, getAuthenticatedRequestHeaders } from '../lib/api';
import { getDomainFromUrl } from '../lib/email';
import { supabase } from '../lib/supabase';
import { useAppStore, useIntegrations } from '../store';
import { useIsMobile } from '../hooks';

const TOTAL_STEPS_NORMAL = 6;
const TOTAL_STEPS_INVITED = 5;

interface TeamMember {
  id: string;
  name: string | null;
  email: string;
  avatar_url: string | null;
  role: string | null;
  status: string | null;
  is_guest: boolean;
}

interface OnboardingWizardProps {
  emailDomain: string;
  isInvitedMode?: boolean;
  isCreatingNewOrg?: boolean;
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

/** Official Slack multi-color logo */
const SLACK_LOGO_PATH = '/slack-logo.png';

const SlackLogo = ({ className }: { className?: string }): JSX.Element => (
  <img src={SLACK_LOGO_PATH} alt="Slack" className={`${className ?? ''} object-contain`} />
);

const ICON_MAP: Record<string, IconType | React.ComponentType<{ className?: string }>> = {
  hubspot: SiHubspot,
  salesforce: SiSalesforce,
  slack: SlackLogo,
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
  2: "Without Slack, Basebase won't respond in your workspace. You can connect Slack later from Connectors. Skip for now?",
  3: "Fewer connections mean Basebase has less context. You can add sources anytime from Connectors. Skip?",
  4: "You can invite teammates later from Team settings. Skip?",
};

export function OnboardingWizard({ emailDomain, isInvitedMode = false, isCreatingNewOrg = false, onComplete: rawOnComplete, onBack }: OnboardingWizardProps): JSX.Element {
  const TOTAL_STEPS: number = isInvitedMode ? TOTAL_STEPS_INVITED : TOTAL_STEPS_NORMAL;

  const { user, organization, organizations, setOrganization, setIntegrations, syncUserToBackend, fetchUserOrganizations, fetchIntegrations, switchActiveOrganization } =
    useAppStore();

  const orgId: string | null = organization?.id ?? null;

  const onComplete = async (): Promise<void> => {
    if (orgId) {
      try {
        const { data: { session } } = await supabase.auth.getSession();
        const headers: Record<string, string> = { 'Content-Type': 'application/json' };
        if (session?.access_token) headers['Authorization'] = `Bearer ${session.access_token}`;
        await fetch(`${API_BASE}/auth/organizations/${orgId}/complete-onboarding`, {
          method: 'POST',
          headers,
        });
      } catch (err) {
        console.error('Failed to complete onboarding:', err);
      }
    }
    localStorage.removeItem('onboarding_step');
    rawOnComplete();
  };
  const [step, setStep] = useState<number>(() => {
    const saved: string | null = localStorage.getItem('onboarding_step');
    const parsed: number = saved ? parseInt(saved, 10) : 1;
    return parsed >= 1 && parsed <= TOTAL_STEPS ? parsed : 1;
  });
  const [orgName, setOrgName] = useState<string>('');
  const [websiteUrl, setWebsiteUrl] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [connectingProvider, setConnectingProvider] = useState<string | null>(null);
  const [showCodeSandboxWarning, setShowCodeSandboxWarning] = useState<boolean>(false);
  const [inviteEmail, setInviteEmail] = useState<string>('');
  const [isInviting, setIsInviting] = useState<boolean>(false);
  const [invitedEmails, setInvitedEmails] = useState<ReadonlyArray<string>>([]);
  const [companySummary, setCompanySummary] = useState<string | null>(null);
  const [companySummaryLoading, setCompanySummaryLoading] = useState<boolean>(false);
  const [teamMembers, setTeamMembers] = useState<ReadonlyArray<TeamMember>>([]);

  const integrations = useIntegrations();
  const isMobile = useIsMobile();

  const userId: string | null = user?.id ?? null;
  const activeMembership = organizations.find((org) => org.id === orgId);
  const canConnectCodeSandbox = (user?.roles.includes('global_admin') ?? false) || activeMembership?.role === 'admin';

  useEffect(() => {
    localStorage.setItem('onboarding_step', String(step));
  }, [step]);

  // Fetch team members for invited mode step 1
  useEffect(() => {
    if (!isInvitedMode || !orgId || !userId) return;
    let cancelled = false;
    const loadMembers = async (): Promise<void> => {
      try {
        const { data: { session } } = await supabase.auth.getSession();
        const headers: Record<string, string> = {};
        if (session?.access_token) headers['Authorization'] = `Bearer ${session.access_token}`;
        const res = await fetch(
          `${API_BASE}/auth/organizations/${orgId}/members?user_id=${encodeURIComponent(userId)}`,
          { headers },
        );
        if (!res.ok || cancelled) return;
        const data = (await res.json()) as { members: TeamMember[] };
        if (!cancelled) {
          setTeamMembers(data.members.filter((m) => m.id !== userId && m.status === 'active' && !m.is_guest));
        }
      } catch {
        // ignore
      }
    };
    void loadMembers();
    return () => { cancelled = true; };
  }, [isInvitedMode, orgId, userId]);

  useEffect(() => {
    if (orgId && userId && (isInvitedMode ? step >= 1 : step >= 2)) {
      void fetchIntegrations();
    }
  }, [orgId, userId, step, fetchIntegrations, isInvitedMode]);

  useEffect(() => {
    if (contentStep !== 6 || !orgId || !userId) return;
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, orgId, userId, websiteUrl]);

  const slackConnected: boolean =
    integrations.some((i) => i.provider === 'slack' && i.currentUserConnected) ?? false;

  const slackSatisfied: boolean = integrations.some(
    (i) => i.provider === 'slack' && (i.currentUserConnected || (i.scope === 'organization' && i.isActive))
  );

  const suggestedName: string = (getDomainFromUrl(websiteUrl.trim()) || emailDomain)
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
      const urlTrimmed: string = websiteUrl.trim();
      const domainFromUrl: string = getDomainFromUrl(urlTrimmed);
      const effectiveEmailDomain: string = domainFromUrl || emailDomain;

      const response = await fetch(`${API_BASE}/auth/organizations`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          id: companyId,
          name: orgName.trim(),
          email_domain: effectiveEmailDomain,
          website_url: urlTrimmed || undefined,
          allow_duplicate_domain: isCreatingNewOrg,
        }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `Failed to create team: ${response.status}`);
      }
      const data = (await response.json()) as { id: string; name: string; logo_url: string | null; handle?: string | null };
      const orgHandle: string | null = data.handle ?? null;
      setOrganization({ id: data.id, name: data.name, logoUrl: data.logo_url ?? null, handle: orgHandle });
      // Clear integrations so we don't show the previous org's connections (e.g. Slack) as connected
      setIntegrations([]);
      const ONBOARDING_TIMEOUT_MS: number = 45_000;
      const timeoutPromise: Promise<never> = new Promise((_, reject) => {
        setTimeout(
          () => reject(new Error('Request timed out. Please refresh the page — your team was created.')),
          ONBOARDING_TIMEOUT_MS,
        );
      });
      await Promise.race([
        (async (): Promise<void> => {
          await syncUserToBackend();
          await fetchUserOrganizations();
          await switchActiveOrganization(data.id);
        })(),
        timeoutPromise,
      ]);
      const { organization: updatedOrg } = useAppStore.getState();
      const handle: string | null = orgHandle ?? updatedOrg?.handle ?? null;
      const prefix = handle ? `/${handle}` : '';
      window.history.replaceState({}, '', `${prefix}/chat`);
      // Delayed refetch to pick up logo_url once background favicon task completes
      if (urlTrimmed) {
        setTimeout(() => {
          void fetchUserOrganizations().then(() => switchActiveOrganization(data.id));
        }, 3000);
      }
      // Fire-and-forget: trigger company research workflow if website URL provided
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

  const connectProvider = async (provider: string): Promise<void> => {
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
      const connectHeaders = await getAuthenticatedRequestHeaders();
      const response = await fetch(`${API_BASE}/auth/connect/${provider}/session`, {
        headers: connectHeaders,
      });
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

  const handleConnect = async (provider: string): Promise<void> => {
    if (provider === 'code_sandbox') {
      if (!canConnectCodeSandbox) return;
      setShowCodeSandboxWarning(true);
      return;
    }

    await connectProvider(provider);
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

  /**
   * In invited mode, logical step 4 (invite teammates) is skipped.
   * Map step numbers to content: steps 1-3 are the same, then 4→5 and 5→6 in normal numbering.
   */
  const contentStep: number = isInvitedMode && step >= 4 ? step + 1 : step;

  const skipMessageForStep: string | undefined = SKIP_MESSAGES[contentStep];
  const handleSkip = (): void => {
    if (skipMessageForStep && window.confirm(skipMessageForStep)) {
      setStep((prev) => Math.min(prev + 1, TOTAL_STEPS));
    }
  };

  const handleNext = (): void => {
    if (step < TOTAL_STEPS) setStep((prev) => prev + 1);
    else void onComplete();
  };

  const renderFooter = (nextLabel?: string, continueDisabled?: boolean): JSX.Element => {
    const step3HasConnection: boolean = integrations.some((i) =>
      INTEGRATION_KEYS_STEP3.includes(i.provider) && i.currentUserConnected
    );
    const defaultDisabled: boolean =
      contentStep === 2 ? !slackSatisfied : contentStep === 3 ? !step3HasConnection : contentStep === 4 ? invitedEmails.length === 0 : false;
    const isDisabled: boolean = continueDisabled ?? defaultDisabled;
    const showContinue: boolean = isInvitedMode ? step >= 1 && step <= TOTAL_STEPS - 1 : step >= 2 && step <= 5;
    return (
    <div className="mt-8 space-y-3">
      {skipMessageForStep !== undefined && (
        <button
          type="button"
          onClick={handleSkip}
          className="text-sm text-surface-500 hover:text-surface-300 transition-colors"
        >
          I&apos;ll do this later
        </button>
      )}
      {showContinue && (
        <button
          type="button"
          onClick={handleNext}
          disabled={isDisabled}
          className="w-full btn-primary py-3.5 text-base font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {nextLabel ?? 'Continue'}
        </button>
      )}
      <div className="flex justify-center gap-1.5 pt-2">
        {Array.from({ length: TOTAL_STEPS }, (_, i) => (
          <div
            key={i}
            className={`h-1.5 rounded-full transition-all duration-300 ${
              i + 1 <= step
                ? 'w-6 bg-primary-500'
                : 'w-1.5 bg-surface-700'
            }`}
          />
        ))}
      </div>
    </div>
  );
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      {showCodeSandboxWarning && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="w-full max-w-lg rounded-2xl border border-amber-500/30 bg-surface-900 shadow-2xl">
            <div className="border-b border-surface-700/60 p-5">
              <div className="flex items-start gap-3">
                <div className="mt-0.5 rounded-xl bg-amber-500/15 p-2 text-amber-300">
                  <HiLightningBolt className="h-5 w-5" />
                </div>
                <div>
                  <h2 className="text-lg font-semibold text-surface-100">
                    Warning: Code Sandbox can run insecure code
                  </h2>
                  <p className="mt-1 text-sm text-surface-400">
                    This connector can execute arbitrary code and shell commands. If it is abused,
                    it could expose secrets, exfiltrate data, or contribute to a data breach.
                  </p>
                </div>
              </div>
            </div>
            <div className="space-y-4 p-5">
              <div className="rounded-xl border border-amber-500/20 bg-amber-500/10 p-4 text-sm text-amber-100">
                <p className="font-medium text-amber-200">Admin approval required</p>
                <p className="mt-1 text-amber-100/90">
                  Only an organization admin or global admin should connect Code Sandbox. Continue
                  only if you understand the security risk and still want to enable it.
                </p>
              </div>
              <p className="text-sm text-surface-400">
                Use this connector only at your own risk.
              </p>
              <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
                <button
                  type="button"
                  onClick={() => setShowCodeSandboxWarning(false)}
                  className="rounded-lg border border-surface-600 px-4 py-2 text-sm font-medium text-surface-200 transition-colors hover:bg-surface-800"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShowCodeSandboxWarning(false);
                    void connectProvider('code_sandbox');
                  }}
                  className="rounded-lg bg-amber-500 px-4 py-2 text-sm font-semibold text-surface-950 transition-colors hover:bg-amber-400"
                >
                  Connect at my own risk
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-1/3 -right-1/4 w-[900px] h-[900px] rounded-full bg-gradient-to-br from-primary-600/15 via-primary-500/10 to-transparent blur-3xl" />
        <div className="absolute -bottom-1/4 -left-1/4 w-[700px] h-[700px] rounded-full bg-gradient-to-tr from-purple-600/10 to-transparent blur-3xl" />
        <div className="absolute top-1/3 left-1/2 -translate-x-1/2 w-[500px] h-[500px] rounded-full bg-gradient-to-b from-emerald-500/5 to-transparent blur-3xl" />
      </div>

      <div className="relative z-10 w-full max-w-md">
        <button
          onClick={onBack}
          className="flex items-center gap-2 text-surface-500 hover:text-surface-300 transition-colors mb-6 text-sm"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          {isCreatingNewOrg ? 'Cancel' : 'Sign out'}
        </button>

        <div className="bg-surface-900/80 backdrop-blur-sm border border-surface-800 rounded-2xl p-8">
          {/* Step 1: Welcome (normal) or Welcome to [Org] (invited) */}
          {step === 1 && !isInvitedMode && (
            <>
              <div className="text-center mb-8">
                <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-primary-500 to-primary-700 mb-5 shadow-lg shadow-primary-500/20">
                  <span className="text-3xl">&#x1F44B;</span>
                </div>
                <h1 className="text-2xl font-bold text-surface-50 leading-tight">
                  Meet Basebase, your new<br />AI teammate
                </h1>
                <p className="text-surface-300 mt-3 text-[15px] leading-relaxed max-w-sm mx-auto">
                  It finds data across all your tools, manages tasks, runs workflows,
                  and keeps your whole team in the loop — so nobody has to sign into
                  five different apps to get one answer.
                </p>
              </div>
              <form onSubmit={(e) => void handleStep1Submit(e)} className="space-y-4">
                <div>
                  <label htmlFor="orgName" className="block text-sm font-medium text-surface-300 mb-1.5">
                    What&apos;s your company called?
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
                  <label htmlFor="websiteUrl" className="block text-sm font-medium text-surface-300 mb-1.5">
                    Company website
                    <span className="text-surface-500 font-normal ml-1">(so Basebase can learn about you)</span>
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
                  className="btn-primary w-full py-3.5 text-base font-semibold disabled:opacity-50"
                >
                  {loading ? (
                    <span className="inline-flex items-center justify-center gap-2">
                      <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                      Getting things ready...
                    </span>
                  ) : (
                    "Let\u2019s go"
                  )}
                </button>
              </form>
              <div className="flex justify-center gap-1.5 pt-6">
                {Array.from({ length: TOTAL_STEPS }, (_, i) => (
                  <div
                    key={i}
                    className={`h-1.5 rounded-full transition-all duration-300 ${
                      i === 0 ? 'w-6 bg-primary-500' : 'w-1.5 bg-surface-700'
                    }`}
                  />
                ))}
              </div>
            </>
          )}

          {/* Step 1 (invited): Welcome to [Org] */}
          {step === 1 && isInvitedMode && (
            <>
              <div className="text-center mb-6">
                {organization?.logoUrl ? (
                  <img
                    src={organization.logoUrl}
                    alt={organization.name}
                    className="w-16 h-16 rounded-2xl mx-auto mb-5 object-cover shadow-lg"
                  />
                ) : (
                  <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-primary-500 to-primary-700 mb-5 shadow-lg shadow-primary-500/20">
                    <span className="text-3xl">&#x1F44B;</span>
                  </div>
                )}
                <h1 className="text-2xl font-bold text-surface-50 leading-tight">
                  Welcome to {organization?.name ?? 'your team'}!
                </h1>
                <p className="text-surface-300 mt-3 text-[15px] leading-relaxed max-w-sm mx-auto">
                  You&apos;re all set as a member. Let&apos;s connect your tools so Basebase
                  can help you alongside your team.
                </p>
              </div>

              {/* Team members already here */}
              {teamMembers.length > 0 && (
                <div className="mb-4 p-4 rounded-xl bg-surface-800/50 border border-surface-700/50">
                  <p className="text-surface-400 text-xs font-medium mb-3">Your teammates</p>
                  <div className="space-y-2.5">
                    {teamMembers.slice(0, 6).map((member) => (
                      <div key={member.id} className="flex items-center gap-3">
                        {member.avatar_url ? (
                          <img
                            src={member.avatar_url}
                            alt={member.name ?? member.email}
                            className="w-8 h-8 rounded-full"
                          />
                        ) : (
                          <div className="w-8 h-8 rounded-full bg-surface-700 flex items-center justify-center text-surface-300 text-sm font-medium">
                            {(member.name ?? member.email).charAt(0).toUpperCase()}
                          </div>
                        )}
                        <div className="min-w-0 flex-1">
                          <div className="text-sm text-surface-200 font-medium truncate">
                            {member.name ?? member.email}
                          </div>
                          {member.name && (
                            <div className="text-xs text-surface-500 truncate">{member.email}</div>
                          )}
                        </div>
                      </div>
                    ))}
                    {teamMembers.length > 6 && (
                      <p className="text-xs text-surface-500">
                        + {teamMembers.length - 6} more
                      </p>
                    )}
                  </div>
                </div>
              )}

              {/* Integrations already connected by the org */}
              {(() => {
                const orgConnectedProviders: ReadonlyArray<string> = integrations
                  .filter((i) => i.teamConnections.length > 0 || (i.isActive && !i.currentUserConnected))
                  .map((i) => i.provider);
                const uniqueProviders: ReadonlyArray<string> = [...new Set(orgConnectedProviders)];
                if (uniqueProviders.length === 0) return null;
                return (
                  <div className="mb-4 p-4 rounded-xl bg-surface-800/50 border border-surface-700/50">
                    <p className="text-surface-400 text-xs font-medium mb-3">Already connected by your team</p>
                    <div className="flex flex-wrap gap-2">
                      {uniqueProviders.map((provider) => {
                        const config = INTEGRATION_CONFIG[provider];
                        if (!config) return null;
                        const Icon = ICON_MAP[config.icon] ?? HiGlobeAlt;
                        return (
                          <div key={provider} className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-emerald-500/10 border border-emerald-500/20">
                            <Icon className="w-4 h-4 text-emerald-400" />
                            <span className="text-sm text-emerald-300">{config.name}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })()}

              {renderFooter('Get started', false)}
            </>
          )}

          {/* Step 2: Connect Slack */}
          {contentStep === 2 && (
            <>
              <div className="text-center mb-6">
                <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-white mb-4 shadow-lg shadow-surface-900/20">
                  <SlackLogo className="w-8 h-8" />
                </div>
                <h2 className="text-xl font-bold text-surface-50">
                  {slackSatisfied && !slackConnected
                    ? 'Slack is ready'
                    : isInvitedMode ? 'Connect your Slack account' : 'Bring Basebase where your team already works'}
                </h2>
                <p className="text-surface-300 mt-3 text-sm leading-relaxed">
                  {slackSatisfied && !slackConnected
                    ? 'Your team already connected Slack for this team. Basebase is available in any channel the bot has been invited to.'
                    : <>Connect Slack and your team can ask Basebase anything right from the channels they&apos;re
                      already in &mdash; deal updates, meeting prep, customer research &mdash; no tab-switching required.</>
                  }
                </p>
              </div>
              {slackSatisfied && !slackConnected ? (
                <div className="flex items-center gap-3 p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-400">
                  <svg className="w-5 h-5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  <div>
                    <span className="font-medium">Slack connected for your team</span>
                    <span className="block text-sm text-emerald-400/70 mt-0.5">
                      Connected by {integrations.find((i) => i.provider === 'slack')?.teamConnections.map((tc) => tc.userName).join(', ')}
                    </span>
                  </div>
                </div>
              ) : slackConnected ? (
                <div className="flex items-center gap-3 p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-400">
                  <svg className="w-5 h-5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  <span className="font-medium">Slack connected &mdash; Basebase is in your workspace!</span>
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
                    <SiSlack className="w-5 h-5 text-[#FFFFFF]" />
                  )}
                  {isMobile ? 'Use desktop to connect' : connectingProvider === 'slack' ? 'Connecting...' : 'Connect Slack'}
                </button>
              )}
              {renderFooter()}
            </>
          )}

          {/* Step 3: Data sources */}
          {contentStep === 3 && (
            <>
              <div className="mb-6">
                <h2 className="text-xl font-bold text-surface-50">
                  {isInvitedMode ? 'Connect your data sources' : 'Give Basebase superpowers'}
                </h2>
                <p className="text-surface-300 mt-2 text-sm leading-relaxed">
                  {isInvitedMode
                    ? 'Team-wide sources are already set up. Connect your personal accounts (email, calendar) so Basebase has full context.'
                    : 'Now that you can ask Basebase questions directly in Slack, what data sources do you want it to be able to read/write?'
                  }
                </p>
              </div>
              <div className="grid grid-cols-2 gap-3 max-h-64 overflow-y-auto">
                {INTEGRATION_KEYS_STEP3.map((key) => {
                  const config = INTEGRATION_CONFIG[key];
                  if (!config) return null;
                  const matchedIntegration = integrations.find((i) => i.provider === key);
                  if (matchedIntegration?.scope === 'organization' && matchedIntegration.isActive && !matchedIntegration.currentUserConnected) return null;
                  const Icon = ICON_MAP[config.icon] ?? HiGlobeAlt;
                  const connected: boolean = integrations.some((i) => i.provider === key && i.currentUserConnected);
                  const isConnecting: boolean = connectingProvider === key;
                  const orgConnected: boolean = !!matchedIntegration
                    && matchedIntegration.scope === 'organization'
                    && matchedIntegration.isActive
                    && !connected;
                  const teamConnected: boolean = !orgConnected
                    && isInvitedMode
                    && !!matchedIntegration
                    && matchedIntegration.teamConnections.length > 0
                    && !connected;
                  const codeSandboxBlocked: boolean = key === 'code_sandbox' && !canConnectCodeSandbox;
                  const isClickable: boolean = !connected && !orgConnected && !codeSandboxBlocked;
                  return (
                    <button
                      key={key}
                      type="button"
                      onClick={isClickable ? () => void handleConnect(key) : undefined}
                      disabled={!isClickable || isConnecting || isMobile}
                      className={`flex items-center gap-3 p-3 rounded-xl border text-left transition-colors ${
                        connected || orgConnected
                          ? 'border-emerald-500/30 bg-emerald-500/10'
                          : teamConnected
                            ? 'border-blue-500/20 bg-blue-500/5'
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
                        <div className="text-xs text-surface-500 truncate">
                          {connected
                            ? 'Connected'
                            : orgConnected
                              ? `Connected for your team`
                              : codeSandboxBlocked
                                ? 'Admin access required'
                              : teamConnected
                                ? `By ${matchedIntegration!.teamConnections[0]?.userName ?? 'team'}`
                                : config.description
                          }
                        </div>
                      </div>
                      {(connected || orgConnected) && (
                        <svg className="w-4 h-4 text-emerald-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                        </svg>
                      )}
                      {teamConnected && (
                        <svg className="w-4 h-4 text-blue-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
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

          {/* Step 4: Invite teammates (skipped in invited mode) */}
          {contentStep === 4 && !isInvitedMode && (
            <>
              <div className="text-center mb-6">
                <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gradient-to-br from-blue-500 to-indigo-600 mb-4 shadow-lg shadow-blue-500/20">
                  <HiUserGroup className="w-7 h-7 text-white" />
                </div>
                <h2 className="text-xl font-bold text-surface-50">Better together</h2>
                <p className="text-surface-300 mt-3 text-sm leading-relaxed">
                  Invite your teammates and watch the magic happen! Basebase gets smarter with
                  every person who joins. Meeting briefs, deal updates, customer insights
                  &mdash; all synced and accessible. Less chasing, more celebrating together.
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
                  {isInviting ? 'Sending...' : 'Invite'}
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
          {contentStep === 5 && (
            <>
              <div className="text-center mb-6">
                <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-primary-500 to-primary-700 mb-5 shadow-lg shadow-primary-500/20">
                  <span className="text-3xl">&#x1F389;</span>
                </div>
                <h2 className="text-2xl font-bold text-surface-50">You&apos;re ready to roll</h2>
                <p className="text-surface-300 mt-3 text-sm leading-relaxed max-w-sm mx-auto">
                  Your free plan includes <span className="text-surface-50 font-semibold">100 credits/month</span> &mdash;
                  enough to explore everything Basebase can do. Upgrade anytime if you want more.
                </p>
              </div>
              <div className="space-y-2 mb-2 p-4 rounded-xl bg-surface-800/50 border border-surface-700/50">
                <p className="text-surface-400 text-xs font-medium mb-3">Try asking Basebase:</p>
                <div className="flex flex-col gap-2">
                  <div className="self-start rounded-2xl rounded-bl-sm px-4 py-2.5 bg-primary-500/15 border border-primary-500/25 text-sm text-surface-100 max-w-[92%]">
                    &ldquo;Catch me up on what I missed this week&rdquo;
                  </div>
                  <div className="self-start rounded-2xl rounded-bl-sm px-4 py-2.5 bg-primary-500/15 border border-primary-500/25 text-sm text-surface-100 max-w-[92%]">
                    &ldquo;Which deals have gone dormant?&rdquo;
                  </div>
                  <div className="self-start rounded-2xl rounded-bl-sm px-4 py-2.5 bg-primary-500/15 border border-primary-500/25 text-sm text-surface-100 max-w-[92%]">
                    &ldquo;Create a task in Asana for&hellip;&rdquo;
                  </div>
                  <div className="self-start rounded-2xl rounded-bl-sm px-4 py-2.5 bg-primary-500/15 border border-primary-500/25 text-sm text-surface-100 max-w-[92%]">
                    &ldquo;Research competitors to [Acme]&rdquo;
                  </div>
                </div>
              </div>
              {renderFooter()}
            </>
          )}

          {/* Step 6: Success — Basebase's research + launch */}
          {contentStep === 6 && (
            <>
              <div className="text-center mb-6">
                <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-emerald-500 to-emerald-700 mb-5 shadow-lg shadow-emerald-500/20">
                  <span className="text-3xl">&#x1F680;</span>
                </div>
                <h2 className="text-2xl font-bold text-surface-50">Setup complete!</h2>
                <p className="text-surface-300 mt-2 text-sm">
                  While you were setting up, Basebase got a head start.
                </p>
              </div>
              {companySummary ? (
                <div className="mb-6 p-5 rounded-xl bg-primary-500/10 border border-primary-500/20">
                  <p className="text-surface-200 text-[15px] leading-relaxed">
                    {companySummary}
                  </p>
                </div>
              ) : companySummaryLoading ? (
                <div className="mb-6 p-5 rounded-xl bg-surface-800/50 border border-surface-700 animate-pulse">
                  <p className="text-surface-400 text-sm">
                    Basebase is researching your company&hellip;
                  </p>
                </div>
              ) : (
                <div className="mb-6 p-5 rounded-xl bg-surface-800/50 border border-surface-700">
                  <p className="text-surface-300 text-sm">
                    Basebase is ready to learn about your business. Start a conversation and it&apos;ll
                    get up to speed fast.
                  </p>
                </div>
              )}
              <button
                type="button"
                onClick={onComplete}
                className="w-full btn-primary py-3.5 text-base font-semibold"
              >
                Start chatting with Basebase
              </button>
              <div className="flex justify-center gap-1.5 pt-6">
                {Array.from({ length: TOTAL_STEPS }, (_, i) => (
                  <div
                    key={i}
                    className="h-1.5 w-6 rounded-full bg-primary-500 transition-all duration-300"
                  />
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
