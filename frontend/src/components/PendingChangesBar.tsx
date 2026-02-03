/**
 * PendingChangesBar - Shows pending local CRM changes above the chat input.
 * 
 * Follows the Cursor pattern: a small alert bar that appears when there are
 * uncommitted changes, with Commit/Undo buttons.
 */

import { useState, useEffect, useCallback } from 'react';
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

  // Poll for pending changes every 5 seconds
  useEffect(() => {
    fetchPendingChanges();
    const interval = setInterval(fetchPendingChanges, 5000);
    return () => clearInterval(interval);
  }, [fetchPendingChanges]);

  // Commit all pending changes
  const handleCommitAll = async (): Promise<void> => {
    setIsCommitting(true);
    setError(null);
    
    try {
      const { data, error: apiError } = await apiRequest<{ status: string; message: string; synced_count?: number; error_count?: number }>(
        `/change-sessions/commit-all?user_id=${userId}`,
        { method: 'POST', body: JSON.stringify({}) }
      );
      
      if (apiError) {
        setError(apiError);
      } else if (data?.error_count && data.error_count > 0) {
        setError(`Synced ${data.synced_count ?? 0} records, but ${data.error_count} failed`);
      }
      
      await fetchPendingChanges();
      onChangesResolved?.();
    } catch (err) {
      console.error('[PendingChangesBar] Failed to commit changes:', err);
      setError('Failed to commit changes to CRM');
    } finally {
      setIsCommitting(false);
    }
  };

  // Discard all pending changes
  const handleDiscardAll = async (): Promise<void> => {
    setIsDiscarding(true);
    setError(null);
    
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

  // Don't render if no pending changes
  if (!pendingData || pendingData.pending_count === 0) {
    return null;
  }

  // Count records by type
  const recordCounts: Record<string, number> = {};
  for (const session of pendingData.sessions) {
    for (const record of session.records) {
      const key = record.table === 'contacts' ? 'contact' : 
                  record.table === 'accounts' ? 'company' : 
                  record.table === 'deals' ? 'deal' : record.table;
      recordCounts[key] = (recordCounts[key] || 0) + 1;
    }
  }

  const totalRecords = Object.values(recordCounts).reduce((a, b) => a + b, 0);
  const recordSummary = Object.entries(recordCounts)
    .map(([type, count]) => `${count} ${type}${count > 1 ? 's' : ''}`)
    .join(', ');

  return (
    <div className="mb-2">
      {/* Main bar */}
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
    </div>
  );
}
