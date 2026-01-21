/**
 * Main application component.
 *
 * Handles routing between:
 * - Landing page (public)
 * - Auth (sign in / sign up)
 * - Company setup (for new companies)
 * - Onboarding (connect data sources)
 * - Chat (main app)
 */

import { useEffect, useState } from 'react';
import { supabase } from './lib/supabase';
import { getEmailDomain, isPersonalEmail } from './lib/email';
import { Landing } from './components/Landing';
import { Auth } from './components/Auth';
import { CompanySetup } from './components/CompanySetup';
import { Onboarding } from './components/Onboarding';
import { Chat } from './components/Chat';
import { OAuthCallback } from './components/OAuthCallback';
import type { User } from '@supabase/supabase-js';

type Screen = 'landing' | 'auth' | 'blocked-email' | 'company-setup' | 'onboarding' | 'chat';

interface UserInfo {
  id: string;
  email: string;
  emailDomain: string;
  name: string | null;
  companyId: string | null;
  companyName: string | null;
}

// Simple in-memory store for companies (MVP - in production, use API)
function getStoredCompanies(): Record<string, string> {
  const stored = localStorage.getItem('revtops_companies');
  return stored ? JSON.parse(stored) : {};
}

function storeCompany(domain: string, name: string): void {
  const companies = getStoredCompanies();
  companies[domain] = name;
  localStorage.setItem('revtops_companies', JSON.stringify(companies));
}

function getCompanyByDomain(domain: string): string | null {
  const companies = getStoredCompanies();
  return companies[domain] || null;
}

function App(): JSX.Element {
  const [screen, setScreen] = useState<Screen>('landing');
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [user, setUser] = useState<UserInfo | null>(null);

  // Check auth status on mount
  useEffect(() => {
    const checkAuth = async (): Promise<void> => {
      try {
        // Check Supabase session
        const { data: { session } } = await supabase.auth.getSession();

        if (session?.user) {
          await handleAuthenticatedUser(session.user);
        } else {
          // Check legacy localStorage auth
          const storedUserId = localStorage.getItem('user_id');
          if (storedUserId) {
            // For backwards compatibility with existing MVP auth
            setUser({
              id: storedUserId,
              email: 'user@example.com',
              emailDomain: 'example.com',
              name: null,
              companyId: null,
              companyName: null,
            });
            setScreen('chat');
          }
        }
      } catch (error) {
        console.error('Auth check failed:', error);
      } finally {
        setIsLoading(false);
      }
    };

    // Listen for auth changes
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      async (event, session) => {
        if (event === 'SIGNED_IN' && session?.user) {
          setIsLoading(true);
          await handleAuthenticatedUser(session.user);
          setIsLoading(false);
        } else if (event === 'SIGNED_OUT') {
          setUser(null);
          setScreen('landing');
        }
      }
    );

    void checkAuth();

    return () => {
      subscription.unsubscribe();
    };
  }, []);

  const handleAuthenticatedUser = async (supabaseUser: User): Promise<void> => {
    const email = supabaseUser.email ?? '';
    const emailDomain = getEmailDomain(email);

    // Check if personal email (for OAuth users who bypassed frontend validation)
    if (isPersonalEmail(email)) {
      setUser({
        id: supabaseUser.id,
        email,
        emailDomain,
        name: supabaseUser.user_metadata?.name ?? null,
        companyId: null,
        companyName: null,
      });
      setScreen('blocked-email');
      return;
    }

    // Check if company exists for this domain
    const existingCompany = getCompanyByDomain(emailDomain);

    setUser({
      id: supabaseUser.id,
      email,
      emailDomain,
      name: supabaseUser.user_metadata?.name ?? null,
      companyId: existingCompany ? emailDomain : null,
      companyName: existingCompany,
    });

    if (!existingCompany) {
      // New company - need to set it up
      setScreen('company-setup');
      return;
    }

    // Check if user has completed onboarding
    const completedOnboarding = localStorage.getItem(`onboarding_${supabaseUser.id}`);
    if (completedOnboarding) {
      setScreen('chat');
    } else {
      setScreen('onboarding');
    }
  };

  const handleCompanySetup = (companyName: string): void => {
    if (!user) return;

    // Store the company
    storeCompany(user.emailDomain, companyName);

    // Update user with company info
    setUser({
      ...user,
      companyId: user.emailDomain,
      companyName,
    });

    // Proceed to onboarding
    setScreen('onboarding');
  };

  const handleLogout = async (): Promise<void> => {
    await supabase.auth.signOut();
    localStorage.removeItem('user_id');
    setUser(null);
    setScreen('landing');
  };

  const handleOnboardingComplete = (): void => {
    if (user) {
      localStorage.setItem(`onboarding_${user.id}`, 'true');
    }
    setScreen('chat');
  };

  const handleOnboardingSkip = (): void => {
    if (user) {
      localStorage.setItem(`onboarding_${user.id}`, 'true');
    }
    setScreen('chat');
  };

  // Handle OAuth callback route
  const path = window.location.pathname;
  if (path === '/auth/callback') {
    return <OAuthCallback />;
  }

  // Loading state
  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center animate-pulse">
            <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
            </svg>
          </div>
          <p className="text-surface-400">Loading...</p>
        </div>
      </div>
    );
  }

  // Render based on current screen
  switch (screen) {
    case 'landing':
      return <Landing onGetStarted={() => setScreen('auth')} />;

    case 'auth':
      return (
        <Auth
          onBack={() => setScreen('landing')}
          onSuccess={() => {
            // Auth component handles the redirect via onAuthStateChange
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
              (not {user?.emailDomain}).
            </p>
            <button onClick={handleLogout} className="btn-primary">
              Sign in with work email
            </button>
          </div>
        </div>
      );

    case 'company-setup':
      return (
        <CompanySetup
          emailDomain={user?.emailDomain ?? ''}
          onComplete={handleCompanySetup}
          onBack={handleLogout}
        />
      );

    case 'onboarding':
      return (
        <Onboarding
          onComplete={handleOnboardingComplete}
          onSkip={handleOnboardingSkip}
        />
      );

    case 'chat':
      return (
        <Chat
          userId={user?.id ?? ''}
          organizationId={user?.companyId ?? undefined}
          onLogout={handleLogout}
        />
      );

    default:
      return <Landing onGetStarted={() => setScreen('auth')} />;
  }
}

export default App;
