/**
 * OAuth callback handler component.
 *
 * Handles the redirect from Supabase OAuth providers (Google, Microsoft, etc.)
 * Supabase automatically handles the token exchange via the URL hash.
 */

import { useEffect, useState } from 'react';
import { supabase } from '../lib/supabase';
import type { AuthChangeEvent, Session } from '@supabase/supabase-js';

type CallbackState = 'processing' | 'success' | 'error';

export function OAuthCallback(): JSX.Element {
  const [state, setState] = useState<CallbackState>('processing');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (state !== 'processing') {
      return;
    }
    const handleCallback = async (): Promise<void> => {
      try {
        // Check URL for error params (OAuth errors)
        const urlParams = new URLSearchParams(window.location.search);
        const errorParam = urlParams.get('error');
        const errorDescription = urlParams.get('error_description');

        if (errorParam) {
          setState('error');
          setError(errorDescription || errorParam);
          return;
        }

        // Supabase automatically handles the OAuth tokens from the URL hash
        // We just need to wait for the session to be established
        const { data, error: sessionError } = await supabase.auth.getSession();

        if (sessionError) {
          setState('error');
          setError(sessionError.message);
          return;
        }

        if (data.session) {
          setState('success');
          // Redirect to home after a brief delay
          setTimeout(() => {
            window.location.href = '/';
          }, 1500);
        } else {
          // No session yet - might still be processing
          // Listen for auth state change
          const { data: { subscription } } = supabase.auth.onAuthStateChange(
            (event: AuthChangeEvent, session: Session | null) => {
              if (event === 'SIGNED_IN' && session) {
                setState('success');
                setTimeout(() => {
                  window.location.href = '/';
                }, 1500);
                subscription.unsubscribe();
              }
            }
          );

          // Timeout after 10 seconds
          setTimeout(() => {
            if (state === 'processing') {
              setState('error');
              setError('Authentication timed out. Please try again.');
              subscription.unsubscribe();
            }
          }, 10000);
        }
      } catch (err) {
        setState('error');
        setError(err instanceof Error ? err.message : 'An unknown error occurred');
      }
    };

    void handleCallback();
  }, [state]);

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="max-w-md w-full text-center">
        {state === 'processing' && (
          <div className="animate-fade-in">
            <div className="w-12 h-12 border-2 border-primary-500 border-t-transparent rounded-full animate-spin mx-auto mb-4" />
            <h2 className="text-xl font-semibold text-surface-100 mb-2">
              Completing authentication...
            </h2>
            <p className="text-surface-400">
              Please wait while we set up your account.
            </p>
          </div>
        )}

        {state === 'success' && (
          <div className="animate-fade-in">
            <div className="w-12 h-12 rounded-full bg-primary-500 flex items-center justify-center mx-auto mb-4">
              <svg
                className="w-6 h-6 text-white"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M5 13l4 4L19 7"
                />
              </svg>
            </div>
            <h2 className="text-xl font-semibold text-surface-100 mb-2">
              Welcome to Revtops!
            </h2>
            <p className="text-surface-400">Redirecting you to the app...</p>
          </div>
        )}

        {state === 'error' && (
          <div className="animate-fade-in">
            <div className="w-12 h-12 rounded-full bg-red-500 flex items-center justify-center mx-auto mb-4">
              <svg
                className="w-6 h-6 text-white"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M6 18L18 6M6 6l12 12"
                />
              </svg>
            </div>
            <h2 className="text-xl font-semibold text-surface-100 mb-2">
              Authentication failed
            </h2>
            <p className="text-surface-400 mb-6">{error ?? 'An unknown error occurred'}</p>
            <a href="/" className="btn-primary inline-block">
              Try again
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
