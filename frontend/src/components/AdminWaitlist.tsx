/**
 * Admin Waitlist management page.
 * 
 * Allows global admins to view and invite users from the waitlist.
 * Access controlled by JWT auth.
 */

import { useEffect, useState, useCallback } from 'react';
import { apiRequest } from '../lib/api';

interface WaitlistEntry {
  id: string;
  email: string;
  name: string | null;
  status: string;
  waitlist_data: {
    title?: string;
    company_name?: string;
    num_employees?: string;
    apps_of_interest?: string[];
    core_needs?: string[];
  } | null;
  waitlisted_at: string | null;
  invited_at: string | null;
  created_at: string | null;
}

export function AdminWaitlist(): JSX.Element {
  const [entries, setEntries] = useState<WaitlistEntry[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'waitlist' | 'invited'>('waitlist');
  const [inviting, setInviting] = useState<string | null>(null);

  const fetchWaitlist = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);

    try {
      const response = await apiRequest<{ entries: WaitlistEntry[]; total: number }>(
        `/waitlist/admin?status=${filter}`,
      );

      if (response.error || !response.data) {
        if (response.error?.includes('Global admin access required')) {
          setError('Access denied. You need global_admin role.');
        } else {
          setError('Failed to fetch waitlist');
        }
        setEntries([]);
        return;
      }

      setEntries(response.data.entries);
    } catch (err) {
      setError('Failed to connect to server');
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    void fetchWaitlist();
  }, [fetchWaitlist]);

  const handleInvite = async (userId: string): Promise<void> => {
    setInviting(userId);

    try {
      const response = await apiRequest<{ success: boolean; message: string; user_id: string }>(
        `/waitlist/admin/${userId}/invite`,
        { method: 'POST' },
      );

      if (response.error) {
        throw new Error(response.error);
      }

      // Refresh the list
      await fetchWaitlist();
    } catch (err) {
      console.error('Failed to invite:', err);
    } finally {
      setInviting(null);
    }
  };

  const formatDate = (dateStr: string | null): string => {
    if (!dateStr) return '—';
    // Backend returns UTC times, ensure proper parsing
    const utcDateStr = dateStr.endsWith('Z') || dateStr.includes('+') || dateStr.includes('-', 10)
      ? dateStr
      : `${dateStr}Z`;
    return new Date(utcDateStr).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  };

  const getStatusBadge = (status: string): JSX.Element => {
    const styles: Record<string, string> = {
      waitlist: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
      invited: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
      active: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
    };
    return (
      <span className={`px-2 py-0.5 rounded-full text-xs border ${styles[status] ?? styles.waitlist}`}>
        {status}
      </span>
    );
  };

  return (
    <div className="min-h-screen bg-surface-950 p-6">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-bold text-surface-50">Waitlist Management</h1>
            <p className="text-surface-400 mt-1">Review and invite users from the waitlist</p>
          </div>
          <button
            onClick={() => void fetchWaitlist()}
            disabled={loading}
            className="px-4 py-2 rounded-lg bg-surface-800 border border-surface-700 text-surface-300 hover:bg-surface-700 transition-colors disabled:opacity-50"
          >
            Refresh
          </button>
        </div>

        {/* Filters */}
        <div className="flex gap-2 mb-6">
          {(['waitlist', 'invited', 'all'] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                filter === f
                  ? 'bg-primary-500/20 text-primary-400 border border-primary-500/30'
                  : 'bg-surface-800 text-surface-400 border border-surface-700 hover:border-surface-600'
              }`}
            >
              {f === 'all' ? 'All' : f.charAt(0).toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>

        {/* Error */}
        {error && (
          <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 mb-6">
            {error}
          </div>
        )}

        {/* Loading */}
        {loading && (
          <div className="text-center py-12 text-surface-400">Loading...</div>
        )}

        {/* Empty state */}
        {!loading && !error && entries.length === 0 && (
          <div className="text-center py-12">
            <div className="w-16 h-16 rounded-full bg-surface-800 flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
              </svg>
            </div>
            <p className="text-surface-400">No {filter === 'all' ? '' : filter} users found</p>
          </div>
        )}

        {/* Table */}
        {!loading && !error && entries.length > 0 && (
          <div className="bg-surface-900 rounded-xl border border-surface-800 overflow-hidden">
            <table className="w-full">
              <thead>
                <tr className="border-b border-surface-800 text-left">
                  <th className="px-4 py-3 text-sm font-medium text-surface-400">User</th>
                  <th className="px-4 py-3 text-sm font-medium text-surface-400">Company</th>
                  <th className="px-4 py-3 text-sm font-medium text-surface-400">Apps</th>
                  <th className="px-4 py-3 text-sm font-medium text-surface-400">Status</th>
                  <th className="px-4 py-3 text-sm font-medium text-surface-400">Signed Up</th>
                  <th className="px-4 py-3 text-sm font-medium text-surface-400">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-800">
                {entries.map((entry) => (
                  <tr key={entry.id} className="hover:bg-surface-800/50">
                    <td className="px-4 py-3">
                      <div>
                        <div className="font-medium text-surface-100">{entry.name ?? 'Unknown'}</div>
                        <div className="text-sm text-surface-400">{entry.email}</div>
                        {entry.waitlist_data?.title && (
                          <div className="text-xs text-surface-500">{entry.waitlist_data.title}</div>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="text-surface-200">{entry.waitlist_data?.company_name ?? '—'}</div>
                      {entry.waitlist_data?.num_employees && (
                        <div className="text-xs text-surface-500">{entry.waitlist_data.num_employees} employees</div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1">
                        {entry.waitlist_data?.apps_of_interest?.slice(0, 3).map((app) => (
                          <span key={app} className="px-1.5 py-0.5 rounded bg-surface-700 text-xs text-surface-300">
                            {app}
                          </span>
                        ))}
                        {(entry.waitlist_data?.apps_of_interest?.length ?? 0) > 3 && (
                          <span className="px-1.5 py-0.5 text-xs text-surface-500">
                            +{(entry.waitlist_data?.apps_of_interest?.length ?? 0) - 3}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">{getStatusBadge(entry.status)}</td>
                    <td className="px-4 py-3 text-sm text-surface-400">
                      {formatDate(entry.waitlisted_at)}
                    </td>
                    <td className="px-4 py-3">
                      {entry.status === 'waitlist' ? (
                        <button
                          onClick={() => void handleInvite(entry.id)}
                          disabled={inviting === entry.id}
                          className="px-3 py-1.5 rounded-lg bg-primary-500 hover:bg-primary-600 text-white text-sm font-medium transition-colors disabled:opacity-50"
                        >
                          {inviting === entry.id ? 'Inviting...' : 'Invite'}
                        </button>
                      ) : entry.status === 'invited' ? (
                        <span className="text-sm text-surface-500">Invited {formatDate(entry.invited_at)}</span>
                      ) : (
                        <span className="text-sm text-emerald-400">Active</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Stats */}
        {!loading && !error && (
          <div className="mt-6 text-sm text-surface-500 text-center">
            Showing {entries.length} {filter === 'all' ? 'total' : filter} users
          </div>
        )}
      </div>
    </div>
  );
}
