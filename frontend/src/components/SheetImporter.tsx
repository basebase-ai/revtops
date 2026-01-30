/**
 * Google Sheets Importer Component
 * 
 * Multi-step wizard for importing data from Google Sheets:
 * 1. Select spreadsheet from user's Google Drive
 * 2. Preview tabs with LLM-inferred schema mappings
 * 3. Review/adjust mappings
 * 4. Execute import and show results
 */

import { useState, useEffect } from 'react';
import { SiGooglesheets } from 'react-icons/si';
import { HiChevronLeft, HiChevronRight, HiCheck, HiX, HiRefresh, HiExclamation, HiInformationCircle } from 'react-icons/hi';
import { API_BASE } from '../lib/api';
import { useAppStore } from '../store';

// Types for API responses
interface SpreadsheetInfo {
  id: string;
  name: string;
  lastModified: string | null;
  owner: string | null;
}

interface TabPreview {
  tab_name: string;
  headers: string[];
  sample_rows: string[][];
  row_count: number;
}

interface ColumnMapping {
  tab_name: string;
  entity_type: string;
  confidence: number;
  column_mappings: Record<string, string>;
  ignored_columns: string[];
  notes: string | null;
}

interface SpreadsheetPreview {
  spreadsheet_id: string;
  title: string;
  tabs: TabPreview[];
  mappings: ColumnMapping[];
  target_schemas: Record<string, Record<string, string>>;
}

interface ImportResult {
  id: string;
  status: string;
  spreadsheet_name: string | null;
  results: {
    created: number;
    updated: number;
    skipped: number;
    total_errors: number;
  } | null;
  errors: Array<{ tab: string; row: number | null; error: string }> | null;
  error_message: string | null;
}

type Step = 'select' | 'preview' | 'importing' | 'complete';

interface SheetImporterProps {
  onClose: () => void;
}

export function SheetImporter({ onClose }: SheetImporterProps): JSX.Element {
  const { user, organization } = useAppStore();
  const [step, setStep] = useState<Step>('select');
  const [spreadsheets, setSpreadsheets] = useState<SpreadsheetInfo[]>([]);
  const [selectedSpreadsheet, setSelectedSpreadsheet] = useState<SpreadsheetInfo | null>(null);
  const [preview, setPreview] = useState<SpreadsheetPreview | null>(null);
  const [mappings, setMappings] = useState<ColumnMapping[]>([]);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const organizationId = organization?.id ?? '';
  const userId = user?.id ?? '';

  // Load spreadsheets on mount
  useEffect(() => {
    if (step === 'select' && spreadsheets.length === 0) {
      void loadSpreadsheets();
    }
  }, [step]);

  const loadSpreadsheets = async (): Promise<void> => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(
        `${API_BASE}/sheets/list?user_id=${userId}&organization_id=${organizationId}`
      );

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to load spreadsheets');
      }

      const data = await response.json();
      setSpreadsheets(data.spreadsheets);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load spreadsheets');
    } finally {
      setLoading(false);
    }
  };

  const loadPreview = async (spreadsheetId: string): Promise<void> => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(
        `${API_BASE}/sheets/${spreadsheetId}/preview?user_id=${userId}&organization_id=${organizationId}`
      );

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to load preview');
      }

      const data: SpreadsheetPreview = await response.json();
      setPreview(data);
      setMappings(data.mappings);
      setStep('preview');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load preview');
    } finally {
      setLoading(false);
    }
  };

  const startImport = async (): Promise<void> => {
    if (!selectedSpreadsheet || mappings.length === 0) return;

    setLoading(true);
    setError(null);
    setStep('importing');

    try {
      const tabMappings = mappings.map((m) => ({
        tab_name: m.tab_name,
        entity_type: m.entity_type,
        column_mappings: m.column_mappings,
        skip_header_row: true,
      }));

      const response = await fetch(
        `${API_BASE}/sheets/${selectedSpreadsheet.id}/import?user_id=${userId}&organization_id=${organizationId}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tab_mappings: tabMappings }),
        }
      );

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to start import');
      }

      const data = await response.json();
      const importId = data.import_id;

      // Poll for completion
      await pollImportStatus(importId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Import failed');
      setStep('preview');
    } finally {
      setLoading(false);
    }
  };

  const pollImportStatus = async (importId: string): Promise<void> => {
    const maxAttempts = 60;
    let attempts = 0;

    const checkStatus = async (): Promise<void> => {
      try {
        const response = await fetch(
          `${API_BASE}/sheets/imports/${importId}?user_id=${userId}&organization_id=${organizationId}`
        );

        if (!response.ok) {
          throw new Error('Failed to check import status');
        }

        const result: ImportResult = await response.json();

        if (result.status === 'completed' || result.status === 'failed') {
          setImportResult(result);
          setStep('complete');
        } else if (attempts < maxAttempts) {
          attempts++;
          setTimeout(() => void checkStatus(), 2000);
        } else {
          throw new Error('Import timed out');
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to check status');
        setStep('preview');
      }
    };

    await checkStatus();
  };

  const updateMapping = (tabName: string, field: string, value: string): void => {
    setMappings((prev) =>
      prev.map((m) => {
        if (m.tab_name !== tabName) return m;
        return {
          ...m,
          [field]: value,
        };
      })
    );
  };

  const updateColumnMapping = (tabName: string, column: string, targetField: string): void => {
    setMappings((prev) =>
      prev.map((m) => {
        if (m.tab_name !== tabName) return m;
        const newMappings = { ...m.column_mappings };
        
        if (targetField === '') {
          // Remove mapping
          delete newMappings[column];
          return {
            ...m,
            column_mappings: newMappings,
            ignored_columns: [...m.ignored_columns, column],
          };
        } else {
          // Add/update mapping
          newMappings[column] = targetField;
          return {
            ...m,
            column_mappings: newMappings,
            ignored_columns: m.ignored_columns.filter((c) => c !== column),
          };
        }
      })
    );
  };

  // Render step content
  const renderStepContent = (): JSX.Element => {
    switch (step) {
      case 'select':
        return renderSelectStep();
      case 'preview':
        return renderPreviewStep();
      case 'importing':
        return renderImportingStep();
      case 'complete':
        return renderCompleteStep();
    }
  };

  const renderSelectStep = (): JSX.Element => (
    <div className="space-y-4">
      <p className="text-surface-400">
        Select a spreadsheet from your Google Drive to import contacts, accounts, or deals.
      </p>

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : error ? (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 text-red-400">
          <div className="flex items-center gap-2">
            <HiExclamation className="w-5 h-5" />
            <span>{error}</span>
          </div>
          <button
            onClick={() => void loadSpreadsheets()}
            className="mt-3 text-sm text-red-300 hover:text-red-200 flex items-center gap-1"
          >
            <HiRefresh className="w-4 h-4" />
            Try again
          </button>
        </div>
      ) : spreadsheets.length === 0 ? (
        <div className="text-center py-8 text-surface-400">
          <p>No spreadsheets found in your Google Drive.</p>
          <p className="text-sm mt-2">Create a spreadsheet in Google Sheets first, then try again.</p>
        </div>
      ) : (
        <div className="space-y-2 max-h-96 overflow-y-auto">
          {spreadsheets.map((sheet) => (
            <button
              key={sheet.id}
              onClick={() => {
                setSelectedSpreadsheet(sheet);
                void loadPreview(sheet.id);
              }}
              className={`w-full p-4 rounded-lg border text-left transition-colors ${
                selectedSpreadsheet?.id === sheet.id
                  ? 'border-primary-500 bg-primary-500/10'
                  : 'border-surface-700 hover:border-surface-600 hover:bg-surface-800/50'
              }`}
            >
              <div className="flex items-center gap-3">
                <SiGooglesheets className="w-8 h-8 text-emerald-500 flex-shrink-0" />
                <div className="min-w-0 flex-1">
                  <h3 className="font-medium text-surface-100 truncate">{sheet.name}</h3>
                  <p className="text-sm text-surface-500">
                    {sheet.lastModified && `Modified ${new Date(sheet.lastModified).toLocaleDateString()}`}
                    {sheet.owner && ` • ${sheet.owner}`}
                  </p>
                </div>
                <HiChevronRight className="w-5 h-5 text-surface-500" />
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );

  const renderPreviewStep = (): JSX.Element => {
    if (!preview) return <div>Loading...</div>;

    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="font-medium text-surface-100">{preview.title}</h3>
            <p className="text-sm text-surface-400">{preview.tabs.length} tab(s) found</p>
          </div>
          <button
            onClick={() => {
              setSelectedSpreadsheet(null);
              setPreview(null);
              setStep('select');
            }}
            className="text-sm text-surface-400 hover:text-surface-300 flex items-center gap-1"
          >
            <HiChevronLeft className="w-4 h-4" />
            Choose different spreadsheet
          </button>
        </div>

        {/* Schema legend */}
        <div className="bg-surface-800/50 rounded-lg p-4 border border-surface-700">
          <div className="flex items-start gap-2">
            <HiInformationCircle className="w-5 h-5 text-primary-400 flex-shrink-0 mt-0.5" />
            <div className="text-sm text-surface-400">
              <p className="font-medium text-surface-300 mb-1">How it works</p>
              <p>
                We analyzed your spreadsheet and suggested how columns map to Revtops fields. 
                Review the mappings below and adjust if needed before importing.
              </p>
            </div>
          </div>
        </div>

        {/* Tab mappings */}
        <div className="space-y-6">
          {mappings.map((mapping) => {
            const tab = preview.tabs.find((t) => t.tab_name === mapping.tab_name);
            if (!tab) return null;

            const schema = preview.target_schemas[mapping.entity_type] || {};

            return (
              <div key={mapping.tab_name} className="card p-4">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h4 className="font-medium text-surface-100">{mapping.tab_name}</h4>
                    <p className="text-sm text-surface-500">~{tab.row_count} rows</p>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-sm text-surface-400">Import as:</span>
                    <select
                      value={mapping.entity_type}
                      onChange={(e) => updateMapping(mapping.tab_name, 'entity_type', e.target.value)}
                      className="bg-surface-800 border border-surface-700 rounded-lg px-3 py-1.5 text-sm text-surface-200"
                    >
                      <option value="contact">Contacts</option>
                      <option value="account">Accounts</option>
                      <option value="deal">Deals</option>
                    </select>
                    {mapping.confidence >= 0.7 && (
                      <span className="text-xs text-emerald-400 px-2 py-0.5 bg-emerald-500/10 rounded">
                        {Math.round(mapping.confidence * 100)}% confident
                      </span>
                    )}
                  </div>
                </div>

                {/* Sample data preview */}
                <div className="mb-4 overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-surface-700">
                        {tab.headers.map((header) => (
                          <th key={header} className="px-2 py-1 text-left text-surface-400 font-medium">
                            {header}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {tab.sample_rows.slice(0, 2).map((row, rowIdx) => (
                        <tr key={rowIdx} className="border-b border-surface-800">
                          {row.map((cell, cellIdx) => (
                            <td key={cellIdx} className="px-2 py-1 text-surface-300 truncate max-w-32">
                              {cell || <span className="text-surface-600">—</span>}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Column mappings */}
                <div className="space-y-2">
                  <p className="text-sm font-medium text-surface-300">Column Mappings</p>
                  <div className="grid grid-cols-2 gap-2">
                    {tab.headers.map((header) => {
                      const targetField = mapping.column_mappings[header] || '';
                      
                      return (
                        <div key={header} className="flex items-center gap-2">
                          <span className="text-sm text-surface-400 w-32 truncate" title={header}>
                            {header}
                          </span>
                          <HiChevronRight className="w-4 h-4 text-surface-600" />
                          <select
                            value={targetField}
                            onChange={(e) => updateColumnMapping(mapping.tab_name, header, e.target.value)}
                            className="flex-1 bg-surface-800 border border-surface-700 rounded px-2 py-1 text-sm text-surface-200"
                          >
                            <option value="">— Skip —</option>
                            {Object.entries(schema).map(([field]) => (
                              <option key={field} value={field}>
                                {field}
                              </option>
                            ))}
                          </select>
                        </div>
                      );
                    })}
                  </div>
                </div>

                {mapping.notes && (
                  <p className="mt-3 text-xs text-surface-500 italic">{mapping.notes}</p>
                )}
              </div>
            );
          })}
        </div>

        {/* Import button */}
        <div className="flex justify-end gap-3 pt-4 border-t border-surface-700">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-surface-400 hover:text-surface-300"
          >
            Cancel
          </button>
          <button
            onClick={() => void startImport()}
            disabled={mappings.length === 0}
            className="px-6 py-2 text-sm font-medium bg-primary-500 text-white rounded-lg hover:bg-primary-600 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Import Data
          </button>
        </div>
      </div>
    );
  };

  const renderImportingStep = (): JSX.Element => (
    <div className="flex flex-col items-center justify-center py-12 space-y-4">
      <div className="w-12 h-12 border-4 border-primary-500 border-t-transparent rounded-full animate-spin" />
      <div className="text-center">
        <h3 className="text-lg font-medium text-surface-100">Importing data...</h3>
        <p className="text-sm text-surface-400 mt-1">
          This may take a few minutes for large spreadsheets.
        </p>
      </div>
    </div>
  );

  const renderCompleteStep = (): JSX.Element => {
    if (!importResult) return <div>Loading...</div>;

    const isSuccess = importResult.status === 'completed';
    const results = importResult.results;

    return (
      <div className="space-y-6">
        <div className={`p-6 rounded-lg border ${isSuccess ? 'bg-emerald-500/10 border-emerald-500/30' : 'bg-red-500/10 border-red-500/30'}`}>
          <div className="flex items-center gap-3">
            {isSuccess ? (
              <div className="w-12 h-12 rounded-full bg-emerald-500/20 flex items-center justify-center">
                <HiCheck className="w-6 h-6 text-emerald-400" />
              </div>
            ) : (
              <div className="w-12 h-12 rounded-full bg-red-500/20 flex items-center justify-center">
                <HiX className="w-6 h-6 text-red-400" />
              </div>
            )}
            <div>
              <h3 className={`text-lg font-medium ${isSuccess ? 'text-emerald-400' : 'text-red-400'}`}>
                {isSuccess ? 'Import Complete!' : 'Import Failed'}
              </h3>
              <p className="text-sm text-surface-400">
                {importResult.spreadsheet_name || 'Spreadsheet'}
              </p>
            </div>
          </div>
        </div>

        {results && (
          <div className="grid grid-cols-3 gap-4">
            <div className="card p-4 text-center">
              <div className="text-2xl font-bold text-emerald-400">{results.created}</div>
              <div className="text-sm text-surface-400">Created</div>
            </div>
            <div className="card p-4 text-center">
              <div className="text-2xl font-bold text-blue-400">{results.updated}</div>
              <div className="text-sm text-surface-400">Updated</div>
            </div>
            <div className="card p-4 text-center">
              <div className="text-2xl font-bold text-surface-400">{results.skipped}</div>
              <div className="text-sm text-surface-400">Skipped</div>
            </div>
          </div>
        )}

        {importResult.error_message && (
          <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 text-red-400">
            <p className="font-medium">Error:</p>
            <p className="text-sm mt-1">{importResult.error_message}</p>
          </div>
        )}

        {importResult.errors && importResult.errors.length > 0 && (
          <div className="card p-4">
            <h4 className="font-medium text-surface-200 mb-3">
              Errors ({importResult.errors.length})
            </h4>
            <div className="space-y-2 max-h-48 overflow-y-auto text-sm">
              {importResult.errors.slice(0, 10).map((err, errIdx) => (
                <div key={errIdx} className="flex items-start gap-2 text-surface-400">
                  <span className="text-surface-500">{err.tab}</span>
                  {err.row && <span className="text-surface-500">Row {err.row}:</span>}
                  <span className="text-red-400">{err.error}</span>
                </div>
              ))}
              {importResult.errors.length > 10 && (
                <p className="text-surface-500 italic">
                  And {importResult.errors.length - 10} more errors...
                </p>
              )}
            </div>
          </div>
        )}

        <div className="flex justify-end gap-3 pt-4 border-t border-surface-700">
          <button
            onClick={onClose}
            className="px-6 py-2 text-sm font-medium bg-surface-700 text-surface-200 rounded-lg hover:bg-surface-600"
          >
            Close
          </button>
          <button
            onClick={() => {
              setStep('select');
              setSelectedSpreadsheet(null);
              setPreview(null);
              setMappings([]);
              setImportResult(null);
            }}
            className="px-6 py-2 text-sm font-medium bg-primary-500 text-white rounded-lg hover:bg-primary-600"
          >
            Import Another
          </button>
        </div>
      </div>
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-surface-900 border border-surface-700 rounded-xl shadow-2xl w-full max-w-3xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-surface-700">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-emerald-500/20 flex items-center justify-center">
              <SiGooglesheets className="w-5 h-5 text-emerald-400" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-surface-100">Import from Google Sheets</h2>
              <p className="text-sm text-surface-400">
                {step === 'select' && 'Select a spreadsheet'}
                {step === 'preview' && 'Review mappings'}
                {step === 'importing' && 'Importing...'}
                {step === 'complete' && 'Import complete'}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-surface-400 hover:text-surface-300 hover:bg-surface-800 rounded-lg"
          >
            <HiX className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          {renderStepContent()}
        </div>
      </div>
    </div>
  );
}
