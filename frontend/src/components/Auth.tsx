/**
 * Authentication component.
 * 
 * Handles sign up and sign in using Supabase Auth.
 * Requires work email (blocks personal email domains).
 */

import { useState, useEffect } from 'react';
import { supabase } from '../lib/supabase';
import { isPersonalEmail } from '../lib/email';
import { validateGoodPassword } from '../lib/password';

interface AuthProps {
  onBack: () => void;
  onSuccess: () => void;
}

export function Auth({ onBack, onSuccess }: AuthProps): JSX.Element {
  const [mode, setMode] = useState<'signin' | 'signup' | 'forgot' | 'reset'>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  // Check if this is a password reset callback
  useEffect(() => {
    const hashParams = new URLSearchParams(window.location.hash.substring(1));
    const type = hashParams.get('type');
    if (type === 'recovery') {
      setMode('reset');
    }
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setMessage(null);

    // Validate work email (except for password reset which doesn't need email)
    if (mode !== 'reset' && isPersonalEmail(email)) {
      setError('Please use your work email address. Personal email domains like Gmail and Hotmail are not allowed.');
      setLoading(false);
      return;
    }

    try {
      if (mode === 'signup') {
        const passwordValidation = validateGoodPassword(password, email);
        if (!passwordValidation.isValid) {
          setError(passwordValidation.errors[0] ?? 'Password does not meet security requirements.');
          setLoading(false);
          return;
        }

        const { error } = await supabase.auth.signUp({
          email,
          password,
          options: {
            data: { name },
          },
        });
        if (error) throw error;
        setMessage('Check your email to confirm your account!');
      } else if (mode === 'forgot') {
        const { error } = await supabase.auth.resetPasswordForEmail(email, {
          redirectTo: `${window.location.origin}/auth`,
        });
        if (error) throw error;
        setMessage('Check your email for a password reset link!');
      } else if (mode === 'reset') {
        if (newPassword !== confirmPassword) {
          setError('Passwords do not match');
          setLoading(false);
          return;
        }

        const passwordValidation = validateGoodPassword(newPassword, email);
        if (!passwordValidation.isValid) {
          setError(passwordValidation.errors[0] ?? 'Password does not meet security requirements.');
          setLoading(false);
          return;
        }
        const { error } = await supabase.auth.updateUser({ password: newPassword });
        if (error) throw error;
        setMessage('Password updated successfully! You can now sign in.');
        // Clear the hash and switch to signin mode after a short delay
        window.history.replaceState(null, '', window.location.pathname);
        setTimeout(() => {
          setMode('signin');
          setMessage(null);
        }, 2000);
      } else {
        const { error } = await supabase.auth.signInWithPassword({
          email,
          password,
        });
        if (error) throw error;
        onSuccess();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
    } finally {
      setLoading(false);
    }
  };

  const handleOAuthSignIn = async (provider: 'google' | 'azure') => {
    setLoading(true);
    setError(null);

    try {
      const { error } = await supabase.auth.signInWithOAuth({
        provider,
        options: {
          redirectTo: `${window.location.origin}/auth/callback`,
          scopes: provider === 'azure' ? 'email profile openid' : undefined,
          // Force account selection prompt - prevents auto-selecting a previously used account
          queryParams: {
            prompt: 'select_account',
          },
        },
      });
      if (error) throw error;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      {/* Background effects */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-1/2 -right-1/4 w-[800px] h-[800px] rounded-full bg-gradient-to-br from-primary-600/20 to-transparent blur-3xl" />
        <div className="absolute -bottom-1/4 -left-1/4 w-[600px] h-[600px] rounded-full bg-gradient-to-tr from-emerald-600/10 to-transparent blur-3xl" />
      </div>

      <div className="relative z-10 w-full max-w-md">
        {/* Back button */}
        <button
          onClick={onBack}
          className="flex items-center gap-2 text-surface-400 hover:text-surface-200 transition-colors mb-8"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          Back to home
        </button>

        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gradient-to-br from-primary-500 to-primary-700 mb-4">
            <img src="/logo.svg" alt="Revtops" className="w-8 h-8 invert" />
          </div>
          <h1 className="text-2xl font-bold text-surface-50">
            {mode === 'signin' && 'Welcome back'}
            {mode === 'signup' && 'Create your account'}
            {mode === 'forgot' && 'Reset your password'}
            {mode === 'reset' && 'Set new password'}
          </h1>
          <p className="text-surface-400 mt-2">
            {mode === 'signin' && 'Sign in to access your revenue insights'}
            {mode === 'signup' && 'Start your free trial today'}
            {mode === 'forgot' && "Enter your email and we'll send you a reset link"}
            {mode === 'reset' && 'Choose a new password for your account'}
          </p>
        </div>

        {/* Auth Card */}
        <div className="bg-surface-900/80 backdrop-blur-sm border border-surface-800 rounded-2xl p-8">
          {/* OAuth buttons - only show for signin/signup */}
          {(mode === 'signin' || mode === 'signup') && (
            <>
              <div className="space-y-3 mb-8">
                <button
                  onClick={() => handleOAuthSignIn('google')}
                  disabled={loading}
                  className="w-full flex items-center justify-center gap-3 px-4 py-3.5 rounded-xl bg-surface-800 border border-surface-700 hover:bg-surface-700 hover:border-surface-600 transition-all disabled:opacity-50"
                >
                  <svg className="w-5 h-5" viewBox="0 0 24 24">
                    <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                    <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                    <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                    <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
                  </svg>
                  <span className="text-surface-100 font-medium">Continue with Google</span>
                </button>

                <button
                  onClick={() => handleOAuthSignIn('azure')}
                  disabled={loading}
                  className="w-full flex items-center justify-center gap-3 px-4 py-3.5 rounded-xl bg-surface-800 border border-surface-700 hover:bg-surface-700 hover:border-surface-600 transition-all disabled:opacity-50"
                >
                  <svg className="w-5 h-5" viewBox="0 0 23 23">
                    <path fill="#f35325" d="M1 1h10v10H1z"/>
                    <path fill="#81bc06" d="M12 1h10v10H12z"/>
                    <path fill="#05a6f0" d="M1 12h10v10H1z"/>
                    <path fill="#ffba08" d="M12 12h10v10H12z"/>
                  </svg>
                  <span className="text-surface-100 font-medium">Continue with Microsoft</span>
                </button>
              </div>

              {/* Divider */}
              <div className="relative mb-8">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t border-surface-700"></div>
                </div>
                <div className="relative flex justify-center text-sm">
                  <span className="px-4 bg-surface-900 text-surface-500">or continue with email</span>
                </div>
              </div>
            </>
          )}

          {/* Email form */}
          <form onSubmit={handleSubmit} className="space-y-5">
            {mode === 'signup' && (
              <div>
                <label htmlFor="name" className="block text-sm font-medium text-surface-300 mb-2">
                  Name
                </label>
                <input
                  id="name"
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="input"
                  placeholder="Your name"
                />
              </div>
            )}

            {/* Email field - show for signin, signup, forgot (not reset) */}
            {mode !== 'reset' && (
              <div>
                <label htmlFor="email" className="block text-sm font-medium text-surface-300 mb-2">
                  Email
                </label>
                <input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  className="input"
                  placeholder="you@company.com"
                />
              </div>
            )}

            {/* Password field - show for signin and signup only */}
            {(mode === 'signin' || mode === 'signup') && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label htmlFor="password" className="block text-sm font-medium text-surface-300">
                    Password
                  </label>
                  {mode === 'signin' && (
                    <button
                      type="button"
                      onClick={() => {
                        setMode('forgot');
                        setError(null);
                        setMessage(null);
                      }}
                      className="text-sm text-primary-400 hover:text-primary-300 transition-colors"
                    >
                      Forgot password?
                    </button>
                  )}
                </div>
                <input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={12}
                  className="input"
                  placeholder="••••••••"
                />
                {mode === 'signup' && (
                  <p className="text-xs text-surface-500 mt-2">
                    Use 12+ characters and at least 3 of: uppercase, lowercase, number, symbol.
                  </p>
                )}
              </div>
            )}

            {/* New password fields - show for reset mode */}
            {mode === 'reset' && (
              <>
                <div>
                  <label htmlFor="newPassword" className="block text-sm font-medium text-surface-300 mb-2">
                    New Password
                  </label>
                  <input
                    id="newPassword"
                    type="password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    required
                    minLength={12}
                    className="input"
                    placeholder="••••••••"
                  />
                </div>
                <div>
                  <label htmlFor="confirmPassword" className="block text-sm font-medium text-surface-300 mb-2">
                    Confirm Password
                  </label>
                  <input
                    id="confirmPassword"
                    type="password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    required
                    minLength={12}
                    className="input"
                    placeholder="••••••••"
                  />
                </div>
              </>
            )}

            {error && (
              <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
                {error}
              </div>
            )}

            {message && (
              <div className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-sm">
                {message}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="btn-primary w-full py-3.5 text-base disabled:opacity-50 disabled:cursor-not-allowed mt-2"
            >
              {loading ? (
                <span className="inline-flex items-center justify-center gap-2">
                  <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  {mode === 'signin' && 'Signing in...'}
                  {mode === 'signup' && 'Creating account...'}
                  {mode === 'forgot' && 'Sending reset link...'}
                  {mode === 'reset' && 'Updating password...'}
                </span>
              ) : (
                <>
                  {mode === 'signin' && 'Sign In'}
                  {mode === 'signup' && 'Create Account'}
                  {mode === 'forgot' && 'Send Reset Link'}
                  {mode === 'reset' && 'Update Password'}
                </>
              )}
            </button>
          </form>

          {/* Toggle mode / Back links */}
          <p className="text-center text-surface-400 text-sm mt-8">
            {mode === 'signin' && (
              <>
                Don't have an account?{' '}
                <button
                  onClick={() => {
                    setMode('signup');
                    setError(null);
                    setMessage(null);
                  }}
                  className="text-primary-400 hover:text-primary-300 font-medium transition-colors"
                >
                  Sign up
                </button>
              </>
            )}
            {mode === 'signup' && (
              <>
                Already have an account?{' '}
                <button
                  onClick={() => {
                    setMode('signin');
                    setError(null);
                    setMessage(null);
                  }}
                  className="text-primary-400 hover:text-primary-300 font-medium transition-colors"
                >
                  Sign in
                </button>
              </>
            )}
            {(mode === 'forgot' || mode === 'reset') && (
              <>
                Remember your password?{' '}
                <button
                  onClick={() => {
                    setMode('signin');
                    setError(null);
                    setMessage(null);
                    window.history.replaceState(null, '', window.location.pathname);
                  }}
                  className="text-primary-400 hover:text-primary-300 font-medium transition-colors"
                >
                  Sign in
                </button>
              </>
            )}
          </p>
        </div>
      </div>
    </div>
  );
}
