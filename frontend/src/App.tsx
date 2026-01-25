/**
 * Main application component.
 *
 * Handles:
 * - Authentication flow (landing → auth → onboarding → app)
 * - Work email validation
 * - Company setup for new organizations
 * - Main app layout routing
 */

import { useEffect, useState } from 'react';
import { supabase } from './lib/supabase';
import { getEmailDomain, isPersonalEmail } from './lib/email';
import { API_BASE } from './lib/api';
import { useAppStore } from './store';
import { Landing } from './components/Landing';
import { Auth } from './components/Auth';
import { CompanySetup } from './components/CompanySetup';
import { Onboarding } from './components/Onboarding';
import { AppLayout } from './components/AppLayout';
import { OAuthCallback } from './components/OAuthCallback';
import { AdminWaitlist } from './components/AdminWaitlist';
import { PublicBlog } from './components/PublicBlog';
import { PublicBlogPost } from './components/PublicBlogPost';
import type { User, AuthChangeEvent, Session } from '@supabase/supabase-js';

type Screen = 'landing' | 'auth' | 'blocked-email' | 'not-registered' | 'waitlist' | 'company-setup' | 'onboarding' | 'app' | 'blog' | 'blog-post';

// Simple in-memory store for companies (MVP - in production, use API)
interface StoredCompany {
  id: string; // UUID
  name: string;
}

function generateUUID(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function getStoredCompanies(): Record<string, StoredCompany> {
  const stored = localStorage.getItem('revtops_companies');
  if (!stored) return {};
  
  const companies = JSON.parse(stored) as Record<string, Partial<StoredCompany> & { name: string }>;
  let needsMigration = false;
  
  // Migrate old company format (without UUID) to new format
  for (const domain of Object.keys(companies)) {
    const company = companies[domain];
    if (company && !company.id) {
      company.id = generateUUID();
      needsMigration = true;
    }
  }
  
  if (needsMigration) {
    localStorage.setItem('revtops_companies', JSON.stringify(companies));
  }
  
  return companies as Record<string, StoredCompany>;
}

function storeCompany(domain: string, name: string): StoredCompany {
  const companies = getStoredCompanies();
  const company: StoredCompany = {
    id: generateUUID(),
    name,
  };
  companies[domain] = company;
  localStorage.setItem('revtops_companies', JSON.stringify(companies));
  return company;
}

function getCompanyByDomain(domain: string): StoredCompany | null {
  const companies = getStoredCompanies();
  return companies[domain] || null;
}

function App(): JSX.Element {
  const [screen, setScreen] = useState<Screen>('landing');
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [emailDomain, setEmailDomain] = useState<string>('');
  const [currentBlogSlug, setCurrentBlogSlug] = useState<string>('');
  
  // Zustand store
  const { 
    user, 
    organization,
    setUser, 
    setOrganization, 
    logout: storeLogout,
    syncUserToBackend,
  } = useAppStore();

  // Check auth status on mount
  useEffect(() => {
    const checkAuth = async (): Promise<void> => {
      try {
        // If we already have user in store, show app immediately but still sync with backend
        const currentUser = useAppStore.getState().user;
        const currentOrg = useAppStore.getState().organization;
        const hasPersistedUser = currentUser && currentOrg;
        
        if (hasPersistedUser) {
          console.log('[Auth] User in store, showing app while syncing...');
          setScreen('app');
          setIsLoading(false);
        }

        const { data: { session } } = await supabase.auth.getSession();

        if (session?.user) {
          // Always sync with backend to get fresh data (including avatar_url)
          await handleAuthenticatedUser(session.user);
        } else if (!hasPersistedUser) {
          // No session and no persisted user - check legacy localStorage auth
          const storedUserId = localStorage.getItem('user_id');
          if (storedUserId) {
            setUser({
              id: storedUserId,
              email: 'user@example.com',
              name: null,
              avatarUrl: null,
              roles: [],
            });
            setOrganization({
              id: 'example.com',
              name: 'Example Company',
              logoUrl: null,
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
        console.log('[Auth] Event:', event, 'hasSession:', !!session);
        
        // Only handle actual sign-in/sign-out events, not token refreshes
        if (event === 'SIGNED_IN' && session?.user) {
          // Skip if user is already authenticated (this is just a token refresh)
          const currentUser = useAppStore.getState().user;
          if (currentUser?.id === session.user.id) {
            console.log('[Auth] Token refresh, skipping re-auth');
            return;
          }
          
          setIsLoading(true);
          await handleAuthenticatedUser(session.user);
          setIsLoading(false);
        } else if (event === 'SIGNED_OUT') {
          storeLogout();
          setScreen('landing');
        }
      }
    );

    void checkAuth();

    return () => {
      subscription.unsubscribe();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleAuthenticatedUser = async (supabaseUser: User): Promise<void> => {
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

    // Check if personal email
    if (isPersonalEmail(email)) {
      setUser({
        id: supabaseUser.id,
        email,
        name,
        avatarUrl,
        roles: [],
      });
      setScreen('blocked-email');
      return;
    }

    // Set user in store first (needed for syncUserToBackend)
    setUser({
      id: supabaseUser.id,
      email,
      name,
      avatarUrl,
      roles: [],
    });

    // CHECK WAITLIST STATUS FIRST - before any company/org setup
    // This catches users who signed up via waitlist form
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

      if (syncResponse.status === 403) {
        // User not registered - needs to join waitlist
        setScreen('not-registered');
        return;
      }

      if (syncResponse.ok) {
        const userData = await syncResponse.json() as { 
          id: string;  // Database user ID (may differ from Supabase ID for waitlist users)
          status: string; 
          avatar_url: string | null;
          name: string | null;
          roles: string[];
        };
        
        // Update user with data from backend (authoritative source)
        // Use the database ID from backend - this may differ from Supabase ID for waitlist users
        setUser({
          id: userData.id,
          email,
          name: userData.name ?? name,
          avatarUrl: userData.avatar_url ?? avatarUrl,
          roles: userData.roles ?? [],
        });
        
        if (userData.status === 'waitlist') {
          setScreen('waitlist');
          return;
        }
        // If status is 'invited', it gets upgraded to 'active' by the backend
      }
    } catch (error) {
      console.error('Failed to check user status:', error);
    }

    // User is allowed in - now check company/organization
    let existingCompany = getCompanyByDomain(domain);

    // If not in localStorage, check backend (colleague on different machine scenario)
    if (!existingCompany) {
      try {
        const response = await fetch(`${API_BASE}/auth/organizations/by-domain/${encodeURIComponent(domain)}`);
        if (response.ok) {
          const backendOrg: { id: string; name: string; email_domain: string } = await response.json();
          // Store in localStorage for future use
          existingCompany = {
            id: backendOrg.id,
            name: backendOrg.name,
          };
          // Update localStorage
          const companies = getStoredCompanies();
          companies[domain] = existingCompany;
          localStorage.setItem('revtops_companies', JSON.stringify(companies));
        }
      } catch (error) {
        console.error('Failed to check backend for organization:', error);
      }
    }

    if (!existingCompany) {
      setScreen('company-setup');
      return;
    }

    // Set organization in store
    setOrganization({
      id: existingCompany.id,
      name: existingCompany.name,
      logoUrl: null,
    });

    // Sync user with organization to backend
    await syncUserToBackend();

    // Ensure organization exists in backend (migration for existing localStorage data)
    try {
      await fetch(`${API_BASE}/auth/organizations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: existingCompany.id,
          name: existingCompany.name,
          email_domain: domain,
        }),
      });
    } catch (error) {
      console.error('Failed to sync organization to backend:', error);
    }

    // Check if user has completed onboarding OR already has connected integrations
    const completedOnboarding = localStorage.getItem(`onboarding_${supabaseUser.id}`);
    
    // Fetch integrations directly to check for connected ones
    let hasConnectedIntegrations = false;
    try {
      const integrationsResponse = await fetch(
        `${API_BASE}/auth/integrations?organization_id=${existingCompany.id}&user_id=${supabaseUser.id}`
      );
      if (integrationsResponse.ok) {
        const integrationsData = await integrationsResponse.json() as { 
          integrations: Array<{ provider: string; current_user_connected?: boolean }> 
        };
        hasConnectedIntegrations = integrationsData.integrations.length > 0;
      }
    } catch (error) {
      console.error('Failed to check integrations:', error);
    }
    
    if (completedOnboarding || hasConnectedIntegrations) {
      // Mark onboarding as complete if they have integrations (for future logins)
      if (hasConnectedIntegrations && !completedOnboarding) {
        localStorage.setItem(`onboarding_${supabaseUser.id}`, 'true');
      }
      setScreen('app');
    } else {
      setScreen('onboarding');
    }
  };

  const handleCompanySetup = async (companyName: string): Promise<void> => {
    if (!user) return;

    // Store in localStorage first
    const company = storeCompany(emailDomain, companyName);

    // Set organization in store
    setOrganization({
      id: company.id,
      name: company.name,
      logoUrl: null,
    });

    // Create organization in backend database
    try {
      const response = await fetch(`${API_BASE}/auth/organizations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: company.id,
          name: companyName,
          email_domain: emailDomain,
        }),
      });

      if (!response.ok) {
        console.error('Failed to create organization in backend:', await response.text());
      }
    } catch (error) {
      console.error('Failed to create organization:', error);
    }

    // Sync user to backend now that we have an org
    await syncUserToBackend();

    setScreen('onboarding');
  };

  const handleLogout = async (): Promise<void> => {
    await supabase.auth.signOut();
    localStorage.removeItem('user_id');
    storeLogout();
    setScreen('landing');
  };

  const handleOnboardingComplete = (): void => {
    if (user) {
      localStorage.setItem(`onboarding_${user.id}`, 'true');
    }
    setScreen('app');
  };

  const handleOnboardingSkip = (): void => {
    if (user) {
      localStorage.setItem(`onboarding_${user.id}`, 'true');
    }
    setScreen('app');
  };

  // Handle OAuth callback route
  const path = window.location.pathname;
  if (path === '/auth/callback') {
    return <OAuthCallback />;
  }

  // Handle admin waitlist route
  if (path === '/admin/waitlist') {
    const params = new URLSearchParams(window.location.search);
    const adminKey = params.get('key');
    if (adminKey) {
      return <AdminWaitlist adminKey={adminKey} />;
    }
    // No key provided - show error
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <div className="text-center">
          <h1 className="text-xl font-bold text-surface-50 mb-2">Access Denied</h1>
          <p className="text-surface-400">Admin key required. Add ?key=YOUR_KEY to the URL.</p>
        </div>
      </div>
    );
  }

  // Loading state
  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center animate-pulse">
            <img src="/logo.svg" alt="Loading" className="w-5 h-5 invert" />
          </div>
          <p className="text-surface-400">Loading...</p>
        </div>
      </div>
    );
  }

  // Render based on current screen
  switch (screen) {
    case 'landing':
      return (
        <Landing
          onGetStarted={() => setScreen('auth')}
          onNavigateToBlog={() => setScreen('blog')}
        />
      );

    case 'blog':
      return (
        <div className="min-h-screen bg-surface-950">
          {/* Navigation */}
          <nav className="sticky top-0 z-50 bg-surface-950/80 backdrop-blur-lg border-b border-surface-800">
            <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
              <button
                onClick={() => setScreen('landing')}
                className="flex items-center gap-2 hover:opacity-80 transition-opacity"
              >
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center">
                  <img src="/logo.svg" alt="Revtops" className="w-5 h-5 invert" />
                </div>
                <span className="text-xl font-bold text-surface-50">Revtops</span>
              </button>
              <div className="flex items-center gap-6">
                <button
                  onClick={() => setScreen('blog')}
                  className="px-3 py-2 text-sm font-medium text-primary-400"
                >
                  Blog
                </button>
                <button
                  onClick={() => setScreen('auth')}
                  className="px-4 py-2 text-sm font-medium text-surface-300 hover:text-white transition-colors"
                >
                  Sign In
                </button>
              </div>
            </div>
          </nav>
          <PublicBlog
            onSelectPost={(slug) => {
              setCurrentBlogSlug(slug);
              setScreen('blog-post');
            }}
          />
        </div>
      );

    case 'blog-post':
      return (
        <div className="min-h-screen bg-surface-950">
          {/* Navigation */}
          <nav className="sticky top-0 z-50 bg-surface-950/80 backdrop-blur-lg border-b border-surface-800">
            <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
              <button
                onClick={() => setScreen('landing')}
                className="flex items-center gap-2 hover:opacity-80 transition-opacity"
              >
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center">
                  <img src="/logo.svg" alt="Revtops" className="w-5 h-5 invert" />
                </div>
                <span className="text-xl font-bold text-surface-50">Revtops</span>
              </button>
              <div className="flex items-center gap-6">
                <button
                  onClick={() => setScreen('blog')}
                  className="px-3 py-2 text-sm font-medium text-primary-400"
                >
                  Blog
                </button>
                <button
                  onClick={() => setScreen('auth')}
                  className="px-4 py-2 text-sm font-medium text-surface-300 hover:text-white transition-colors"
                >
                  Sign In
                </button>
              </div>
            </div>
          </nav>
          <PublicBlogPost slug={currentBlogSlug} onBack={() => setScreen('blog')} />
        </div>
      );

    case 'auth':
      return (
        <Auth
          onBack={() => setScreen('landing')}
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
              Revtops is designed for teams. Please sign in with your work email address
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
              <button onClick={() => void handleLogout()} className="btn-primary">
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

    case 'company-setup':
      return (
        <CompanySetup
          emailDomain={emailDomain}
          onComplete={(name) => void handleCompanySetup(name)}
          onBack={() => void handleLogout()}
        />
      );

    case 'onboarding':
      return (
        <Onboarding
          onComplete={handleOnboardingComplete}
          onSkip={handleOnboardingSkip}
        />
      );

    case 'app': {
      if (!user || !organization) {
        return (
          <Landing
            onGetStarted={() => setScreen('auth')}
            onNavigateToBlog={() => setScreen('blog')}
          />
        );
      }

      return (
        <AppLayout
          onLogout={() => void handleLogout()}
        />
      );
    }

    default:
      return (
        <Landing
          onGetStarted={() => setScreen('auth')}
          onNavigateToBlog={() => setScreen('blog')}
        />
      );
  }
}

export default App;
