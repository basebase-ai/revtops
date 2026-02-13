/**
 * PendingChangesBar - Shows pending local CRM changes above the chat input.
 * 
 * Follows the Cursor pattern: a small alert bar that appears when there are
 * uncommitted changes, with Commit/Undo buttons.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { apiRequest } from '../lib/api';

interface RecordInfo {
  table: string;
  operation: string;
  record_id: string;
  name?: string;
  email?: string;
  domain?: string;
  amount?: number;
}

interface ChangeSessionSummary {
  id: string;
  status: string;
  description: string | null;
  created_at: string;
  record_count: number;
  records: RecordInfo[];
}

interface PendingChangesResponse {
  pending_count: number;
  sessions: ChangeSessionSummary[];
}

interface CommitResult {
  status: 'success' | 'partial' | 'error';
  message: string;
  syncedCount: number;
  errorCount: number;
}

interface PendingChangesBarProps {
  organizationId: string;
  userId: string;
  /** Callback when changes are committed or discarded (to refresh chat state) */
  onChangesResolved?: () => void;
}

export function PendingChangesBar({ organizationId, userId, onChangesResolved }: PendingChangesBarProps): JSX.Element | null {
  const [pendingData, setPendingData] = useState<PendingChangesResponse | null>(null);
  const [isExpanded, setIsExpanded] = useState(false);
  const [isCommitting, setIsCommitting] = useState(false);
  const [isDiscarding, setIsDiscarding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [commitResult, setCommitResult] = useState<CommitResult | null>(null);
  const autoFadeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Fetch pending changes
  const fetchPendingChanges = useCallback(async () => {
    if (!organizationId || !userId) return;
    
    try {
      const { data, error: apiError } = await apiRequest<PendingChangesResponse>(`/change-sessions/pending?user_id=${userId}`);
      if (apiError) {
        console.error('[PendingChangesBar] API error:', apiError);
        setPendingData(null);
        return;
      }
      setPendingData(data);
      setError(null);
    } catch (err) {
      console.error('[PendingChangesBar] Failed to fetch pending changes:', err);
      // Don't show error to user, just hide the bar
      setPendingData(null);
    }
  }, [organizationId, userId]);

  // Fetch pending changes on mount and when 'pending-changes-updated' event fires
  // No polling - uses event-driven updates
  useEffect(() => {
    void fetchPendingChanges();
    
    // Listen for updates from WebSocket (e.g., when write_to_system_of_record tool completes)
    const handleUpdate = (): void => {
      void fetchPendingChanges();
    };
    window.addEventListener('pending-changes-updated', handleUpdate);
    return () => window.removeEventListener('pending-changes-updated', handleUpdate);
  }, [fetchPendingChanges]);

  // Clean up auto-fade timer on unmount
  useEffect(() => {
    return () => {
      if (autoFadeTimer.current) clearTimeout(autoFadeTimer.current);
    };
  }, []);

  const dismissResult = useCallback(() => {
    setCommitResult(null);
    if (autoFadeTimer.current) {
      clearTimeout(autoFadeTimer.current);
      autoFadeTimer.current = null;
    }
  }, []);

  // Commit all pending changes
  const handleCommitAll = async (): Promise<void> => {
    setIsCommitting(true);
    setError(null);
    setCommitResult(null);
    if (autoFadeTimer.current) clearTimeout(autoFadeTimer.current);
    
    try {
      const { data, error: apiError } = await apiRequest<{ status: string; message: string; synced_count?: number; error_count?: number; errors?: Array<{ table: string; record_id: string; error: string }> }>(
        `/change-sessions/commit-all?user_id=${userId}`,
        { method: 'POST', body: JSON.stringify({}) }
      );
      
      if (apiError) {
        setCommitResult({
          status: 'error',
          message: apiError,
          syncedCount: 0,
          errorCount: 0,
        });
      } else {
        const syncedCount: number = data?.synced_count ?? 0;
        const errorCount: number = data?.error_count ?? 0;

        if (errorCount > 0) {
          setCommitResult({
            status: 'partial',
            message: `Synced ${syncedCount} record${syncedCount !== 1 ? 's' : ''} to CRM. ${errorCount} record${errorCount !== 1 ? 's' : ''} failed (may have been deleted from CRM).`,
            syncedCount,
            errorCount,
          });
          // Partial results stay until dismissed — no auto-fade
        } else {
          setCommitResult({
            status: 'success',
            message: `Successfully synced ${syncedCount} record${syncedCount !== 1 ? 's' : ''} to CRM.`,
            syncedCount,
            errorCount: 0,
          });
          // Auto-dismiss success after 5 seconds
          autoFadeTimer.current = setTimeout(() => {
            setCommitResult(null);
            autoFadeTimer.current = null;
          }, 5000);
        }
      }
      
      await fetchPendingChanges();
      onChangesResolved?.();
    } catch (err) {
      console.error('[PendingChangesBar] Failed to commit changes:', err);
      setCommitResult({
        status: 'error',
        message: 'Failed to commit changes to CRM. Please try again.',
        syncedCount: 0,
        errorCount: 0,
      });
    } finally {
      setIsCommitting(false);
    }
  };

  // Discard all pending changes
  const handleDiscardAll = async (): Promise<void> => {
    setIsDiscarding(true);
    setError(null);
    setCommitResult(null);
    
    try {
      const { error: apiError } = await apiRequest(`/change-sessions/discard-all?user_id=${userId}`, { method: 'POST', body: JSON.stringify({}) });
      if (apiError) {
        setError(apiError);
      }
      await fetchPendingChanges();
      onChangesResolved?.();
    } catch (err) {
      console.error('[PendingChangesBar] Failed to discard changes:', err);
      setError('Failed to discard changes');
    } finally {
      setIsDiscarding(false);
    }
  };

  const hasPending: boolean = (pendingData?.pending_count ?? 0) > 0;

  // Show nothing only if no pending changes AND no result banner to display
  if (!hasPending && !commitResult) {
    return null;
  }

  return (
    <div className="mb-2 space-y-2">
      {/* Commit result banner — persists after pending bar disappears */}
      {commitResult && (
        <div
          className={`rounded-lg px-3 py-2 border ${
            commitResult.status === 'success'
              ? 'bg-emerald-950/50 border-emerald-800/60 text-emerald-300'
              : commitResult.status === 'partial'
              ? 'bg-amber-950/50 border-amber-800/60 text-amber-300'
              : 'bg-red-950/50 border-red-800/60 text-red-300'
          }`}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-start gap-2 min-w-0">
              <span className="flex-shrink-0 mt-0.5 text-sm">
                {commitResult.status === 'success' ? '✓' : commitResult.status === 'partial' ? '⚠' : '✗'}
              </span>
              <span className="text-sm">{commitResult.message}</span>
            </div>
            <button
              onClick={dismissResult}
              className="flex-shrink-0 text-xs opacity-60 hover:opacity-100 transition-opacity px-1"
              aria-label="Dismiss"
            >
              ✕
            </button>
          </div>
        </div>
      )}

      {/* Pending changes bar — only shown when there are pending changes */}
      {hasPending && pendingData && (() => {
        const recordCounts: Record<string, number> = {};
        for (const session of pendingData.sessions) {
          for (const record of session.records) {
            const key: string = record.table === 'contacts' ? 'contact' : 
                        record.table === 'accounts' ? 'company' : 
                        record.table === 'deals' ? 'deal' : record.table;
            recordCounts[key] = (recordCounts[key] || 0) + 1;
          }
        }
        const totalRecords: number = Object.values(recordCounts).reduce((a, b) => a + b, 0);
        const recordSummary: string = Object.entries(recordCounts)
          .map(([type, count]) => `${count} ${type}${count > 1 ? 's' : ''}`)
          .join(', ');

        return (
          <div className="bg-surface-800 border border-surface-600 rounded-lg px-3 py-2">
            <div className="flex items-center justify-between gap-3">
              {/* Left: Status */}
              <div className="flex items-center gap-2 min-w-0">
                <div className="flex-shrink-0 w-2 h-2 rounded-full bg-surface-400" />
                <span className="text-sm text-surface-200 truncate">
                  {totalRecords} pending change{totalRecords !== 1 ? 's' : ''} ({recordSummary})
                </span>
              </div>

              {/* Right: Actions */}
              <div className="flex items-center gap-2 flex-shrink-0">
                <button
                  onClick={() => setIsExpanded(!isExpanded)}
                  className="text-xs text-surface-400 hover:text-surface-200 transition-colors"
                >
                  {isExpanded ? 'Hide' : 'Review'}
                </button>
                
                <button
                  onClick={handleDiscardAll}
                  disabled={isCommitting || isDiscarding}
                  className="px-3 py-1 text-xs font-medium bg-surface-700 hover:bg-surface-600 disabled:opacity-50 text-surface-200 rounded transition-colors"
                >
                  {isDiscarding ? 'Undoing...' : 'Undo All'}
                </button>
                
                <button
                  onClick={handleCommitAll}
                  disabled={isCommitting || isDiscarding}
                  className="px-3 py-1 text-xs font-medium bg-surface-600 hover:bg-surface-500 disabled:opacity-50 text-white rounded transition-colors"
                >
                  {isCommitting ? 'Committing...' : 'Commit All'}
                </button>
              </div>
            </div>

            {/* Error message */}
            {error && (
              <div className="mt-2 text-xs text-red-400">
                {error}
              </div>
            )}

            {/* Expanded details */}
            {isExpanded && (
              <div className="mt-3 pt-3 border-t border-surface-600">
                <div className="space-y-2 max-h-40 overflow-y-auto">
                  {pendingData.sessions.map((session) => (
                    <div key={session.id} className="text-xs">
                      <div className="text-surface-300 font-medium mb-1">
                        {session.description || 'Pending changes'}
                      </div>
                      <div className="space-y-1 pl-3">
                        {session.records.map((record, idx) => (
                          <div key={`${record.record_id}-${idx}`} className="text-surface-400 flex items-center gap-2">
                            <span className="text-surface-500">+</span>
                            <span className="text-surface-500">{record.table}:</span>
                            <span className="text-surface-300">{record.name || record.email || record.domain || record.record_id}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        );
      })()}
    </div>
  );
}
