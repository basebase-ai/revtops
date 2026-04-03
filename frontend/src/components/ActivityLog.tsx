/**
 * Activity Log — displays the action ledger (connector mutation audit trail).
 */
import { useState, useEffect, useCallback } from 'react';
import { apiRequest } from '../lib/api';
import { useAppStore } from '../store';

interface LedgerEntry {
  id: string;
  connector: string;
  dispatch_type: string;
  operation: string;
  entity_type: string | null;
  entity_id: string | null;
  intent: { changes?: Record<string, unknown>; before_state?: Record<string, unknown> | null };
  outcome: { status: string; response?: Record<string, unknown>; error?: string } | null;
  status: string;
  created_at: string | null;
  executed_at: string | null;
  conversation_id: string | null;
  user_id: string | null;
}

interface LedgerResponse {
  entries: LedgerEntry[];
  total: number;
}

const STATUS_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  success: { bg: 'bg-emerald-500/10', text: 'text-emerald-400', label: 'Success' },
  error: { bg: 'bg-red-500/10', text: 'text-red-400', label: 'Error' },
  'in-flight': { bg: 'bg-amber-500/10', text: 'text-amber-400', label: 'In-flight' },
  unknown: { bg: 'bg-surface-700', text: 'text-surface-400', label: 'Unknown' },
};

function StatusBadge({ status }: { status: string }) {
  const s = STATUS_STYLES[status] ?? STATUS_STYLES.unknown!;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${s!.bg} ${s!.text}`}>
      {s!.label}
    </span>
  );
}

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

function ConnectorIcon({ connector }: { connector: string }) {
  const initials = connector.slice(0, 2).toUpperCase();
  return (
    <div className="w-8 h-8 rounded-md bg-surface-700 flex items-center justify-center text-xs font-bold text-surface-300 flex-shrink-0">
      {initials}
    </div>
  );
}

function EntryCard({ entry }: { entry: LedgerEntry }) {
  const [expanded, setExpanded] = useState(false);

  const entityLabel = entry.entity_type
    ? `${entry.entity_type}${entry.entity_id ? ` ${entry.entity_id}` : ''}`
    : null;

  return (
    <div className="border border-surface-700 rounded-lg p-3 hover:border-surface-600 transition-colors">
      <div
        className="flex items-start gap-3 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <ConnectorIcon connector={entry.connector} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-surface-100">{entry.connector}</span>
            <span className="text-surface-500">·</span>
            <span className="text-sm text-surface-300">{entry.operation}</span>
            {entityLabel && (
              <>
                <span className="text-surface-500">·</span>
                <span className="text-xs text-surface-400 truncate max-w-[200px]">{entityLabel}</span>
              </>
            )}
          </div>
          <div className="flex items-center gap-2 mt-1">
            <span className="text-xs text-surface-500">{formatTime(entry.created_at)}</span>
            <StatusBadge status={entry.status} />
            <span className="text-xs text-surface-600">{entry.dispatch_type}</span>
          </div>
        </div>
        <svg
          className={`w-4 h-4 text-surface-500 transition-transform flex-shrink-0 mt-1 ${expanded ? 'rotate-180' : ''}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </div>

      {expanded && (
        <div className="mt-3 pl-11 space-y-2 text-xs">
          {entry.intent?.before_state && (
            <div>
              <span className="font-medium text-surface-400">Before:</span>
              <pre className="mt-1 p-2 bg-surface-800 rounded overflow-x-auto text-surface-300 max-h-40">
                {JSON.stringify(entry.intent.before_state, null, 2)}
              </pre>
            </div>
          )}
          {entry.intent?.changes && (
            <div>
              <span className="font-medium text-surface-400">Changes sent:</span>
              <pre className="mt-1 p-2 bg-surface-800 rounded overflow-x-auto text-surface-300 max-h-40">
                {JSON.stringify(entry.intent.changes, null, 2)}
              </pre>
            </div>
          )}
          {entry.outcome && (
            <div>
              <span className="font-medium text-surface-400">Outcome:</span>
              <pre className="mt-1 p-2 bg-surface-800 rounded overflow-x-auto text-surface-300 max-h-40">
                {JSON.stringify(entry.outcome, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ActivityLog(): JSX.Element {
  const organization = useAppStore((s) => s.organization);
  const orgId = organization?.id;

  const [entries, setEntries] = useState<LedgerEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [connectorFilter, setConnectorFilter] = useState('');

  const LIMIT = 50;

  const fetchEntries = useCallback(async (currentOffset: number, append: boolean) => {
    if (!orgId) return;
    setLoading(true);
    try {
      let url = `/action-ledger/${orgId}?limit=${LIMIT}&offset=${currentOffset}`;
      if (connectorFilter) url += `&connector=${encodeURIComponent(connectorFilter)}`;

      const { data, error: apiErr } = await apiRequest<LedgerResponse>(url);
      if (apiErr) {
        setError(apiErr);
      } else if (data) {
        setEntries(prev => append ? [...prev, ...data.entries] : data.entries);
        setTotal(data.total);
        setError(null);
      }
    } catch {
      setError('Failed to load activity log');
    } finally {
      setLoading(false);
    }
  }, [orgId, connectorFilter]);

  useEffect(() => {
    setOffset(0);
    fetchEntries(0, false);
  }, [fetchEntries]);

  const loadMore = () => {
    const next = offset + LIMIT;
    setOffset(next);
    fetchEntries(next, true);
  };

  if (!orgId) {
    return <div className="p-6 text-surface-400">No organization selected.</div>;
  }

  return (
    <div className="h-full overflow-y-auto">
    <div className="max-w-4xl mx-auto px-4 py-6 sm:px-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-surface-100">Activity Log</h1>
          <p className="text-sm text-surface-400 mt-1">
            Audit trail of all external writes and actions
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={connectorFilter}
            onChange={(e) => setConnectorFilter(e.target.value)}
            className="bg-surface-800 border border-surface-700 rounded-md px-3 py-1.5 text-sm text-surface-200 focus:outline-none focus:ring-1 focus:ring-primary-500"
          >
            <option value="">All connectors</option>
            <option value="hubspot">HubSpot</option>
            <option value="google_drive">Google Drive</option>
            <option value="google_mail">Google Mail</option>
            <option value="slack">Slack</option>
            <option value="linear">Linear</option>
          </select>
        </div>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
          {error}
        </div>
      )}

      <div className="space-y-2">
        {entries.map((entry) => (
          <EntryCard key={entry.id} entry={entry} />
        ))}
      </div>

      {!loading && entries.length === 0 && !error && (
        <div className="text-center py-16 text-surface-500">
          <svg className="w-12 h-12 mx-auto mb-3 text-surface-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
          </svg>
          <p>No actions recorded yet.</p>
          <p className="text-xs mt-1">Actions will appear here when connectors write to external systems.</p>
        </div>
      )}

      {loading && (
        <div className="flex justify-center py-8">
          <div className="animate-spin w-6 h-6 border-2 border-surface-500 border-t-primary-500 rounded-full" />
        </div>
      )}

      {!loading && entries.length < total && (
        <div className="flex justify-center mt-4">
          <button
            onClick={loadMore}
            className="px-4 py-2 text-sm bg-surface-800 hover:bg-surface-700 border border-surface-700 rounded-md text-surface-300 transition-colors"
          >
            Load more ({total - entries.length} remaining)
          </button>
        </div>
      )}
    </div>
    </div>
  );
}
