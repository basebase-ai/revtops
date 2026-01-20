/**
 * OAuth callback handler component.
 *
 * Handles the redirect from Salesforce OAuth and stores the user session.
 */

import { useEffect, useState } from 'react';

type CallbackState = 'processing' | 'success' | 'error';

export function OAuthCallback(): JSX.Element {
  const [state, setState] = useState<CallbackState>('processing');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const handleCallback = (): void => {
      const urlParams = new URLSearchParams(window.location.search);
      const userId = urlParams.get('user_id');
      const errorParam = urlParams.get('error');

      if (errorParam) {
        setState('error');
        setError(errorParam);
        return;
      }

      if (userId) {
        localStorage.setItem('user_id', userId);
        setState('success');
        // Redirect to home after a brief delay
        setTimeout(() => {
          window.location.href = '/';
        }, 1500);
      } else {
        setState('error');
        setError('No user ID received from authentication');
      }
    };

    handleCallback();
  }, []);

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
            <div className="w-12 h-12 rounded-full bg-green-500 flex items-center justify-center mx-auto mb-4">
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
              Connected successfully!
            </h2>
            <p className="text-surface-400">Redirecting you to Revenue Copilot...</p>
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
            <p className="text-surface-400 mb-4">{error ?? 'An unknown error occurred'}</p>
            <a href="/" className="btn-primary inline-block">
              Try again
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
