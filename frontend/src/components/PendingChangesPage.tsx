/**
 * PendingChangesPage – Full-page review screen for pending CRM changes.
 *
 * Accessible via sidebar "Pending changes" item or `/changes` URL.
 * Shows all pending change sessions with per-session and global Commit/Discard.
 */

import { useState, useEffect, useCallback } from 'react';
import { apiRequest } from '../lib/api';
import { useAppStore } from '../store';

// ── Types ──────────────────────────────────────────────────────────────────

interface RecordInfo {
  table: string;
  operation: string;
  record_id: string;
  name?: string | null;
  email?: string | null;
  domain?: string | null;
  amount?: number | null;
}

interface ChangeSessionSummary {
  id: string;
  status: string;
  description: string | null;
  created_at: string;
  record_count: number;
  records: RecordInfo[];
  conversation_id: string | null;
  source_title: string | null;
  source_type: string | null; // 'workflow' | 'chat'
}

interface PendingChangesResponse {
  pending_count: number;
  sessions: ChangeSessionSummary[];
}

interface ActionResponse {
  status: string;
  message: string;
  synced_count?: number;
  error_count?: number;
  deleted_count?: number;
  errors?: Array<{ table: string; record_id: string; error: string }>;
}

// ── Helpers ────────────────────────────────────────────────────────────────

function friendlyTable(table: string): string {
  if (table === 'contacts') return 'Contact';
  if (table === 'accounts') return 'Company';
  if (table === 'deals') return 'Deal';
  return table;
}

function recordLabel(r: RecordInfo): string {
  return r.name ?? r.email ?? r.domain ?? r.record_id;
}

function friendlyDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

// ── Component ──────────────────────────────────────────────────────────────

export function PendingChangesPage(): JSX.Element {
  const user = useAppStore((s) => s.user);
  const setCurrentView = useAppStore((s) => s.setCurrentView);
  const setCurrentChatId = useAppStore((s) => s.setCurrentChatId);

  const userId: string | null = user?.id ?? null;

  const [data, setData] = useState<PendingChangesResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [busySession, setBusySession] = useState<string | null>(null); // session id being acted on
  const [busyAll, setBusyAll] = useState<'commit' | 'discard' | null>(null);
  const [expandedSessions, setExpandedSessions] = useState<Set<string>>(new Set());

  // Fetch pending changes
  const fetchPending = useCallback(async () => {
    if (!userId) return;
    try {
      const { data: res, error: apiErr } = await apiRequest<PendingChangesResponse>(
        `/change-sessions/pending?user_id=${userId}`,
      );
      if (apiErr) {
        setError(apiErr);
        setData(null);
      } else {
        setData(res);
        setError(null);
      }
    } catch {
      setError('Failed to load pending changes');
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [userId]);

  // Initial load + event-driven refresh
  useEffect(() => {
    void fetchPending();

    const handleUpdate = (): void => {
      void fetchPending();
    };
    window.addEventListener('pending-changes-updated', handleUpdate);
    return () => window.removeEventListener('pending-changes-updated', handleUpdate);
  }, [fetchPending]);

  // ── Per-session actions ─────────────────────────────────────────────────

  const commitSession = async (sessionId: string): Promise<void> => {
    setBusySession(sessionId);
    setError(null);
    try {
      const { data: res, error: apiErr } = await apiRequest<ActionResponse>(
        `/change-sessions/${sessionId}/commit?user_id=${userId}`,
        { method: 'POST', body: JSON.stringify({}) },
      );
      if (apiErr) setError(apiErr);
      else if (res?.error_count && res.error_count > 0) {
        setError(`Synced ${res.synced_count ?? 0} records, but ${res.error_count} failed`);
      }
      await fetchPending();
      window.dispatchEvent(new Event('pending-changes-updated'));
    } catch {
      setError('Failed to commit session');
    } finally {
      setBusySession(null);
    }
  };

  const discardSession = async (sessionId: string): Promise<void> => {
    setBusySession(sessionId);
    setError(null);
    try {
      const { error: apiErr } = await apiRequest<ActionResponse>(
        `/change-sessions/${sessionId}/discard?user_id=${userId}`,
        { method: 'POST', body: JSON.stringify({}) },
      );
      if (apiErr) setError(apiErr);
      await fetchPending();
      window.dispatchEvent(new Event('pending-changes-updated'));
    } catch {
      setError('Failed to discard session');
    } finally {
      setBusySession(null);
    }
  };

  // ── Global actions ──────────────────────────────────────────────────────

  const commitAll = async (): Promise<void> => {
    setBusyAll('commit');
    setError(null);
    try {
      const { data: res, error: apiErr } = await apiRequest<ActionResponse>(
        `/change-sessions/commit-all?user_id=${userId}`,
        { method: 'POST', body: JSON.stringify({}) },
      );
      if (apiErr) setError(apiErr);
      else if (res?.error_count && res.error_count > 0) {
        setError(`Synced ${res.synced_count ?? 0} records, but ${res.error_count} failed`);
      }
      await fetchPending();
      window.dispatchEvent(new Event('pending-changes-updated'));
    } catch {
      setError('Failed to commit all changes');
    } finally {
      setBusyAll(null);
    }
  };

  const discardAll = async (): Promise<void> => {
    setBusyAll('discard');
    setError(null);
    try {
      const { error: apiErr } = await apiRequest<ActionResponse>(
        `/change-sessions/discard-all?user_id=${userId}`,
        { method: 'POST', body: JSON.stringify({}) },
      );
      if (apiErr) setError(apiErr);
      await fetchPending();
      window.dispatchEvent(new Event('pending-changes-updated'));
    } catch {
      setError('Failed to discard all changes');
    } finally {
      setBusyAll(null);
    }
  };

  // ── Navigate to source conversation ─────────────────────────────────────

  const goToConversation = (conversationId: string): void => {
    setCurrentChatId(conversationId);
    setCurrentView('chat');
  };

  // ── Toggle expand ───────────────────────────────────────────────────────

  const toggleExpand = (id: string): void => {
    setExpandedSessions((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // ── Total record count across all sessions ──────────────────────────────

  const totalRecords: number = data?.sessions.reduce((sum, s) => sum + s.record_count, 0) ?? 0;

  // ── Render ──────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-surface-400">Loading pending changes...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Header */}
      <header className="h-14 border-b border-surface-800 flex items-center justify-between px-6 flex-shrink-0">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold text-surface-100">Pending Changes</h1>
          {totalRecords > 0 && (
            <span className="px-2 py-0.5 text-xs font-semibold rounded-full bg-amber-500/20 text-amber-400">
              {totalRecords} record{totalRecords !== 1 ? 's' : ''}
            </span>
          )}
        </div>

        {/* Global actions */}
        {data && data.sessions.length > 0 && (
          <div className="flex items-center gap-2">
            <button
              onClick={() => void discardAll()}
              disabled={busyAll !== null || busySession !== null}
              className="px-4 py-1.5 text-sm font-medium rounded-lg border border-surface-600 text-surface-300 hover:bg-surface-800 disabled:opacity-40 transition-colors"
            >
              {busyAll === 'discard' ? 'Discarding...' : 'Discard All'}
            </button>
            <button
              onClick={() => void commitAll()}
              disabled={busyAll !== null || busySession !== null}
              className="px-4 py-1.5 text-sm font-medium rounded-lg bg-primary-600 hover:bg-primary-500 text-white disabled:opacity-40 transition-colors"
            >
              {busyAll === 'commit' ? 'Committing...' : 'Commit All'}
            </button>
          </div>
        )}
      </header>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-6">
        {/* Error banner */}
        {error && (
          <div className="mb-4 px-4 py-2 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
            {error}
          </div>
        )}

        {/* Empty state */}
        {(!data || data.sessions.length === 0) && !error && (
          <div className="flex flex-col items-center justify-center py-24 text-center">
            <div className="w-16 h-16 rounded-2xl bg-surface-800 flex items-center justify-center mb-4">
              <svg className="w-8 h-8 text-surface-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <h2 className="text-lg font-medium text-surface-200 mb-1">No pending changes</h2>
            <p className="text-surface-400 text-sm max-w-sm">
              When you ask the agent to create or update CRM records, they'll appear here for review before syncing to HubSpot.
            </p>
          </div>
        )}

        {/* Session list */}
        {data && data.sessions.length > 0 && (
          <div className="max-w-3xl mx-auto space-y-3">
            {data.sessions.map((session) => {
              const isExpanded: boolean = expandedSessions.has(session.id);
              const isBusy: boolean = busySession === session.id || busyAll !== null;

              return (
                <div
                  key={session.id}
                  className="bg-surface-900 border border-surface-700 rounded-xl overflow-hidden"
                >
                  {/* Session header */}
                  <button
                    type="button"
                    onClick={() => toggleExpand(session.id)}
                    className="w-full flex items-center justify-between px-4 py-3 hover:bg-surface-800/50 transition-colors text-left"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      {/* Chevron */}
                      <svg
                        className={`w-4 h-4 text-surface-500 transition-transform ${isExpanded ? 'rotate-90' : ''}`}
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>

                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-surface-200 truncate">
                            {session.description ?? `${session.record_count} change${session.record_count !== 1 ? 's' : ''}`}
                          </span>
                          {session.source_type && (
                            <span className="flex items-center gap-1 text-xs text-surface-500">
                              {session.source_type === 'workflow' ? (
                                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                                </svg>
                              ) : (
                                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                                </svg>
                              )}
                              {session.source_title ?? session.source_type}
                            </span>
                          )}
                        </div>
                        <span className="text-xs text-surface-500">{friendlyDate(session.created_at)}</span>
                      </div>
                    </div>

                    <span className="text-xs font-medium text-surface-400 tabular-nums flex-shrink-0 ml-2">
                      {session.record_count} record{session.record_count !== 1 ? 's' : ''}
                    </span>
                  </button>

                  {/* Expanded details */}
                  {isExpanded && (
                    <div className="border-t border-surface-800 px-4 py-3 space-y-3">
                      {/* Record list */}
                      <div className="space-y-1.5">
                        {session.records.map((record, idx) => (
                          <div
                            key={`${record.record_id}-${idx}`}
                            className="flex items-center gap-2 text-sm"
                          >
                            <span className="text-green-500 text-xs">+</span>
                            <span className="px-1.5 py-0.5 rounded bg-surface-800 text-surface-400 text-xs font-medium">
                              {friendlyTable(record.table)}
                            </span>
                            <span className="text-surface-200 truncate">
                              {recordLabel(record)}
                            </span>
                            {record.amount != null && (
                              <span className="text-surface-400 text-xs ml-auto tabular-nums">
                                ${Number(record.amount).toLocaleString()}
                              </span>
                            )}
                          </div>
                        ))}
                      </div>

                      {/* Source link */}
                      {session.conversation_id && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            goToConversation(session.conversation_id!);
                          }}
                          className="text-xs text-primary-400 hover:text-primary-300 transition-colors"
                        >
                          View conversation &rarr;
                        </button>
                      )}

                      {/* Per-session actions */}
                      <div className="flex items-center gap-2 pt-2 border-t border-surface-800">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            void discardSession(session.id);
                          }}
                          disabled={isBusy}
                          className="px-3 py-1 text-xs font-medium rounded-md border border-surface-600 text-surface-300 hover:bg-surface-800 disabled:opacity-40 transition-colors"
                        >
                          Discard
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            void commitSession(session.id);
                          }}
                          disabled={isBusy}
                          className="px-3 py-1 text-xs font-medium rounded-md bg-primary-600 hover:bg-primary-500 text-white disabled:opacity-40 transition-colors"
                        >
                          {busySession === session.id ? 'Working...' : 'Commit'}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
