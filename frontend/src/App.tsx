/**
 * Main application component.
 *
 * Handles routing and authentication state.
 */

import { useEffect, useState } from 'react';
import { Chat } from './components/Chat';
import { OAuthCallback } from './components/OAuthCallback';

interface UserInfo {
  id: string;
  email: string;
  name: string | null;
  role: string | null;
  customer_id: string | null;
}

function App(): JSX.Element {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [user, setUser] = useState<UserInfo | null>(null);

  // Check auth status on mount
  useEffect(() => {
    const checkAuth = async (): Promise<void> => {
      // Check URL for user_id param (MVP auth)
      const urlParams = new URLSearchParams(window.location.search);
      const userId = urlParams.get('user_id');

      if (userId) {
        // Store in localStorage for subsequent requests
        localStorage.setItem('user_id', userId);
        // Clean up URL
        window.history.replaceState({}, document.title, '/');
      }

      const storedUserId = localStorage.getItem('user_id');
      if (!storedUserId) {
        setIsLoading(false);
        return;
      }

      try {
        const response = await fetch(`/api/auth/me?user_id=${storedUserId}`);
        if (response.ok) {
          const userData = (await response.json()) as UserInfo;
          setUser(userData);
          setIsAuthenticated(true);
        } else {
          localStorage.removeItem('user_id');
        }
      } catch (error) {
        console.error('Auth check failed:', error);
        localStorage.removeItem('user_id');
      }

      setIsLoading(false);
    };

    void checkAuth();
  }, []);

  // Simple routing
  const path = window.location.pathname;

  if (path === '/auth/callback') {
    return <OAuthCallback />;
  }

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-surface-400">Loading...</p>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <LoginScreen />;
  }

  return <Chat userId={user?.id ?? ''} customerId={user?.customer_id ?? undefined} />;
}

function LoginScreen(): JSX.Element {
  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="max-w-md w-full">
        {/* Logo and Title */}
        <div className="text-center mb-12">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-primary-500 to-primary-700 mb-6">
            <svg
              className="w-8 h-8 text-white"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"
              />
            </svg>
          </div>
          <h1 className="text-3xl font-bold text-surface-50 mb-2">Revenue Copilot</h1>
          <p className="text-surface-400 text-lg">
            AI-powered insights for your sales pipeline
          </p>
        </div>

        {/* Login Card */}
        <div className="card">
          <h2 className="text-xl font-semibold text-surface-100 mb-2">
            Connect your CRM
          </h2>
          <p className="text-surface-400 mb-6">
            Link your Salesforce account to get started with intelligent pipeline
            analysis.
          </p>

          <a href="/api/auth/salesforce/login" className="btn-primary w-full block text-center">
            <span className="inline-flex items-center gap-2">
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z" />
              </svg>
              Connect Salesforce
            </span>
          </a>

          <p className="text-xs text-surface-500 mt-4 text-center">
            We only request read access to your Salesforce data.
          </p>
        </div>

        {/* Features */}
        <div className="mt-8 grid grid-cols-3 gap-4 text-center">
          <div className="animate-fade-in animate-delay-100">
            <div className="text-2xl mb-1">📊</div>
            <p className="text-xs text-surface-400">Pipeline Analysis</p>
          </div>
          <div className="animate-fade-in animate-delay-200">
            <div className="text-2xl mb-1">💬</div>
            <p className="text-xs text-surface-400">Natural Language</p>
          </div>
          <div className="animate-fade-in animate-delay-300">
            <div className="text-2xl mb-1">⚡</div>
            <p className="text-xs text-surface-400">Real-time Insights</p>
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;
