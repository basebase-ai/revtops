/**
 * Main application component.
 *
 * Handles:
 * - Authentication flow (auth → app)
 * - Company setup for new organizations
 * - Main app layout routing
 * 
 * Note: Public landing page can be served from www.basebase.com (VITE_WWW_URL).
 */

import { useCallback, useEffect, useState } from 'react';
import { supabase } from './lib/supabase';
import { getEmailDomain } from './lib/email';
import { API_BASE } from './lib/api';
import { useAppStore } from './store';
import { Auth } from './components/Auth';
import { OnboardingWizard } from './components/OnboardingWizard';
import { SubscriptionSetup } from './components/SubscriptionSetup';
import { AppLayout } from './components/AppLayout';
import { OAuthCallback } from './components/OAuthCallback';
import { AppEmbed } from './components/apps/AppEmbed';
import type { User, AuthChangeEvent, Session } from '@supabase/supabase-js';
import { queryClient } from './lib/queryClient';
import { APP_NAME, LOGO_PATH } from './lib/brand';

type Screen = 'auth' | 'blocked-email' | 'not-registered' | 'waitlist' | 'onboarding-wizard' | 'payment-setup' | 'app';

// URL for public website (landing, blog, waitlist form)
const WWW_URL = import.meta.env.VITE_WWW_URL ?? 'https://www.basebase.com';

function App(): JSX.Element {
  const [screen, setScreen] = useState<Screen>('auth');
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [emailDomain, setEmailDomain] = useState<string>('');
  const [onboardingMode, setOnboardingMode] = useState<'new' | 'invited' | null>(null);
  const [isCreatingNewOrg, setIsCreatingNewOrg] = useState<boolean>(false);

  // Zustand store
  const {
    user,
    organization,
    setUser,
    setOrganization,
    logout: storeLogout,
    fetchUserOrganizations,
    switchActiveOrganization,
  } = useAppStore();

  const handleCreateNewOrg = useCallback((): void => {
    localStorage.setItem('onboarding_step', '1');
    setOnboardingMode('new');
    setIsCreatingNewOrg(true);
    setScreen('onboarding-wizard');
  }, []);

  // Easter egg: Cmd/Ctrl+Shift+O opens onboarding for testing (when in app)
  useEffect(() => {
    if (screen !== 'app') return;
    const onKeyDown = (e: KeyboardEvent): void => {
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === 'o') {
        e.preventDefault();
        handleCreateNewOrg();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [screen, handleCreateNewOrg]);

  // Check auth status on mount
  useEffect(() => {
    const checkAuth = async (): Promise<void> => {
      try {
        // If we already have user in store, show app immediately but still sync with backend
        const currentUser = useAppStore.getState().user;
        const currentOrg = useAppStore.getState().organization;
        const hasPersistedUser = currentUser && currentOrg;
        
        if (hasPersistedUser) {
          setScreen('app');
          setIsLoading(false);
        }

        const { data: { session } } = await supabase.auth.getSession();

        if (session?.user) {
          // If masquerading, preserve the masquerade state - don't overwrite with Supabase user
          const masquerade = useAppStore.getState().masquerade;
          if (masquerade) {
            // Don't call handleAuthenticatedUser - keep the masqueraded user/org
          } else {
            // Always sync with backend to get fresh data (including avatar_url).
            // When the app is already visible (persisted-user fast path), skip org
            // updates — the URL sync in AppLayout is the source of truth for which
            // org should be active.
            await handleAuthenticatedUser(session.user, !!hasPersistedUser);
            // Refresh user's org list in background
            void useAppStore.getState().fetchUserOrganizations();
          }
        } else if (!hasPersistedUser) {
          // No session and no persisted user - check legacy localStorage auth
          const storedUserId = localStorage.getItem('user_id');
          if (storedUserId) {
            setUser({
              id: storedUserId,
              email: 'user@example.com',
              name: null,
              avatarUrl: null,
              phoneNumber: null,
              jobTitle: null,
              roles: [],
              smsConsent: false,
              whatsappConsent: false,
              phoneNumberVerified: false,
            });
            setOrganization({
              id: 'example.com',
              name: 'Example Company',
              logoUrl: null,
              handle: null,
            });
            setScreen('app');
          }
        }
      } catch (error) {
        console.error('Auth check failed:', error);
      } finally {
        setIsLoading(false);
      }
    };

    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      async (event: AuthChangeEvent, session: Session | null) => {
        // Only handle actual sign-in/sign-out events, not token refreshes
        if (event === 'SIGNED_IN' && session?.user) {
          // Skip if masquerading - don't overwrite masquerade state
          const masquerade = useAppStore.getState().masquerade;
          if (masquerade) {
            return;
          }
          
          // Skip if user is already authenticated (this is just a token refresh)
          const currentUser = useAppStore.getState().user;
          if (currentUser?.id === session.user.id) {
            return;
          }
          
          setIsLoading(true);
          await handleAuthenticatedUser(session.user);
          setIsLoading(false);
        } else if (event === 'SIGNED_OUT') {
          clearAllLocalData();
          // Redirect to public website on sign out (only in production)
          if (window.location.hostname !== 'localhost') {
            window.location.href = WWW_URL;
          } else {
            // In local dev, just reload to show login screen
            window.location.reload();
          }
        }
      }
    );

    void checkAuth();

    return () => {
      subscription.unsubscribe();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleAuthenticatedUser = async (supabaseUser: User, skipOrgUpdate = false): Promise<void> => {
    const email = supabaseUser.email ?? '';
    const domain = getEmailDomain(email);
    setEmailDomain(domain);

    // Get avatar from OAuth metadata - try multiple possible field names
    // Google OAuth stores it in user_metadata, but also check identities array
    const identityData = supabaseUser.identities?.[0]?.identity_data as Record<string, unknown> | undefined;
    const newAvatarUrl = (supabaseUser.user_metadata?.avatar_url as string | undefined) ??
      (supabaseUser.user_metadata?.picture as string | undefined) ??
      (identityData?.avatar_url as string | undefined) ??
      (identityData?.picture as string | undefined) ??
      null;
    
    // Preserve existing avatar URL if new value is null (session restore may not have metadata)
    const existingUser = useAppStore.getState().user;
    const avatarUrl = newAvatarUrl ?? existingUser?.avatarUrl ?? null;

    const name = (supabaseUser.user_metadata?.name as string | undefined) ?? 
      (supabaseUser.user_metadata?.full_name as string | undefined) ?? 
      (identityData?.name as string | undefined) ??
      (identityData?.full_name as string | undefined) ??
      null;

    // Set user in store first (needed for syncUserToBackend).
    // Preserve persisted roles/profile fields so the admin guard in AppLayout
    // doesn't kick the user out before the backend sync responds.
    setUser({
      id: supabaseUser.id,
      email,
      name,
      avatarUrl,
      phoneNumber: existingUser?.phoneNumber ?? null,
      jobTitle: existingUser?.jobTitle ?? null,
      roles: existingUser?.roles ?? [],
      smsConsent: existingUser?.smsConsent ?? false,
      whatsappConsent: existingUser?.whatsappConsent ?? false,
      phoneNumberVerified: existingUser?.phoneNumberVerified ?? false,
    });

    // CHECK WAITLIST STATUS FIRST - before any company/org setup
    // This catches users who signed up via waitlist form
    // Also handles invited users with personal emails (they'll have a pending invitation)
    try {
      const syncResponse = await fetch(`${API_BASE}/auth/users/sync`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: supabaseUser.id,
          email,
          name,
          avatar_url: avatarUrl,
        }),
      });


      if (syncResponse.ok) {
        const userData = await syncResponse.json() as { 
          id: string;
          status: string; 
          avatar_url: string | null;
          name: string | null;
          roles: string[];
          phone_number: string | null;
          job_title: string | null;
          organization_id: string | null;
          organization: { id: string; name: string; logo_url: string | null; handle?: string | null } | null;
          needs_onboarding: boolean;
          onboarding_mode: 'new' | 'invited' | null;
          sms_consent?: boolean;
          whatsapp_consent?: boolean;
          phone_number_verified?: boolean;
        };
        
        // Update user with data from backend (authoritative source)
        // Use the database ID from backend - this may differ from Supabase ID for waitlist users
        setUser({
          id: userData.id,
          email,
          name: userData.name ?? name,
          avatarUrl: userData.avatar_url ?? avatarUrl,
          phoneNumber: userData.phone_number ?? null,
          jobTitle: userData.job_title ?? null,
          roles: userData.roles ?? [],
          smsConsent: userData.sms_consent ?? false,
          whatsappConsent: userData.whatsapp_consent ?? false,
          phoneNumberVerified: userData.phone_number_verified ?? false,
        });
        
        // If sync returned an organization, set it and route based on onboarding status
        if (userData.organization) {
          const org = userData.organization as { id: string; name: string; logo_url: string | null; handle?: string | null };
          if (!skipOrgUpdate) {
            setOrganization({
              id: org.id,
              name: org.name,
              logoUrl: org.logo_url ?? null,
              handle: org.handle ?? null,
            });
          }
          await fetchUserOrganizations();
          if (userData.needs_onboarding) {
            setOnboardingMode(userData.onboarding_mode);
            setScreen('onboarding-wizard');
            return;
          }
          setScreen('app');
          return;
        }
      }
    } catch (error) {
      console.error('Failed to check user status:', error);
    }

    // Check if the user already has active org memberships but no active org returned from sync.
    await fetchUserOrganizations();
    const organizations = useAppStore.getState().organizations;
    if (organizations.length > 0) {
      if (!skipOrgUpdate) {
        const activeOrg = organizations.find((o) => o.isActive) ?? organizations[0];
        if (activeOrg) {
          setOrganization({
            id: activeOrg.id,
            name: activeOrg.name,
            logoUrl: activeOrg.logoUrl ?? null,
            handle: activeOrg.handle ?? null,
          });
        }
      }
      setScreen('app');
      return;
    }

    // No memberships yet: continue to onboarding wizard.
    setScreen('onboarding-wizard');
    return;
  };

  const handleLogout = async (): Promise<void> => {
    await supabase.auth.signOut();
    clearAllLocalData();
    // Redirect to public website (only in production)
    if (window.location.hostname !== 'localhost') {
      window.location.href = WWW_URL;
    } else {
      window.location.reload();
    }
  };

  /**
   * Nuke every piece of client-side state so a different user can sign in
   * cleanly: localStorage, sessionStorage, React Query cache, Zustand, and
   * cookies.
   */
  function clearAllLocalData(): void {
    // 1. Reset Zustand in-memory state
    storeLogout();

    // 2. Wipe all localStorage (covers revtops-store, revtops_companies,
    //    user_id, and any Supabase-managed keys)
    localStorage.clear();

    // 3. Wipe sessionStorage (Supabase may store tokens here too)
    sessionStorage.clear();

    // 4. Clear React Query in-memory cache
    queryClient.clear();

    // 5. Clear all cookies for this domain
    document.cookie.split(';').forEach((cookie) => {
      const name: string = cookie.split('=')[0]?.trim() ?? '';
      if (name) {
        document.cookie = `${name}=;expires=${new Date(0).toUTCString()};path=/`;
      }
    });
  }

  // Handle OAuth callback route
  const path = window.location.pathname;
  if (path === '/auth/callback') {
    return <OAuthCallback />;
  }

  // Handle password reset callback - show Auth component with reset mode
  if (path === '/auth') {
    const hashParams = new URLSearchParams(window.location.hash.substring(1));
    const isRecovery = hashParams.get('type') === 'recovery';
    if (isRecovery) {
      return (
        <Auth
          onBack={() => { window.location.href = WWW_URL; }}
          onSuccess={() => setScreen('app')}
        />
      );
    }
  }

  // Handle embed route (standalone, no auth required – token in URL)
  if (path.startsWith('/embed/')) {
    return <AppEmbed />;
  }

  // Loading state
  if (isLoading) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-surface-950">
        <div className="flex flex-col items-center gap-6">
          <div className="relative">
            <div className="w-14 h-14 rounded-full border-2 border-surface-700 border-t-primary-500 animate-spin" />
            <div className="absolute inset-0 flex items-center justify-center">
              <img src={LOGO_PATH} alt="" className="w-7 h-7 opacity-90" />
            </div>
          </div>
          <div className="flex flex-col items-center gap-1">
            <p className="text-surface-200 font-medium">Loading</p>
            <p className="text-surface-500 text-sm">Preparing your workspace…</p>
          </div>
        </div>
      </div>
    );
  }

  // Render based on current screen
  switch (screen) {
    case 'auth':
      return (
        <Auth
          onBack={() => { window.location.href = WWW_URL; }}
          onSuccess={() => {
            // Auth component handles redirect via onAuthStateChange
          }}
        />
      );

    case 'blocked-email':
      return (
        <div className="min-h-screen flex items-center justify-center p-4">
          <div className="max-w-md w-full text-center">
            <div className="w-16 h-16 rounded-full bg-red-500/20 flex items-center justify-center mx-auto mb-6">
              <svg className="w-8 h-8 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            </div>
            <h1 className="text-2xl font-bold text-surface-50 mb-3">Work email required</h1>
            <p className="text-surface-400 mb-6">
              {APP_NAME} is designed for teams. Please sign in with your work email address
              (not {emailDomain}).
            </p>
            <button onClick={() => void handleLogout()} className="btn-primary">
              Sign in with work email
            </button>
          </div>
        </div>
      );

    case 'not-registered':
      return (
        <div className="min-h-screen flex items-center justify-center p-4">
          <div className="max-w-md w-full text-center">
            <div className="w-16 h-16 rounded-full bg-yellow-500/20 flex items-center justify-center mx-auto mb-6">
              <svg className="w-8 h-8 text-yellow-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            </div>
            <h1 className="text-2xl font-bold text-surface-50 mb-3">Join the waitlist first</h1>
            <p className="text-surface-400 mb-6">
              We're currently invite-only. Join the waitlist on our homepage and we'll let you know when it's your turn.
            </p>
            <div className="flex gap-3 justify-center">
              <button 
                onClick={() => { window.location.href = WWW_URL; }} 
                className="btn-primary"
              >
                Back to homepage
              </button>
            </div>
          </div>
        </div>
      );

    case 'waitlist':
      return (
        <div className="min-h-screen flex items-center justify-center p-4">
          <div className="max-w-md w-full text-center">
            <div className="w-16 h-16 rounded-full bg-primary-500/20 flex items-center justify-center mx-auto mb-6">
              <svg className="w-8 h-8 text-primary-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <h1 className="text-2xl font-bold text-surface-50 mb-3">You're on the waitlist!</h1>
            <p className="text-surface-400 mb-6">
              Thanks for signing up. We're gradually letting people in and will email you at{' '}
              <span className="text-surface-200 font-medium">{user?.email}</span>{' '}
              when it's your turn.
            </p>
            <button onClick={() => void handleLogout()} className="btn-secondary">
              Sign out
            </button>
          </div>
        </div>
      );

    case 'onboarding-wizard':
      return (
        <OnboardingWizard
          emailDomain={emailDomain}
          isInvitedMode={onboardingMode === 'invited'}
          isCreatingNewOrg={isCreatingNewOrg}
          onComplete={async () => {
            const state = useAppStore.getState();
            if (isCreatingNewOrg && state.organization?.id) {
              await fetchUserOrganizations();
              await switchActiveOrganization(state.organization.id);
              setIsCreatingNewOrg(false);
            }
            state.startNewChat();
            const org = state.organization;
            const orgs = state.organizations;
            const handle = org?.handle ?? (org?.id ? orgs.find((o) => o.id === org.id)?.handle ?? null : null) ?? null;
            const prefix = handle ? `/${handle}` : '';
            window.history.replaceState({}, '', `${prefix}/chat`);
            setScreen('app');
          }}
          onBack={() => {
            if (isCreatingNewOrg) {
              setIsCreatingNewOrg(false);
              setScreen('app');
            } else {
              void handleLogout();
            }
          }}
        />
      );

    case 'payment-setup':
      return (
        <SubscriptionSetup
          onComplete={() => setScreen('app')}
          onBack={() => void handleLogout()}
          backLabel="Sign out"
        />
      );

    case 'app': {
      if (!user || !organization) {
        // If no user, go to auth
        return (
          <Auth
            onBack={() => { window.location.href = WWW_URL; }}
            onSuccess={() => {}}
          />
        );
      }

      return (
        <AppLayout
          onLogout={() => void handleLogout()}
          onCreateNewOrg={handleCreateNewOrg}
        />
      );
    }

    default:
      return (
        <Auth
          onBack={() => { window.location.href = WWW_URL; }}
          onSuccess={() => {}}
        />
      );
  }
}

export default App;
