/**
 * Search component for finding deals and accounts.
 * 
 * Provides unified search across CRM data with type-specific result formatting.
 */

import { useState, useCallback, useRef, useEffect } from 'react';
import { searchData, type DealSearchResult, type AccountSearchResult } from '../api/client';
import { useAppStore } from '../store';

interface SearchProps {
  organizationId: string;
}

export function Search({ organizationId }: SearchProps): JSX.Element {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<{
    deals: DealSearchResult[];
    accounts: AccountSearchResult[];
    totalDeals: number;
    totalAccounts: number;
  } | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const searchTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  
  const setCurrentView = useAppStore((state) => state.setCurrentView);
  const startNewChat = useAppStore((state) => state.startNewChat);
  const setPendingChatInput = useAppStore((state) => state.setPendingChatInput);
  const setPendingChatAutoSend = useAppStore((state) => state.setPendingChatAutoSend);

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const performSearch = useCallback(async (searchQuery: string) => {
    if (!searchQuery.trim()) {
      setResults(null);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const response = await searchData(searchQuery, organizationId, 10);
      if (response.data) {
        setResults({
          deals: response.data.deals,
          accounts: response.data.accounts,
          totalDeals: response.data.total_deals,
          totalAccounts: response.data.total_accounts,
        });
      } else {
        setError(response.error ?? 'Search failed');
      }
    } catch {
      setError('Search failed');
    } finally {
      setIsLoading(false);
    }
  }, [organizationId]);

  const handleQueryChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setQuery(value);

    // Debounce search
    if (searchTimeoutRef.current) {
      clearTimeout(searchTimeoutRef.current);
    }

    if (value.trim().length >= 2) {
      searchTimeoutRef.current = setTimeout(() => {
        void performSearch(value);
      }, 300);
    } else {
      setResults(null);
    }
  }, [performSearch]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && query.trim().length >= 2) {
      if (searchTimeoutRef.current) {
        clearTimeout(searchTimeoutRef.current);
      }
      void performSearch(query);
    }
  }, [query, performSearch]);

  const handleAskAboutDeal = useCallback((deal: DealSearchResult) => {
    const question = `Tell me about the "${deal.name}" deal${deal.account_name ? ` with ${deal.account_name}` : ''}. What's the current status and any recent activity?`;
    setPendingChatInput(question);
    setPendingChatAutoSend(false);
    startNewChat();
    setCurrentView('chat');
  }, [startNewChat, setCurrentView, setPendingChatAutoSend, setPendingChatInput]);

  const handleAskAboutAccount = useCallback((account: AccountSearchResult) => {
    const question = `Tell me about ${account.name}${account.domain ? ` (${account.domain})` : ''}. What deals do we have with them and what's been happening recently?`;
    setPendingChatInput(question);
    setPendingChatAutoSend(false);
    startNewChat();
    setCurrentView('chat');
  }, [startNewChat, setCurrentView, setPendingChatAutoSend, setPendingChatInput]);

  const formatCurrency = (amount: number | null): string => {
    if (amount === null) return '—';
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    }).format(amount);
  };

  const formatDate = (dateStr: string | null): string => {
    if (!dateStr) return '—';
    return new Date(dateStr).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  };

  const hasResults = results && (results.deals.length > 0 || results.accounts.length > 0);
  const noResults = results && results.deals.length === 0 && results.accounts.length === 0;

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-surface-950">
      {/* Header */}
      <div className="px-6 py-4 border-b border-surface-800">
        <h1 className="text-xl font-semibold text-surface-100">Search</h1>
        <p className="text-sm text-surface-400 mt-1">
          Find deals and accounts across your CRM data
        </p>
      </div>

      {/* Search Input */}
      <div className="px-6 py-4">
        <div className="relative max-w-2xl">
          <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
            <svg
              className={`w-5 h-5 ${isLoading ? 'text-primary-400' : 'text-surface-500'}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
              />
            </svg>
          </div>
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={handleQueryChange}
            onKeyDown={handleKeyDown}
            placeholder="Search by deal name, account name, or domain..."
            className="w-full pl-12 pr-4 py-3 bg-surface-900 border border-surface-700 rounded-lg text-surface-100 placeholder-surface-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition-all"
          />
          {isLoading && (
            <div className="absolute inset-y-0 right-0 pr-4 flex items-center">
              <svg
                className="w-5 h-5 text-primary-400 animate-spin"
                fill="none"
                viewBox="0 0 24 24"
              >
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                />
              </svg>
            </div>
          )}
        </div>
      </div>

      {/* Results */}
      <div className="flex-1 overflow-y-auto px-6 pb-6">
        {error && (
          <div className="text-red-400 text-sm py-2">{error}</div>
        )}

        {/* Empty state - no query */}
        {!query && (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <div className="w-16 h-16 rounded-full bg-surface-800 flex items-center justify-center mb-4">
              <svg
                className="w-8 h-8 text-surface-500"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                />
              </svg>
            </div>
            <p className="text-surface-400 text-lg">Start typing to search</p>
            <p className="text-surface-500 text-sm mt-1">
              Search across your deals and accounts
            </p>
          </div>
        )}

        {/* No results */}
        {noResults && query.length >= 2 && (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <div className="w-16 h-16 rounded-full bg-surface-800 flex items-center justify-center mb-4">
              <svg
                className="w-8 h-8 text-surface-500"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M12 12h.01"
                />
              </svg>
            </div>
            <p className="text-surface-400 text-lg">No results found</p>
            <p className="text-surface-500 text-sm mt-1">
              Try a different search term
            </p>
          </div>
        )}

        {/* Results */}
        {hasResults && (
          <div className="max-w-4xl space-y-6">
            {/* Deals Section */}
            {results.deals.length > 0 && (
              <div>
                <div className="flex items-center gap-2 mb-3">
                  <h2 className="text-sm font-semibold text-surface-300 uppercase tracking-wide">
                    Deals
                  </h2>
                  <span className="text-xs text-surface-500">
                    ({results.totalDeals} total)
                  </span>
                </div>
                <div className="space-y-2">
                  {results.deals.map((deal) => (
                    <div
                      key={deal.id}
                      className="bg-surface-900 border border-surface-800 rounded-lg p-4 hover:border-surface-700 transition-colors group"
                    >
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="w-2 h-2 rounded-full bg-primary-500 flex-shrink-0" />
                            <h3 className="font-medium text-surface-100 truncate">
                              {deal.name}
                            </h3>
                          </div>
                          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-sm text-surface-400">
                            <span className="font-medium text-surface-200">
                              {formatCurrency(deal.amount)}
                            </span>
                            {deal.stage && (
                              <span className="px-2 py-0.5 bg-surface-800 rounded text-xs">
                                {deal.stage}
                              </span>
                            )}
                            {deal.close_date && (
                              <span>Close: {formatDate(deal.close_date)}</span>
                            )}
                            {deal.account_name && (
                              <span className="text-surface-500">
                                {deal.account_name}
                              </span>
                            )}
                          </div>
                          {deal.owner_name && (
                            <div className="text-xs text-surface-500 mt-1">
                              Owner: {deal.owner_name}
                            </div>
                          )}
                        </div>
                        <button
                          onClick={() => handleAskAboutDeal(deal)}
                          className="px-3 py-1.5 text-xs font-medium text-primary-400 hover:text-primary-300 bg-primary-500/10 hover:bg-primary-500/20 rounded-md transition-colors opacity-0 group-hover:opacity-100"
                        >
                          Ask about
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Accounts Section */}
            {results.accounts.length > 0 && (
              <div>
                <div className="flex items-center gap-2 mb-3">
                  <h2 className="text-sm font-semibold text-surface-300 uppercase tracking-wide">
                    Accounts
                  </h2>
                  <span className="text-xs text-surface-500">
                    ({results.totalAccounts} total)
                  </span>
                </div>
                <div className="space-y-2">
                  {results.accounts.map((account) => (
                    <div
                      key={account.id}
                      className="bg-surface-900 border border-surface-800 rounded-lg p-4 hover:border-surface-700 transition-colors group"
                    >
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="w-2 h-2 rounded-full bg-green-500 flex-shrink-0" />
                            <h3 className="font-medium text-surface-100 truncate">
                              {account.name}
                            </h3>
                          </div>
                          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-sm text-surface-400">
                            {account.annual_revenue && (
                              <span className="font-medium text-surface-200">
                                {formatCurrency(account.annual_revenue)} ARR
                              </span>
                            )}
                            {account.industry && (
                              <span className="px-2 py-0.5 bg-surface-800 rounded text-xs">
                                {account.industry}
                              </span>
                            )}
                            {account.domain && (
                              <span className="text-surface-500">
                                {account.domain}
                              </span>
                            )}
                            <span className="text-surface-500">
                              {account.deal_count} deal{account.deal_count !== 1 ? 's' : ''}
                            </span>
                          </div>
                        </div>
                        <button
                          onClick={() => handleAskAboutAccount(account)}
                          className="px-3 py-1.5 text-xs font-medium text-primary-400 hover:text-primary-300 bg-primary-500/10 hover:bg-primary-500/20 rounded-md transition-colors opacity-0 group-hover:opacity-100"
                        >
                          Ask about
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
