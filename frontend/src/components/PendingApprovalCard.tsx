/**
 * PendingApprovalCard - Extensible approval framework for tool operations.
 * 
 * This component handles the common UI for all approval-required tools:
 * - Approve/Cancel buttons
 * - Loading states
 * - Success/failure/canceled results
 * 
 * Tool-specific preview rendering is delegated to registered preview components.
 * To add support for a new tool, add an entry to PREVIEW_RENDERERS.
 */

import { useState, type ReactNode } from 'react';

// =============================================================================
// Types
// =============================================================================

export interface PendingApprovalData {
  type: 'pending_approval';
  status: string;
  operation_id: string;
  tool_name: string;
  preview: Record<string, unknown>;
  message: string;
  // Tool-specific fields (optional)
  target_system?: string;
  record_type?: string;
  operation?: string;
}

export interface ApprovalResult {
  status: 'completed' | 'failed' | 'canceled' | 'expired';
  message?: string;
  error?: string;
  success_count?: number;
  failure_count?: number;
  skipped_count?: number;
}

interface PendingApprovalCardProps {
  data: PendingApprovalData;
  onApprove: (operationId: string, options?: Record<string, unknown>) => void;
  onCancel: (operationId: string) => void;
  isProcessing?: boolean;
  result?: ApprovalResult | null;
}

interface PreviewRendererProps {
  data: PendingApprovalData;
  options: Record<string, unknown>;
  setOptions: (opts: Record<string, unknown>) => void;
}

type PreviewRenderer = (props: PreviewRendererProps) => ReactNode;

// =============================================================================
// Preview Renderers Registry
// =============================================================================

const PREVIEW_RENDERERS: Record<string, PreviewRenderer> = {
  send_email_from: EmailPreview,
  send_slack: SlackPreview,
  write_to_system_of_record: CrmPreview,
  run_sql_write: CrmPreview,  // SQL-based CRM writes use the same preview
};

// =============================================================================
// Main Component
// =============================================================================

export function PendingApprovalCard({
  data,
  onApprove,
  onCancel,
  isProcessing = false,
  result = null,
}: PendingApprovalCardProps): JSX.Element {
  const [options, setOptions] = useState<Record<string, unknown>>({});
  const [showFullError, setShowFullError] = useState(false);

  const { operation_id, tool_name } = data;

  // Get the appropriate preview renderer
  const PreviewComponent = PREVIEW_RENDERERS[tool_name] || GenericPreview;

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
          <CheckIcon />
        ) : isCanceled ? (
          <XIcon />
        ) : (
          <AlertIcon />
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
  return (
    <div className="inline-flex flex-col rounded-md border border-primary-500/30 bg-surface-800/80 text-sm overflow-hidden max-w-md">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 bg-primary-500/10 border-b border-primary-500/20">
        <ToolIcon toolName={tool_name} data={data} />
        <span className="text-surface-200">{getHeaderText(data)}</span>
      </div>

      {/* Tool-specific preview */}
      <div className="px-3 py-2">
        <PreviewComponent data={data} options={options} setOptions={setOptions} />
      </div>

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
          onClick={() => onApprove(operation_id, options)}
          disabled={isProcessing}
          className="px-2.5 py-1 text-xs font-medium bg-primary-600 hover:bg-primary-500 text-white rounded transition-colors disabled:opacity-50 flex items-center gap-1"
        >
          {isProcessing ? (
            <>
              <LoadingSpinner />
              <span>...</span>
            </>
          ) : (
            <>
              <CheckIcon small />
              <span>Approve</span>
            </>
          )}
        </button>
      </div>
    </div>
  );
}

// =============================================================================
// Preview Components
// =============================================================================

function EmailPreview({ data }: PreviewRendererProps): ReactNode {
  const preview = data.preview as {
    provider?: string;
    to?: string;
    subject?: string;
    body?: string;
    cc?: string[];
    bcc?: string[];
  };

  return (
    <div className="space-y-1.5 text-surface-300 text-xs">
      <div className="flex gap-2">
        <span className="text-surface-500 w-12">To:</span>
        <span className="text-surface-100">{preview.to}</span>
      </div>
      <div className="flex gap-2">
        <span className="text-surface-500 w-12">Subject:</span>
        <span className="text-surface-100">{preview.subject}</span>
      </div>
      {preview.cc && preview.cc.length > 0 && (
        <div className="flex gap-2">
          <span className="text-surface-500 w-12">CC:</span>
          <span className="text-surface-100">{preview.cc.join(', ')}</span>
        </div>
      )}
      <div className="mt-2 p-2 bg-surface-900/50 rounded border border-surface-700 max-h-32 overflow-y-auto">
        <p className="text-surface-200 whitespace-pre-wrap">{preview.body}</p>
      </div>
    </div>
  );
}

function SlackPreview({ data }: PreviewRendererProps): ReactNode {
  const preview = data.preview as {
    channel?: string;
    message?: string;
    thread_ts?: string;
  };

  return (
    <div className="space-y-1.5 text-surface-300 text-xs">
      <div className="flex gap-2">
        <span className="text-surface-500 w-16">Channel:</span>
        <span className="text-surface-100 font-medium">{preview.channel}</span>
      </div>
      {preview.thread_ts && (
        <div className="flex gap-2">
          <span className="text-surface-500 w-16">Thread:</span>
          <span className="text-surface-400">Reply in thread</span>
        </div>
      )}
      <div className="mt-2 p-2 bg-surface-900/50 rounded border border-surface-700 max-h-32 overflow-y-auto">
        <p className="text-surface-200 whitespace-pre-wrap">{preview.message}</p>
      </div>
    </div>
  );
}

function CrmPreview({ data, options, setOptions }: PreviewRendererProps): ReactNode {
  const preview = data.preview as {
    records?: Array<Record<string, unknown>>;
    record_count?: number;
    will_create?: number;
    will_skip?: number;
    will_update?: number;
    duplicate_warnings?: Array<{
      record: Record<string, unknown>;
      existing_id: string;
      match_field: string;
      match_value: string;
    }>;
  };

  const records = preview.records ?? [];
  const duplicate_warnings = preview.duplicate_warnings ?? [];
  const record_type = data.record_type ?? 'record';
  const operation = data.operation ?? 'create';
  const [showAll, setShowAll] = useState(false);

  const getRecordName = (record: Record<string, unknown>): string => {
    if (record_type === 'contact') {
      const name = [record.firstname, record.lastname].filter(Boolean).join(' ');
      return (name as string) || (record.email as string) || 'Unknown';
    }
    if (record_type === 'company') return (record.name as string) || (record.domain as string) || 'Unknown';
    if (record_type === 'deal') return (record.dealname as string) || 'Unknown';
    return 'Unknown';
  };

  const displayRecords = showAll ? records : records.slice(0, 3);
  const moreCount = records.length - 3;
  const skipDuplicates = (options.skip_duplicates as boolean) ?? true;

  return (
    <div className="text-surface-300 text-xs">
      {/* Records list */}
      <div>
        {displayRecords.map((r, i) => (
          <span key={i}>
            {i > 0 && ', '}
            <span className="text-surface-100">{getRecordName(r)}</span>
          </span>
        ))}
        {moreCount > 0 && !showAll && (
          <button
            onClick={() => setShowAll(true)}
            className="text-primary-400 hover:text-primary-300 ml-1"
          >
            +{moreCount} more
          </button>
        )}
        {showAll && moreCount > 0 && (
          <button
            onClick={() => setShowAll(false)}
            className="text-primary-400 hover:text-primary-300 ml-1"
          >
            (show less)
          </button>
        )}
      </div>

      {/* Duplicate warning */}
      {duplicate_warnings.length > 0 && operation === 'create' && (
        <div className="mt-2 pt-2 border-t border-surface-700">
          <label className="flex items-center gap-2 text-yellow-400/80 cursor-pointer">
            <input
              type="checkbox"
              checked={skipDuplicates}
              onChange={(e) => setOptions({ ...options, skip_duplicates: e.target.checked })}
              className="rounded border-surface-600 bg-surface-700 text-primary-500 w-3 h-3"
            />
            Skip {duplicate_warnings.length} duplicate{duplicate_warnings.length !== 1 ? 's' : ''}
          </label>
        </div>
      )}
    </div>
  );
}

function GenericPreview({ data }: PreviewRendererProps): ReactNode {
  const preview = data.preview;
  
  // Try to show a nice summary if possible
  const entries = Object.entries(preview).slice(0, 5);
  
  return (
    <div className="space-y-1 text-surface-300 text-xs">
      {entries.map(([key, value]) => (
        <div key={key} className="flex gap-2">
          <span className="text-surface-500 capitalize">{key.replace(/_/g, ' ')}:</span>
          <span className="text-surface-100 truncate max-w-xs">
            {typeof value === 'string' ? value : JSON.stringify(value)}
          </span>
        </div>
      ))}
      {Object.keys(preview).length > 5 && (
        <div className="text-surface-500 text-xs mt-1">
          +{Object.keys(preview).length - 5} more fields
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Helper Components
// =============================================================================

function getHeaderText(data: PendingApprovalData): string {
  const { tool_name, preview, target_system, record_type, operation } = data;
  
  switch (tool_name) {
    case 'send_email_from': {
      const to = (preview as { to?: string }).to;
      return `Send email to ${to}`;
    }
    case 'send_slack': {
      const channel = (preview as { channel?: string }).channel;
      return `Post to ${channel}`;
    }
    case 'write_to_system_of_record':
    case 'run_sql_write': {
      const records = (preview as { records?: unknown[] }).records ?? [];
      const recordLabel = records.length === 1 ? record_type : `${record_type}s`;
      const verb = operation === 'create' ? 'Create' : operation === 'update' ? 'Update' : 'Save';
      const system = target_system ? target_system.charAt(0).toUpperCase() + target_system.slice(1) : 'CRM';
      return `${verb} ${records.length} ${recordLabel} in ${system}`;
    }
    default:
      return data.message || `Approve ${tool_name}`;
  }
}

function ToolIcon({ toolName, data }: { toolName: string; data: PendingApprovalData }): JSX.Element {
  switch (toolName) {
    case 'send_email_from':
      return (
        <svg className="w-4 h-4 text-blue-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
        </svg>
      );
    case 'send_slack':
      return (
        <svg className="w-4 h-4 text-purple-400" viewBox="0 0 24 24" fill="currentColor">
          <path d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312zM18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312zM15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z"/>
        </svg>
      );
    case 'write_to_system_of_record':
    case 'run_sql_write':
      if (data.target_system === 'hubspot') {
        return (
          <svg className="w-4 h-4 text-orange-400" viewBox="0 0 24 24" fill="currentColor">
            <path d="M18.164 7.93V5.084a2.198 2.198 0 001.267-1.984 2.21 2.21 0 00-4.42 0c0 .873.521 1.617 1.267 1.964V7.93a5.157 5.157 0 00-3.036 1.705l-6.39-4.862a2.625 2.625 0 00.077-.575 2.62 2.62 0 10-2.62 2.62c.459 0 .893-.12 1.272-.329l6.24 4.748a5.203 5.203 0 00-.178 1.323 5.223 5.223 0 005.2 5.2 5.16 5.16 0 002.608-.7l2.593 2.601a1.652 1.652 0 001.165.484 1.647 1.647 0 001.165-2.813l-2.552-2.562a5.155 5.155 0 00.888-2.87 5.223 5.223 0 00-3.546-4.97zm-1.596 7.27a2.4 2.4 0 110-4.8 2.4 2.4 0 010 4.8z" />
          </svg>
        );
      }
      return (
        <svg className="w-4 h-4 text-surface-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
        </svg>
      );
    default:
      return (
        <svg className="w-4 h-4 text-primary-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      );
  }
}

function CheckIcon({ small }: { small?: boolean }): JSX.Element {
  return (
    <svg className={small ? "w-3 h-3" : "w-4 h-4"} fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
    </svg>
  );
}

function XIcon(): JSX.Element {
  return (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
    </svg>
  );
}

function AlertIcon(): JSX.Element {
  return (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  );
}

function LoadingSpinner(): JSX.Element {
  return (
    <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
    </svg>
  );
}

// =============================================================================
// Exports
// =============================================================================

export { PREVIEW_RENDERERS };
