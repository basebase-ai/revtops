/**
 * CRM Approval Card component.
 *
 * Displays a preview of CRM records to be created/updated with Approve/Cancel buttons.
 * Used when the agent calls the crm_write tool.
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

  const { operation_id, target_system, record_type, operation, preview, message } = data;
  const { records, will_create, will_skip, will_update, duplicate_warnings } = preview;

  // Get display records - show first 5 unless expanded
  const displayRecords = showAllRecords ? records : records.slice(0, 5);
  const hasMoreRecords = records.length > 5;

  // Get the primary display field based on record type
  const getPrimaryField = (record: CrmRecord): string => {
    if (record_type === 'contact') {
      const name = [record.firstname, record.lastname].filter(Boolean).join(' ');
      return name || record.email || 'Unknown';
    }
    if (record_type === 'company') {
      return record.name || record.domain || 'Unknown';
    }
    if (record_type === 'deal') {
      return record.dealname || 'Unknown';
    }
    return 'Unknown';
  };

  // Get secondary info based on record type
  const getSecondaryInfo = (record: CrmRecord): string => {
    if (record_type === 'contact') {
      return [record.jobtitle, record.company].filter(Boolean).join(' at ') || record.email || '';
    }
    if (record_type === 'company') {
      return record.industry || record.domain || '';
    }
    if (record_type === 'deal') {
      const amount = record.amount ? `$${Number(record.amount).toLocaleString()}` : '';
      return [amount, record.dealstage].filter(Boolean).join(' • ') || '';
    }
    return '';
  };

  // Check if a record is a duplicate
  const isDuplicate = (record: CrmRecord): boolean => {
    return duplicate_warnings.some((w) => {
      if (record_type === 'contact') return w.record.email === record.email;
      if (record_type === 'company') return w.record.domain === record.domain;
      if (record_type === 'deal') return w.record.dealname === record.dealname;
      return false;
    });
  };

  // If we have a result, show that instead
  if (result) {
    const isSuccess = result.status === 'completed';
    const isCanceled = result.status === 'canceled';

    return (
      <div
        className={`rounded-lg border p-4 ${
          isSuccess
            ? 'border-green-500/30 bg-green-500/10'
            : isCanceled
            ? 'border-surface-600 bg-surface-800/50'
            : 'border-red-500/30 bg-red-500/10'
        }`}
      >
        <div className="flex items-center gap-2 mb-2">
          {isSuccess ? (
            <svg className="w-5 h-5 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
          ) : isCanceled ? (
            <svg className="w-5 h-5 text-surface-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          ) : (
            <svg className="w-5 h-5 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          )}
          <span className={`font-medium ${isSuccess ? 'text-green-400' : isCanceled ? 'text-surface-300' : 'text-red-400'}`}>
            {result.message || (isCanceled ? 'Operation canceled' : 'Operation failed')}
          </span>
        </div>
        {isSuccess && (result.success_count !== undefined || result.skipped_count !== undefined) && (
          <div className="text-sm text-surface-400">
            {result.success_count !== undefined && result.success_count > 0 && (
              <span className="mr-3">Created: {result.success_count}</span>
            )}
            {result.skipped_count !== undefined && result.skipped_count > 0 && (
              <span className="mr-3">Skipped: {result.skipped_count}</span>
            )}
            {result.failure_count !== undefined && result.failure_count > 0 && (
              <span className="text-red-400">Failed: {result.failure_count}</span>
            )}
          </div>
        )}
        {result.error && (
          <p className="text-sm text-red-400 mt-1">{result.error}</p>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-primary-500/30 bg-surface-800/80 overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 bg-primary-500/10 border-b border-primary-500/20">
        <div className="flex items-center gap-2">
          <CrmIcon system={target_system} />
          <div>
            <h3 className="font-medium text-surface-100">
              {operation === 'create' ? 'Create' : operation === 'update' ? 'Update' : 'Upsert'}{' '}
              {records.length} {record_type}{records.length !== 1 ? 's' : ''} in{' '}
              {target_system.charAt(0).toUpperCase() + target_system.slice(1)}
            </h3>
            <p className="text-xs text-surface-400">{message}</p>
          </div>
        </div>
      </div>

      {/* Summary stats */}
      <div className="px-4 py-2 bg-surface-800/50 border-b border-surface-700 flex gap-4 text-sm">
        {will_create > 0 && (
          <span className="text-green-400">
            <span className="font-medium">{will_create}</span> to create
          </span>
        )}
        {will_skip > 0 && (
          <span className="text-yellow-400">
            <span className="font-medium">{will_skip}</span> duplicates (will skip)
          </span>
        )}
        {will_update > 0 && (
          <span className="text-blue-400">
            <span className="font-medium">{will_update}</span> to update
          </span>
        )}
      </div>

      {/* Records list */}
      <div className="px-4 py-3 space-y-2 max-h-64 overflow-y-auto">
        {displayRecords.map((record, index) => {
          const duplicate = isDuplicate(record);
          return (
            <div
              key={index}
              className={`flex items-center gap-3 p-2 rounded ${
                duplicate ? 'bg-yellow-500/10 border border-yellow-500/20' : 'bg-surface-700/50'
              }`}
            >
              <RecordTypeIcon type={record_type} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-surface-200 truncate">
                    {getPrimaryField(record)}
                  </span>
                  {duplicate && (
                    <span className="text-xs px-1.5 py-0.5 rounded bg-yellow-500/20 text-yellow-400">
                      duplicate
                    </span>
                  )}
                </div>
                <span className="text-xs text-surface-400 truncate block">
                  {getSecondaryInfo(record)}
                </span>
              </div>
            </div>
          );
        })}

        {hasMoreRecords && (
          <button
            onClick={() => setShowAllRecords(!showAllRecords)}
            className="text-sm text-primary-400 hover:text-primary-300 py-1"
          >
            {showAllRecords ? 'Show less' : `Show ${records.length - 5} more...`}
          </button>
        )}
      </div>

      {/* Duplicate handling option */}
      {duplicate_warnings.length > 0 && operation === 'create' && (
        <div className="px-4 py-2 border-t border-surface-700 bg-surface-800/30">
          <label className="flex items-center gap-2 text-sm text-surface-300 cursor-pointer">
            <input
              type="checkbox"
              checked={skipDuplicates}
              onChange={(e) => setSkipDuplicates(e.target.checked)}
              className="rounded border-surface-600 bg-surface-700 text-primary-500 focus:ring-primary-500"
            />
            Skip {duplicate_warnings.length} duplicate{duplicate_warnings.length !== 1 ? 's' : ''} (recommended)
          </label>
        </div>
      )}

      {/* Action buttons */}
      <div className="px-4 py-3 border-t border-surface-700 flex gap-3 justify-end">
        <button
          onClick={() => onCancel(operation_id)}
          disabled={isProcessing}
          className="px-4 py-2 text-sm font-medium text-surface-300 hover:text-surface-100 hover:bg-surface-700 rounded-lg transition-colors disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          onClick={() => onApprove(operation_id, skipDuplicates)}
          disabled={isProcessing}
          className="px-4 py-2 text-sm font-medium bg-primary-600 hover:bg-primary-500 text-white rounded-lg transition-colors disabled:opacity-50 flex items-center gap-2"
        >
          {isProcessing ? (
            <>
              <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
              Processing...
            </>
          ) : (
            <>
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              Approve
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
      <div className="w-8 h-8 rounded-lg bg-orange-500/20 flex items-center justify-center">
        <svg className="w-5 h-5 text-orange-400" viewBox="0 0 24 24" fill="currentColor">
          <path d="M18.164 7.93V5.084a2.198 2.198 0 001.267-1.984 2.21 2.21 0 00-4.42 0c0 .873.521 1.617 1.267 1.964V7.93a5.157 5.157 0 00-3.036 1.705l-6.39-4.862a2.625 2.625 0 00.077-.575 2.62 2.62 0 10-2.62 2.62c.459 0 .893-.12 1.272-.329l6.24 4.748a5.203 5.203 0 00-.178 1.323 5.223 5.223 0 005.2 5.2 5.16 5.16 0 002.608-.7l2.593 2.601a1.652 1.652 0 001.165.484 1.647 1.647 0 001.165-2.813l-2.552-2.562a5.155 5.155 0 00.888-2.87 5.223 5.223 0 00-3.546-4.97zm-1.596 7.27a2.4 2.4 0 110-4.8 2.4 2.4 0 010 4.8z" />
        </svg>
      </div>
    );
  }
  if (system === 'salesforce') {
    return (
      <div className="w-8 h-8 rounded-lg bg-blue-500/20 flex items-center justify-center">
        <svg className="w-5 h-5 text-blue-400" viewBox="0 0 24 24" fill="currentColor">
          <path d="M10.006 5.415a4.195 4.195 0 013.045-1.306c1.56 0 2.954.9 3.69 2.205.63-.3 1.35-.45 2.1-.45 2.85 0 5.159 2.34 5.159 5.22s-2.31 5.22-5.16 5.22c-.36 0-.72-.045-1.065-.12-.63 1.26-1.905 2.13-3.39 2.13a3.78 3.78 0 01-1.665-.39 4.47 4.47 0 01-3.915 2.34c-2.085 0-3.885-1.425-4.395-3.375-.255.03-.51.06-.78.06C1.68 16.95 0 15.24 0 13.11c0-1.5.84-2.82 2.085-3.465a4.065 4.065 0 01-.42-1.785c0-2.25 1.815-4.065 4.05-4.065 1.32 0 2.49.63 3.225 1.62h1.066z" />
        </svg>
      </div>
    );
  }
  return (
    <div className="w-8 h-8 rounded-lg bg-surface-600 flex items-center justify-center">
      <svg className="w-5 h-5 text-surface-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
      </svg>
    </div>
  );
}

function RecordTypeIcon({ type }: { type: string }): JSX.Element {
  if (type === 'contact') {
    return (
      <div className="w-6 h-6 rounded bg-surface-600 flex items-center justify-center flex-shrink-0">
        <svg className="w-3.5 h-3.5 text-surface-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
        </svg>
      </div>
    );
  }
  if (type === 'company') {
    return (
      <div className="w-6 h-6 rounded bg-surface-600 flex items-center justify-center flex-shrink-0">
        <svg className="w-3.5 h-3.5 text-surface-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
        </svg>
      </div>
    );
  }
  if (type === 'deal') {
    return (
      <div className="w-6 h-6 rounded bg-surface-600 flex items-center justify-center flex-shrink-0">
        <svg className="w-3.5 h-3.5 text-surface-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      </div>
    );
  }
  return (
    <div className="w-6 h-6 rounded bg-surface-600 flex items-center justify-center flex-shrink-0">
      <svg className="w-3.5 h-3.5 text-surface-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
    </div>
  );
}
