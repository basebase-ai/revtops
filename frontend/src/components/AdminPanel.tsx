/**
 * Admin Panel for global admins.
 * 
 * Features:
 * - Waitlist management (invite users)
 * - Future: User management, org management, data source debugging
 */

import { useEffect, useState, useCallback } from 'react';
import { API_BASE } from '../lib/api';
import { useAppStore } from '../store';

type AdminTab = 'waitlist' | 'users' | 'organizations';

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

interface AdminUser {
  id: string;
  email: string;
  first_name: string | null;
  last_name: string | null;
  status: string;
  last_login: string | null;
  created_at: string | null;
  organization_id: string | null;
  organization_name: string | null;
}

interface AdminOrganization {
  id: string;
  name: string;
  email_domain: string | null;
  user_count: number;
  created_at: string | null;
  last_sync_at: string | null;
}

export function AdminPanel(): JSX.Element {
  const user = useAppStore((state) => state.user);
  const [activeTab, setActiveTab] = useState<AdminTab>('waitlist');
  const [entries, setEntries] = useState<WaitlistEntry[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'waitlist' | 'invited'>('waitlist');
  const [inviting, setInviting] = useState<string | null>(null);

  // Users tab state
  const [adminUsers, setAdminUsers] = useState<AdminUser[]>([]);
  const [usersLoading, setUsersLoading] = useState<boolean>(true);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [userSearch, setUserSearch] = useState<string>('');

  // Organizations tab state
  const [adminOrgs, setAdminOrgs] = useState<AdminOrganization[]>([]);
  const [orgsLoading, setOrgsLoading] = useState<boolean>(true);
  const [orgsError, setOrgsError] = useState<string | null>(null);
  const [orgSearch, setOrgSearch] = useState<string>('');

  const fetchWaitlist = useCallback(async (): Promise<void> => {
    if (!user) return;
    
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(
        `${API_BASE}/waitlist/admin/list?status=${filter}&user_id=${user.id}`
      );

      if (!response.ok) {
        if (response.status === 403) {
          setError('Access denied. You need global_admin role.');
        } else {
          setError('Failed to fetch waitlist');
        }
        setEntries([]);
        return;
      }

      const data = await response.json() as { entries: WaitlistEntry[]; total: number };
      setEntries(data.entries);
    } catch (err) {
      setError('Failed to connect to server');
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }, [filter, user]);

  const fetchUsers = useCallback(async (): Promise<void> => {
    if (!user) return;
    
    setUsersLoading(true);
    setUsersError(null);

    try {
      const response = await fetch(
        `${API_BASE}/waitlist/admin/users?user_id=${user.id}`
      );

      if (!response.ok) {
        if (response.status === 403) {
          setUsersError('Access denied. You need global_admin role.');
        } else {
          setUsersError('Failed to fetch users');
        }
        setAdminUsers([]);
        return;
      }

      const data = await response.json() as { users: AdminUser[]; total: number };
      setAdminUsers(data.users);
    } catch (err) {
      setUsersError('Failed to connect to server');
      setAdminUsers([]);
    } finally {
      setUsersLoading(false);
    }
  }, [user]);

  const fetchOrganizations = useCallback(async (): Promise<void> => {
    if (!user) return;
    
    setOrgsLoading(true);
    setOrgsError(null);

    try {
      const response = await fetch(
        `${API_BASE}/waitlist/admin/organizations?user_id=${user.id}`
      );

      if (!response.ok) {
        if (response.status === 403) {
          setOrgsError('Access denied. You need global_admin role.');
        } else {
          setOrgsError('Failed to fetch organizations');
        }
        setAdminOrgs([]);
        return;
      }

      const data = await response.json() as { organizations: AdminOrganization[]; total: number };
      setAdminOrgs(data.organizations);
    } catch (err) {
      setOrgsError('Failed to connect to server');
      setAdminOrgs([]);
    } finally {
      setOrgsLoading(false);
    }
  }, [user]);

  useEffect(() => {
    if (activeTab === 'waitlist') {
      void fetchWaitlist();
    } else if (activeTab === 'users') {
      void fetchUsers();
    } else if (activeTab === 'organizations') {
      void fetchOrganizations();
    }
  }, [activeTab, fetchWaitlist, fetchUsers, fetchOrganizations]);

  const handleInvite = async (targetUserId: string): Promise<void> => {
    if (!user) return;
    
    setInviting(targetUserId);

    try {
      const response = await fetch(
        `${API_BASE}/waitlist/admin/${targetUserId}/invite?user_id=${user.id}`,
        { method: 'POST' }
      );

      if (!response.ok) {
        const data = await response.json() as { detail?: string };
        throw new Error(data.detail ?? 'Failed to invite');
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
    // Backend returns UTC times without 'Z' suffix, so append it if missing
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

  const tabs: { id: AdminTab; label: string; available: boolean }[] = [
    { id: 'waitlist', label: 'Waitlist', available: true },
    { id: 'users', label: 'Users', available: true },
    { id: 'organizations', label: 'Organizations', available: true },
  ];

  // Filter users by search term (in-memory)
  const filteredUsers = adminUsers.filter((u) => {
    if (!userSearch.trim()) return true;
    const searchLower = userSearch.toLowerCase();
    const firstName = (u.first_name ?? '').toLowerCase();
    const lastName = (u.last_name ?? '').toLowerCase();
    const orgName = (u.organization_name ?? '').toLowerCase();
    const email = u.email.toLowerCase();
    return (
      firstName.includes(searchLower) ||
      lastName.includes(searchLower) ||
      orgName.includes(searchLower) ||
      email.includes(searchLower)
    );
  });

  // Filter organizations by search term (in-memory)
  const filteredOrgs = adminOrgs.filter((o) => {
    if (!orgSearch.trim()) return true;
    const searchLower = orgSearch.toLowerCase();
    const name = o.name.toLowerCase();
    const domain = (o.email_domain ?? '').toLowerCase();
    return name.includes(searchLower) || domain.includes(searchLower);
  });

  return (
    <div className="flex-1 overflow-y-auto">
      {/* Header */}
      <header className="sticky top-0 bg-surface-950 border-b border-surface-800 px-8 py-6">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-amber-500 to-orange-600 flex items-center justify-center">
            <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
          </div>
          <div>
            <h1 className="text-2xl font-bold text-surface-50">Admin Panel</h1>
            <p className="text-surface-400 mt-0.5">Manage Revtops platform</p>
          </div>
        </div>
      </header>

      <div className="max-w-6xl mx-auto px-8 py-6">
        {/* Tab Navigation */}
        <div className="flex gap-1 mb-6 border-b border-surface-800">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => tab.available && setActiveTab(tab.id)}
              disabled={!tab.available}
              className={`px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
                activeTab === tab.id
                  ? 'border-primary-500 text-primary-400'
                  : tab.available
                    ? 'border-transparent text-surface-400 hover:text-surface-200'
                    : 'border-transparent text-surface-600 cursor-not-allowed'
              }`}
            >
              {tab.label}
              {!tab.available && (
                <span className="ml-1.5 text-xs text-surface-600">(soon)</span>
              )}
            </button>
          ))}
        </div>

        {/* Waitlist Tab Content */}
        {activeTab === 'waitlist' && (
          <div className="space-y-6">
            {/* Filters & Actions */}
            <div className="flex items-center justify-between">
              <div className="flex gap-2">
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
              <button
                onClick={() => void fetchWaitlist()}
                disabled={loading}
                className="px-4 py-2 rounded-lg bg-surface-800 border border-surface-700 text-surface-300 hover:bg-surface-700 transition-colors disabled:opacity-50 flex items-center gap-2"
              >
                <svg className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                Refresh
              </button>
            </div>

            {/* Error */}
            {error && (
              <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400">
                {error}
              </div>
            )}

            {/* Loading */}
            {loading && (
              <div className="text-center py-12 text-surface-400">
                <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
                Loading waitlist...
              </div>
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
              <div className="text-sm text-surface-500 text-center">
                Showing {entries.length} {filter === 'all' ? 'total' : filter} users
              </div>
            )}
          </div>
        )}

        {/* Users Tab Content */}
        {activeTab === 'users' && (
          <div className="space-y-6">
            {/* Search & Actions */}
            <div className="flex items-center justify-between gap-4">
              <div className="relative flex-1 max-w-md">
                <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-surface-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                <input
                  type="text"
                  placeholder="Search by name, email, or organization..."
                  value={userSearch}
                  onChange={(e) => setUserSearch(e.target.value)}
                  className="w-full pl-10 pr-4 py-2 rounded-lg bg-surface-800 border border-surface-700 text-surface-100 placeholder-surface-500 focus:outline-none focus:border-primary-500"
                />
              </div>
              <button
                onClick={() => void fetchUsers()}
                disabled={usersLoading}
                className="px-4 py-2 rounded-lg bg-surface-800 border border-surface-700 text-surface-300 hover:bg-surface-700 transition-colors disabled:opacity-50 flex items-center gap-2"
              >
                <svg className={`w-4 h-4 ${usersLoading ? 'animate-spin' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                Refresh
              </button>
            </div>

            {/* Error */}
            {usersError && (
              <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400">
                {usersError}
              </div>
            )}

            {/* Loading */}
            {usersLoading && (
              <div className="text-center py-12 text-surface-400">
                <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
                Loading users...
              </div>
            )}

            {/* Empty state */}
            {!usersLoading && !usersError && filteredUsers.length === 0 && (
              <div className="text-center py-12">
                <div className="w-16 h-16 rounded-full bg-surface-800 flex items-center justify-center mx-auto mb-4">
                  <svg className="w-8 h-8 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
                  </svg>
                </div>
                <p className="text-surface-400">
                  {userSearch ? 'No users match your search' : 'No users found'}
                </p>
              </div>
            )}

            {/* Table */}
            {!usersLoading && !usersError && filteredUsers.length > 0 && (
              <div className="bg-surface-900 rounded-xl border border-surface-800 overflow-hidden">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-surface-800 text-left">
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">User</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Organization</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Status</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Last Login</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Joined</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-surface-800">
                    {filteredUsers.map((u) => (
                      <tr key={u.id} className="hover:bg-surface-800/50">
                        <td className="px-4 py-3">
                          <div>
                            <div className="font-medium text-surface-100">
                              {u.first_name || u.last_name
                                ? `${u.first_name ?? ''} ${u.last_name ?? ''}`.trim()
                                : 'Unknown'}
                            </div>
                            <div className="text-sm text-surface-400">{u.email}</div>
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <div className="text-surface-200">{u.organization_name ?? '—'}</div>
                        </td>
                        <td className="px-4 py-3">{getStatusBadge(u.status)}</td>
                        <td className="px-4 py-3 text-sm text-surface-400">
                          {u.last_login ? formatDate(u.last_login) : 'Never'}
                        </td>
                        <td className="px-4 py-3 text-sm text-surface-400">
                          {formatDate(u.created_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* Stats */}
            {!usersLoading && !usersError && (
              <div className="text-sm text-surface-500 text-center">
                Showing {filteredUsers.length} of {adminUsers.length} users
                {userSearch && ` matching "${userSearch}"`}
              </div>
            )}
          </div>
        )}

        {/* Organizations Tab Content */}
        {activeTab === 'organizations' && (
          <div className="space-y-6">
            {/* Search & Actions */}
            <div className="flex items-center justify-between gap-4">
              <div className="relative flex-1 max-w-md">
                <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-surface-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                <input
                  type="text"
                  placeholder="Search by name or domain..."
                  value={orgSearch}
                  onChange={(e) => setOrgSearch(e.target.value)}
                  className="w-full pl-10 pr-4 py-2 rounded-lg bg-surface-800 border border-surface-700 text-surface-100 placeholder-surface-500 focus:outline-none focus:border-primary-500"
                />
              </div>
              <button
                onClick={() => void fetchOrganizations()}
                disabled={orgsLoading}
                className="px-4 py-2 rounded-lg bg-surface-800 border border-surface-700 text-surface-300 hover:bg-surface-700 transition-colors disabled:opacity-50 flex items-center gap-2"
              >
                <svg className={`w-4 h-4 ${orgsLoading ? 'animate-spin' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                Refresh
              </button>
            </div>

            {/* Error */}
            {orgsError && (
              <div className="p-4 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400">
                {orgsError}
              </div>
            )}

            {/* Loading */}
            {orgsLoading && (
              <div className="text-center py-12 text-surface-400">
                <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
                Loading organizations...
              </div>
            )}

            {/* Empty state */}
            {!orgsLoading && !orgsError && filteredOrgs.length === 0 && (
              <div className="text-center py-12">
                <div className="w-16 h-16 rounded-full bg-surface-800 flex items-center justify-center mx-auto mb-4">
                  <svg className="w-8 h-8 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                  </svg>
                </div>
                <p className="text-surface-400">
                  {orgSearch ? 'No organizations match your search' : 'No organizations found'}
                </p>
              </div>
            )}

            {/* Table */}
            {!orgsLoading && !orgsError && filteredOrgs.length > 0 && (
              <div className="bg-surface-900 rounded-xl border border-surface-800 overflow-hidden">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-surface-800 text-left">
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Organization</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Domain</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Users</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Last Sync</th>
                      <th className="px-4 py-3 text-sm font-medium text-surface-400">Created</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-surface-800">
                    {filteredOrgs.map((o) => (
                      <tr key={o.id} className="hover:bg-surface-800/50">
                        <td className="px-4 py-3">
                          <div className="font-medium text-surface-100">{o.name}</div>
                        </td>
                        <td className="px-4 py-3">
                          <div className="text-surface-300">{o.email_domain ?? '—'}</div>
                        </td>
                        <td className="px-4 py-3">
                          <span className="px-2 py-0.5 rounded-full text-xs bg-surface-700 text-surface-300">
                            {o.user_count} {o.user_count === 1 ? 'user' : 'users'}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-sm text-surface-400">
                          {o.last_sync_at ? formatDate(o.last_sync_at) : 'Never'}
                        </td>
                        <td className="px-4 py-3 text-sm text-surface-400">
                          {formatDate(o.created_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* Stats */}
            {!orgsLoading && !orgsError && (
              <div className="text-sm text-surface-500 text-center">
                Showing {filteredOrgs.length} of {adminOrgs.length} organizations
                {orgSearch && ` matching "${orgSearch}"`}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
