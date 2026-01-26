/**
 * CRM Approval Card component - compact version.
 *
 * Displays a preview of CRM records to be created/updated with Approve/Cancel buttons.
 */

import { useState } from 'react';

interface CrmRecord {
  email?: string;
  firstname?: string;
  lastname?: string;
  company?: string;
  jobtitle?: string;
  phone?: string;
  name?: string;
  domain?: string;
  industry?: string;
  dealname?: string;
  amount?: string | number;
  dealstage?: string;
  [key: string]: unknown;
}

interface DuplicateWarning {
  record: CrmRecord;
  existing_id: string;
  existing: CrmRecord;
  match_field: string;
  match_value: string;
}

interface CrmApprovalPreview {
  records: CrmRecord[];
  record_count: number;
  will_create: number;
  will_skip: number;
  will_update: number;
  duplicate_warnings: DuplicateWarning[];
}

interface CrmApprovalData {
  operation_id: string;
  target_system: string;
  record_type: string;
  operation: string;
  preview: CrmApprovalPreview;
  message: string;
}

interface CrmApprovalCardProps {
  data: CrmApprovalData;
  onApprove: (operationId: string, skipDuplicates: boolean) => void;
  onCancel: (operationId: string) => void;
  isProcessing?: boolean;
  result?: {
    status: string;
    message?: string;
    success_count?: number;
    failure_count?: number;
    skipped_count?: number;
    error?: string;
  } | null;
}

export function CrmApprovalCard({
  data,
  onApprove,
  onCancel,
  isProcessing = false,
  result = null,
}: CrmApprovalCardProps): JSX.Element {
  const [skipDuplicates, setSkipDuplicates] = useState(true);
  const [showAllRecords, setShowAllRecords] = useState(false);
  const [showFullError, setShowFullError] = useState(false);

  const { operation_id, target_system, record_type, operation, preview } = data;
  
  const records = preview?.records ?? [];
  const duplicate_warnings = preview?.duplicate_warnings ?? [];

  // Get display name for a record
  const getRecordName = (record: CrmRecord): string => {
    if (record_type === 'contact') {
      const name = [record.firstname, record.lastname].filter(Boolean).join(' ');
      return name || record.email || 'Unknown';
    }
    if (record_type === 'company') return record.name || record.domain || 'Unknown';
    if (record_type === 'deal') return record.dealname || 'Unknown';
    return 'Unknown';
  };

  // Show first 3 records inline, rest as "+N more"
  const displayRecords = showAllRecords ? records : records.slice(0, 3);
  const moreCount = records.length - 3;

  // Result view (completed/failed/canceled)
  if (result) {
    const isSuccess = result.status === 'completed';
    const isCanceled = result.status === 'canceled';

    return (
      <div
        className={`inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm ${
          isSuccess
            ? 'border-green-500/30 bg-green-500/10 text-green-400'
            : isCanceled
            ? 'border-surface-600 bg-surface-800/50 text-surface-400'
            : 'border-red-500/30 bg-red-500/10 text-red-400'
        }`}
      >
        {isSuccess ? (
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
        ) : isCanceled ? (
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        ) : (
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        )}
        <span>{result.message || (isCanceled ? 'Canceled' : 'Failed')}</span>
        {result.error && (
          <button
            onClick={() => setShowFullError(!showFullError)}
            className="text-xs underline opacity-70 hover:opacity-100"
          >
            {showFullError ? 'hide' : 'details'}
          </button>
        )}
        {showFullError && result.error && (
          <span className="text-xs opacity-70 max-w-md truncate" title={result.error}>
            {result.error.slice(0, 100)}{result.error.length > 100 ? '...' : ''}
          </span>
        )}
      </div>
    );
  }

  // Pending approval view
  const operationVerb = operation === 'create' ? 'Create' : operation === 'update' ? 'Update' : 'Save';
  const recordLabel = records.length === 1 ? record_type : `${record_type}s`;

  return (
    <div className="inline-flex flex-col rounded-md border border-primary-500/30 bg-surface-800/80 text-sm overflow-hidden max-w-md">
      {/* Header row */}
      <div className="flex items-center gap-2 px-3 py-2 bg-primary-500/10 border-b border-primary-500/20">
        <CrmIcon system={target_system} />
        <span className="text-surface-200">
          {operationVerb} {records.length} {recordLabel} in {target_system.charAt(0).toUpperCase() + target_system.slice(1)}
        </span>
      </div>

      {/* Records preview */}
      {records.length > 0 && (
        <div className="px-3 py-2 text-surface-300 text-xs">
          {displayRecords.map((r, i) => (
            <span key={i}>
              {i > 0 && ', '}
              <span className="text-surface-100">{getRecordName(r)}</span>
            </span>
          ))}
          {moreCount > 0 && !showAllRecords && (
            <button
              onClick={() => setShowAllRecords(true)}
              className="text-primary-400 hover:text-primary-300 ml-1"
            >
              +{moreCount} more
            </button>
          )}
          {showAllRecords && moreCount > 0 && (
            <button
              onClick={() => setShowAllRecords(false)}
              className="text-primary-400 hover:text-primary-300 ml-1"
            >
              (show less)
            </button>
          )}
        </div>
      )}

      {/* Duplicate warning */}
      {duplicate_warnings.length > 0 && operation === 'create' && (
        <div className="px-3 py-1.5 border-t border-surface-700 bg-yellow-500/5">
          <label className="flex items-center gap-2 text-xs text-yellow-400/80 cursor-pointer">
            <input
              type="checkbox"
              checked={skipDuplicates}
              onChange={(e) => setSkipDuplicates(e.target.checked)}
              className="rounded border-surface-600 bg-surface-700 text-primary-500 w-3 h-3"
            />
            Skip {duplicate_warnings.length} duplicate{duplicate_warnings.length !== 1 ? 's' : ''}
          </label>
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-2 px-3 py-2 border-t border-surface-700 justify-end">
        <button
          onClick={() => onCancel(operation_id)}
          disabled={isProcessing}
          className="px-2.5 py-1 text-xs text-surface-400 hover:text-surface-200 disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          onClick={() => onApprove(operation_id, skipDuplicates)}
          disabled={isProcessing}
          className="px-2.5 py-1 text-xs font-medium bg-primary-600 hover:bg-primary-500 text-white rounded transition-colors disabled:opacity-50 flex items-center gap-1"
        >
          {isProcessing ? (
            <>
              <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
              <span>...</span>
            </>
          ) : (
            <>
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              <span>Approve</span>
            </>
          )}
        </button>
      </div>
    </div>
  );
}

function CrmIcon({ system }: { system: string }): JSX.Element {
  if (system === 'hubspot') {
    return (
      <svg className="w-4 h-4 text-orange-400" viewBox="0 0 24 24" fill="currentColor">
        <path d="M18.164 7.93V5.084a2.198 2.198 0 001.267-1.984 2.21 2.21 0 00-4.42 0c0 .873.521 1.617 1.267 1.964V7.93a5.157 5.157 0 00-3.036 1.705l-6.39-4.862a2.625 2.625 0 00.077-.575 2.62 2.62 0 10-2.62 2.62c.459 0 .893-.12 1.272-.329l6.24 4.748a5.203 5.203 0 00-.178 1.323 5.223 5.223 0 005.2 5.2 5.16 5.16 0 002.608-.7l2.593 2.601a1.652 1.652 0 001.165.484 1.647 1.647 0 001.165-2.813l-2.552-2.562a5.155 5.155 0 00.888-2.87 5.223 5.223 0 00-3.546-4.97zm-1.596 7.27a2.4 2.4 0 110-4.8 2.4 2.4 0 010 4.8z" />
      </svg>
    );
  }
  if (system === 'salesforce') {
    return (
      <svg className="w-4 h-4 text-blue-400" viewBox="0 0 24 24" fill="currentColor">
        <path d="M10.006 5.415a4.195 4.195 0 013.045-1.306c1.56 0 2.954.9 3.69 2.205.63-.3 1.35-.45 2.1-.45 2.85 0 5.159 2.34 5.159 5.22s-2.31 5.22-5.16 5.22c-.36 0-.72-.045-1.065-.12-.63 1.26-1.905 2.13-3.39 2.13a3.78 3.78 0 01-1.665-.39 4.47 4.47 0 01-3.915 2.34c-2.085 0-3.885-1.425-4.395-3.375-.255.03-.51.06-.78.06C1.68 16.95 0 15.24 0 13.11c0-1.5.84-2.82 2.085-3.465a4.065 4.065 0 01-.42-1.785c0-2.25 1.815-4.065 4.05-4.065 1.32 0 2.49.63 3.225 1.62h1.066z" />
      </svg>
    );
  }
  return (
    <svg className="w-4 h-4 text-surface-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
    </svg>
  );
}
