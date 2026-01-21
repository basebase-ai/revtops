/**
 * Company setup component.
 *
 * Shown when a user signs up with a new company domain.
 * Asks them to provide a display name for their company.
 */

import { useState } from 'react';

interface CompanySetupProps {
  emailDomain: string;
  onComplete: (companyName: string) => void;
  onBack: () => void;
}

export function CompanySetup({ emailDomain, onComplete, onBack }: CompanySetupProps): JSX.Element {
  const [companyName, setCompanyName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Generate a suggested name from the domain
  const suggestedName = emailDomain
    .replace(/\.(com|co|io|org|net|ai|app|dev|xyz)(\.[a-z]{2})?$/i, '')
    .split(/[.-]/)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!companyName.trim()) {
      setError('Please enter your company name');
      return;
    }

    setLoading(true);
    setError(null);

    try {
      // In production, this would call an API to create/update the company
      // For now, we'll just pass the name to the parent
      onComplete(companyName.trim());
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
        <div className="absolute -bottom-1/4 -left-1/4 w-[600px] h-[600px] rounded-full bg-gradient-to-tr from-primary-600/10 to-transparent blur-3xl" />
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
          Sign out
        </button>

        {/* Header */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gradient-to-br from-primary-500 to-primary-700 mb-4">
            <svg className="w-7 h-7 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-surface-50">Set up your company</h1>
          <p className="text-surface-400 mt-2">
            You're the first person from <span className="text-primary-400 font-medium">@{emailDomain}</span>
          </p>
        </div>

        {/* Setup Card */}
        <div className="bg-surface-900/80 backdrop-blur-sm border border-surface-800 rounded-2xl p-8">
          <form onSubmit={handleSubmit} className="space-y-6">
            <div>
              <label htmlFor="companyName" className="block text-sm font-medium text-surface-300 mb-2">
                Company name
              </label>
              <input
                id="companyName"
                type="text"
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                className="input"
                placeholder={suggestedName || 'Acme Corporation'}
                autoFocus
              />
              <p className="text-xs text-surface-500 mt-2">
                This is how your company will appear in Revtops
              </p>
            </div>

            {/* Quick suggestions */}
            {suggestedName && !companyName && (
              <div>
                <p className="text-xs text-surface-500 mb-2">Suggestion based on your email:</p>
                <button
                  type="button"
                  onClick={() => setCompanyName(suggestedName)}
                  className="px-3 py-1.5 rounded-lg bg-surface-800 border border-surface-700 text-surface-300 text-sm hover:bg-surface-700 transition-colors"
                >
                  {suggestedName}
                </button>
              </div>
            )}

            {error && (
              <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading || !companyName.trim()}
              className="btn-primary w-full py-3.5 text-base disabled:opacity-50 disabled:cursor-not-allowed"
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

          <p className="text-center text-surface-500 text-xs mt-6">
            Your teammates from @{emailDomain} will automatically join this workspace
          </p>
        </div>
      </div>
    </div>
  );
}
