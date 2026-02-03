/**
 * Change Session Card - displays a diff-like view of local changes.
 * 
 * Shows before/after state for modified records, similar to
 * git diff or Cursor's change view.
 * 
 * Used by the PendingChangesBar "Review" functionality.
 */

import { useState } from 'react';

interface RecordChange {
  table: string;
  record_id: string;
  operation: 'create' | 'update' | 'delete';
  before_data: Record<string, unknown> | null;
  after_data: Record<string, unknown> | null;
}

interface ChangeSession {
  id: string;
  status: string;
  description: string | null;
  created_at: string;
  records: RecordChange[];
}

interface ChangeSessionCardProps {
  session: ChangeSession;
  onApprove?: (sessionId: string) => void;
  onDiscard?: (sessionId: string) => void;
  isApproving?: boolean;
  isDiscarding?: boolean;
}

// Friendly names for tables
const TABLE_LABELS: Record<string, string> = {
  contacts: 'Contact',
  accounts: 'Company',
  deals: 'Deal',
  activities: 'Activity',
};

// Friendly names for fields
const FIELD_LABELS: Record<string, string> = {
  email: 'Email',
  firstname: 'First Name',
  lastname: 'Last Name',
  name: 'Name',
  domain: 'Domain',
  industry: 'Industry',
  numberofemployees: 'Employees',
  dealname: 'Deal Name',
  amount: 'Amount',
  dealstage: 'Stage',
  closedate: 'Close Date',
  jobtitle: 'Job Title',
  phone: 'Phone',
  company: 'Company',
};

// Fields to display prominently (in order)
const DISPLAY_FIELDS = [
  'email', 'firstname', 'lastname', 'name', 'domain', 
  'dealname', 'amount', 'dealstage', 'closedate',
  'jobtitle', 'phone', 'company', 'industry', 'numberofemployees'
];

export function ChangeSessionCard({
  session,
  onApprove,
  onDiscard,
  isApproving = false,
  isDiscarding = false,
}: ChangeSessionCardProps): JSX.Element {
  const [isExpanded, setIsExpanded] = useState(true);

  // Get display name for a record
  const getRecordName = (record: RecordChange): string => {
    const data = record.after_data ?? record.before_data ?? {};
    return (
      (data.name as string) ?? 
      (data.email as string) ?? 
      (data.dealname as string) ?? 
      (data.domain as string) ?? 
      record.record_id.slice(0, 8)
    );
  };

  // Get operation color
  const getOperationColor = (op: string): string => {
    switch (op) {
      case 'create': return 'text-green-400';
      case 'update': return 'text-amber-400';
      case 'delete': return 'text-red-400';
      default: return 'text-surface-400';
    }
  };

  // Get operation icon
  const getOperationIcon = (op: string): string => {
    switch (op) {
      case 'create': return '+';
      case 'update': return '~';
      case 'delete': return '-';
      default: return '?';
    }
  };

  // Get fields that changed (for updates)
  const getChangedFields = (record: RecordChange): Array<{field: string; before: unknown; after: unknown}> => {
    if (record.operation !== 'update' || !record.before_data || !record.after_data) {
      return [];
    }

    const changes: Array<{field: string; before: unknown; after: unknown}> = [];
    const allFields = new Set([
      ...Object.keys(record.before_data),
      ...Object.keys(record.after_data)
    ]);

    for (const field of allFields) {
      const before = record.before_data[field];
      const after = record.after_data[field];
      if (JSON.stringify(before) !== JSON.stringify(after)) {
        changes.push({ field, before, after });
      }
    }

    // Sort by display priority
    changes.sort((a, b) => {
      const aIdx = DISPLAY_FIELDS.indexOf(a.field);
      const bIdx = DISPLAY_FIELDS.indexOf(b.field);
      if (aIdx === -1 && bIdx === -1) return 0;
      if (aIdx === -1) return 1;
      if (bIdx === -1) return -1;
      return aIdx - bIdx;
    });

    return changes;
  };

  // Format value for display
  const formatValue = (value: unknown): string => {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (typeof value === 'number') {
      // Format amounts as currency
      if (value > 100) return `$${value.toLocaleString()}`;
      return value.toString();
    }
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  };

  return (
    <div className="bg-surface-800 rounded-lg border border-surface-700 overflow-hidden">
      {/* Header */}
      <div 
        className="px-4 py-3 flex items-center justify-between cursor-pointer hover:bg-surface-750 transition-colors"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex items-center gap-3">
          <div className={`w-2 h-2 rounded-full ${session.status === 'pending' ? 'bg-amber-500' : 'bg-surface-500'}`} />
          <span className="text-sm font-medium text-surface-200">
            {session.description ?? 'Local Changes'}
          </span>
          <span className="text-xs text-surface-500">
            {session.records.length} record{session.records.length !== 1 ? 's' : ''}
          </span>
        </div>
        <svg 
          className={`w-4 h-4 text-surface-400 transition-transform ${isExpanded ? 'rotate-180' : ''}`} 
          fill="none" 
          viewBox="0 0 24 24" 
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </div>

      {/* Content */}
      {isExpanded && (
        <div className="border-t border-surface-700">
          {/* Records */}
          <div className="divide-y divide-surface-700">
            {session.records.map((record, idx) => (
              <div key={`${record.record_id}-${idx}`} className="px-4 py-3">
                {/* Record header */}
                <div className="flex items-center gap-2 mb-2">
                  <span className={`font-mono text-sm ${getOperationColor(record.operation)}`}>
                    {getOperationIcon(record.operation)}
                  </span>
                  <span className="text-xs text-surface-500 uppercase">
                    {TABLE_LABELS[record.table] ?? record.table}
                  </span>
                  <span className="text-sm text-surface-200 font-medium">
                    {getRecordName(record)}
                  </span>
                </div>

                {/* Record details */}
                {record.operation === 'create' && record.after_data && (
                  <div className="ml-6 space-y-1">
                    {DISPLAY_FIELDS.filter(f => f in (record.after_data ?? {})).slice(0, 5).map(field => (
                      <div key={field} className="flex items-center gap-2 text-xs">
                        <span className="text-surface-500 w-24">{FIELD_LABELS[field] ?? field}:</span>
                        <span className="text-green-400">{formatValue(record.after_data?.[field])}</span>
                      </div>
                    ))}
                  </div>
                )}

                {record.operation === 'update' && (
                  <div className="ml-6 space-y-1">
                    {getChangedFields(record).slice(0, 5).map(({ field, before, after }) => (
                      <div key={field} className="flex items-center gap-2 text-xs">
                        <span className="text-surface-500 w-24">{FIELD_LABELS[field] ?? field}:</span>
                        <span className="text-red-400 line-through">{formatValue(before)}</span>
                        <span className="text-surface-500">→</span>
                        <span className="text-green-400">{formatValue(after)}</span>
                      </div>
                    ))}
                  </div>
                )}

                {record.operation === 'delete' && record.before_data && (
                  <div className="ml-6 space-y-1">
                    {DISPLAY_FIELDS.filter(f => f in (record.before_data ?? {})).slice(0, 3).map(field => (
                      <div key={field} className="flex items-center gap-2 text-xs">
                        <span className="text-surface-500 w-24">{FIELD_LABELS[field] ?? field}:</span>
                        <span className="text-red-400 line-through">{formatValue(record.before_data?.[field])}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Actions */}
          {(onApprove ?? onDiscard) && (
            <div className="px-4 py-3 bg-surface-850 border-t border-surface-700 flex items-center justify-end gap-2">
              {onDiscard && (
                <button
                  onClick={() => onDiscard(session.id)}
                  disabled={isApproving || isDiscarding}
                  className="px-3 py-1.5 text-xs font-medium bg-surface-700 hover:bg-surface-600 disabled:opacity-50 text-surface-200 rounded transition-colors"
                >
                  {isDiscarding ? 'Discarding...' : 'Discard'}
                </button>
              )}
              {onApprove && (
                <button
                  onClick={() => onApprove(session.id)}
                  disabled={isApproving || isDiscarding}
                  className="px-3 py-1.5 text-xs font-medium bg-surface-600 hover:bg-surface-500 disabled:opacity-50 text-white rounded transition-colors"
                >
                  {isApproving ? 'Approving...' : 'Approve'}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}


/**
 * Modal for reviewing all pending change sessions.
 */
interface ChangeSessionReviewModalProps {
  sessions: ChangeSession[];
  onClose: () => void;
  onApproveAll?: () => void;
  onDiscardAll?: () => void;
  isApproving?: boolean;
  isDiscarding?: boolean;
}

export function ChangeSessionReviewModal({
  sessions,
  onClose,
  onApproveAll,
  onDiscardAll,
  isApproving = false,
  isDiscarding = false,
}: ChangeSessionReviewModalProps): JSX.Element {
  const totalRecords = sessions.reduce((sum, s) => sum + s.records.length, 0);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-surface-900 rounded-lg shadow-xl max-w-2xl w-full mx-4 max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="p-4 border-b border-surface-700 flex-shrink-0">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-white">Review Local Changes</h2>
            <button
              onClick={onClose}
              className="text-surface-400 hover:text-white transition-colors"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          <p className="text-sm text-surface-400 mt-1">
            {totalRecords} pending change{totalRecords !== 1 ? 's' : ''} in {sessions.length} session{sessions.length !== 1 ? 's' : ''}
          </p>
        </div>
        
        {/* Content */}
        <div className="p-4 overflow-y-auto flex-1 space-y-3">
          {sessions.map(session => (
            <ChangeSessionCard key={session.id} session={session} />
          ))}
        </div>
        
        {/* Footer */}
        <div className="p-4 border-t border-surface-700 flex-shrink-0">
          <div className="flex items-center justify-end gap-2">
            {onDiscardAll && (
              <button
                onClick={onDiscardAll}
                disabled={isApproving || isDiscarding}
                className="px-4 py-2 text-sm font-medium bg-surface-700 hover:bg-surface-600 disabled:opacity-50 text-surface-200 rounded-lg transition-colors"
              >
                {isDiscarding ? 'Discarding...' : 'Discard All'}
              </button>
            )}
            {onApproveAll && (
              <button
                onClick={onApproveAll}
                disabled={isApproving || isDiscarding}
                className="px-4 py-2 text-sm font-medium bg-surface-600 hover:bg-surface-500 disabled:opacity-50 text-white rounded-lg transition-colors"
              >
                {isApproving ? 'Committing...' : 'Commit All'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
