/**
 * Main application component.
 *
 * Handles routing between:
 * - Landing page (public)
 * - Auth (sign in / sign up)
 * - Onboarding (connect data sources)
 * - Chat (main app)
 */

import { useEffect, useState } from 'react';
import { supabase } from './lib/supabase';
import { Landing } from './components/Landing';
import { Auth } from './components/Auth';
import { Onboarding } from './components/Onboarding';
import { Chat } from './components/Chat';
import { OAuthCallback } from './components/OAuthCallback';
import type { User } from '@supabase/supabase-js';

type Screen = 'landing' | 'auth' | 'onboarding' | 'chat';

interface UserInfo {
  id: string;
  email: string;
  name: string | null;
  customer_id: string | null;
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
              name: null,
              customer_id: null,
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
          await handleAuthenticatedUser(session.user);
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
    setUser({
      id: supabaseUser.id,
      email: supabaseUser.email ?? '',
      name: supabaseUser.user_metadata?.name ?? null,
      customer_id: supabaseUser.user_metadata?.customer_id ?? null,
    });

    // Check if user has completed onboarding (check localStorage for MVP)
    const completed = localStorage.getItem(`onboarding_${supabaseUser.id}`);
    if (completed) {
      setScreen('chat');
    } else {
      setScreen('onboarding');
    }
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
          customerId={user?.customer_id ?? undefined}
          onLogout={handleLogout}
        />
      );

    default:
      return <Landing onGetStarted={() => setScreen('auth')} />;
  }
}

export default App;
