/**
 * Data inspector component.
 *
 * SECURITY: Uses JWT authentication via apiRequest. Organization is determined
 * from the authenticated user, not from query parameters.
 */

import { useState, useEffect, useMemo, useCallback } from 'react';
import { apiRequest } from '../lib/api';
import { useAppStore } from '../store';

interface TableSummary {
  name: string;
  display_name: string;
  count: number;
}

interface DataRow {
  id: string;
  data: Record<string, string | number | boolean | null>;
}

interface DataResponse {
  table: string;
  rows: DataRow[];
  total: number;
  page: number;
  page_size: number;
  columns: string[];
  sort_by: string | null;
  sort_order: 'asc' | 'desc' | null;
}

interface FilterOptions {
  source_systems: string[];
  activity_types: string[] | null;
}

interface DataSummaryResponse {
  organization_id: string;
  tables: TableSummary[];
}

type SortOrder = 'asc' | 'desc';

function tableGroup(tableName: string): string {
  if (tableName === 'activities') return 'Activities';
  if (tableName === 'contacts' || tableName === 'accounts' || tableName === 'deals') return 'CRM';
  return 'Other';
}

export function Data(): JSX.Element {
  const { organization } = useAppStore();
  const setCurrentView = useAppStore((state) => state.setCurrentView);
  const startNewChat = useAppStore((state) => state.startNewChat);
  const setPendingChatInput = useAppStore((state) => state.setPendingChatInput);
  const setPendingChatAutoSend = useAppStore((state) => state.setPendingChatAutoSend);

  const [tables, setTables] = useState<TableSummary[]>([]);
  const [selectedTables, setSelectedTables] = useState<string[]>(['contacts']);
  const [dataByTable, setDataByTable] = useState<Record<string, DataResponse>>({});
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState<number>(1);
  const [search, setSearch] = useState<string>('');
  const [searchInput, setSearchInput] = useState<string>('');
  const [sortBy, setSortBy] = useState<string | null>(null);
  const [sortOrder, setSortOrder] = useState<SortOrder>('asc');
  const [filterOptions, setFilterOptions] = useState<FilterOptions | null>(null);
  const [sourceSystemFilter, setSourceSystemFilter] = useState<string>('');
  const [typeFilter, setTypeFilter] = useState<string>('');

  const organizationId = organization?.id ?? '';
  const primaryTable = selectedTables[0] ?? '';

  useEffect(() => {
    if (!organizationId) return;

    const fetchSummary = async (): Promise<void> => {
      try {
        const { data: result, error: apiError } = await apiRequest<DataSummaryResponse>('/data/summary');
        if (apiError || !result) {
          throw new Error(apiError || 'Failed to fetch data summary');
        }
        setTables(result.tables);
        if (!result.tables.some((table) => selectedTables.includes(table.name)) && result.tables[0]) {
          setSelectedTables([result.tables[0].name]);
        }
      } catch (err) {
        console.error('[Data] Error fetching summary', err);
      }
    };

    void fetchSummary();
  }, [organizationId, selectedTables]);

  useEffect(() => {
    if (!organizationId || !primaryTable) return;

    const fetchFilters = async (): Promise<void> => {
      try {
        const { data: result, error: apiError } = await apiRequest<FilterOptions>(`/data/${primaryTable}/filters`);
        if (apiError || !result) {
          throw new Error(apiError || 'Failed to fetch filters');
        }
        setFilterOptions(result);
      } catch (err) {
        console.error('[Data] Error fetching filter options', err);
        setFilterOptions(null);
      }
    };

    void fetchFilters();
  }, [organizationId, primaryTable]);

  useEffect(() => {
    const timeout = setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => clearTimeout(timeout);
  }, [searchInput]);

  useEffect(() => {
    if (!organizationId || selectedTables.length === 0) return;

    const fetchData = async (): Promise<void> => {
      setLoading(true);
      setError(null);
      try {
        const resultEntries = await Promise.all(
          selectedTables.map(async (tableName) => {
            const params = new URLSearchParams({
              page: String(page),
              page_size: '50',
            });

            if (search) {
              params.set('search', search);
            }
            if (sortBy) {
              params.set('sort_by', sortBy);
              params.set('sort_order', sortOrder);
            }
            if (sourceSystemFilter) {
              params.set('source_system', sourceSystemFilter);
            }
            if (typeFilter && tableName === 'activities') {
              params.set('type_filter', typeFilter);
            }

            const { data: result, error: apiError } = await apiRequest<DataResponse>(
              `/data/${tableName}?${params.toString()}`,
            );
            if (apiError || !result) {
              throw new Error(apiError || `Failed to fetch ${tableName}`);
            }
            return [tableName, result] as const;
          }),
        );

        setDataByTable(Object.fromEntries(resultEntries));
      } catch (err) {
        console.error('[Data] Error fetching table data', err);
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    void fetchData();
  }, [organizationId, selectedTables, page, search, sortBy, sortOrder, sourceSystemFilter, typeFilter]);

  useEffect(() => {
    setPage(1);
    setSortBy(null);
    setSortOrder('asc');
    setSourceSystemFilter('');
    setTypeFilter('');
  }, [primaryTable]);

  useEffect(() => {
    setPage(1);
  }, [search, sourceSystemFilter, typeFilter]);

  const handleSort = (column: string): void => {
    if (sortBy === column) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      setSortBy(column);
      setSortOrder('asc');
    }
    setPage(1);
  };

  const toggleTarget = useCallback((tableName: string): void => {
    setSelectedTables((prev) => {
      if (prev.includes(tableName)) {
        if (prev.length === 1) return prev;
        return prev.filter((name) => name !== tableName);
      }
      return [...prev, tableName];
    });
  }, []);

  const formatColumnHeader = (col: string): string => col.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase());

  const formatCellValue = (value: string | number | boolean | null | undefined): string => {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (typeof value === 'number') return value >= 1000 ? value.toLocaleString() : String(value);
    const str = String(value);
    return str.length > 50 ? `${str.substring(0, 47)}...` : str;
  };

  const groupedTables = useMemo(() => {
    return selectedTables.reduce<Record<string, string[]>>((acc, tableName) => {
      const group = tableGroup(tableName);
      acc[group] = acc[group] ?? [];
      acc[group].push(tableName);
      return acc;
    }, {});
  }, [selectedTables]);

  const singleTableResult = selectedTables.length === 1 && primaryTable ? dataByTable[primaryTable] : null;
  const totalPages = singleTableResult ? Math.ceil(singleTableResult.total / singleTableResult.page_size) : 0;

  const buildChatPrompt = useCallback((): string => {
    const sections = selectedTables.map((tableName) => {
      const result = dataByTable[tableName];
      if (!result) return `${tableName}: no rows returned`;
      const sampleRows = result.rows.slice(0, 5).map((row, idx) => `${idx + 1}. ${JSON.stringify(row.data)}`).join('\n');
      return `${tableName} (total ${result.total}):\n${sampleRows || 'No rows returned'}`;
    });
    return `summarise this data\n\n${sections.join('\n\n')}`;
  }, [dataByTable, selectedTables]);

  const handleStartChat = useCallback((): void => {
    setPendingChatInput(buildChatPrompt());
    setPendingChatAutoSend(false);
    startNewChat();
    setCurrentView('chat');
  }, [buildChatPrompt, setCurrentView, setPendingChatAutoSend, setPendingChatInput, startNewChat]);

  return (
    <div className="flex-1 overflow-hidden flex flex-col">
      <header className="sticky top-0 bg-surface-950 border-b border-surface-800 px-4 md:px-8 py-4 md:py-6">
        <h1 className="text-xl md:text-2xl font-bold text-surface-50">Search Data</h1>
        <p className="text-surface-400 mt-1 text-sm md:text-base">Browse your synced data from connected sources</p>
      </header>

      <div className="flex-1 overflow-hidden flex flex-col px-4 md:px-8 py-4 md:py-6">
        <div className="flex flex-wrap gap-2 mb-4">
          {tables.map((table) => (
            <button
              key={table.name}
              onClick={() => toggleTarget(table.name)}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                selectedTables.includes(table.name)
                  ? 'bg-primary-500 text-white'
                  : 'bg-surface-800 text-surface-300 hover:bg-surface-700'
              }`}
            >
              {table.display_name}
              <span className="ml-2 px-2 py-0.5 rounded-full text-xs bg-surface-900/50">{table.count.toLocaleString()}</span>
            </button>
          ))}
        </div>

        <div className="flex flex-wrap gap-3 mb-4">
          {filterOptions && filterOptions.source_systems.length > 0 && (
            <select
              value={sourceSystemFilter}
              onChange={(e) => setSourceSystemFilter(e.target.value)}
              className="px-3 py-2 text-sm font-medium bg-surface-800 border border-surface-700 rounded-lg text-surface-100 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            >
              <option value="">All Sources</option>
              {filterOptions.source_systems.map((source) => (
                <option key={source} value={source}>
                  {source.charAt(0).toUpperCase() + source.slice(1)}
                </option>
              ))}
            </select>
          )}

          {selectedTables.includes('activities') && filterOptions?.activity_types && filterOptions.activity_types.length > 0 && (
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="px-3 py-2 text-sm font-medium bg-surface-800 border border-surface-700 rounded-lg text-surface-100 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            >
              <option value="">All Types</option>
              {filterOptions.activity_types.map((type) => (
                <option key={type} value={type}>
                  {type.charAt(0).toUpperCase() + type.slice(1)}
                </option>
              ))}
            </select>
          )}

          <div className="flex-1 flex gap-2">
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder={`Search ${selectedTables.join(', ') || 'data'}...`}
              className="flex-1 min-w-[200px] px-4 py-2 bg-surface-800 border border-surface-700 rounded-lg text-surface-100 placeholder-surface-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            />
          </div>

          <button
            type="button"
            onClick={handleStartChat}
            disabled={loading || Object.keys(dataByTable).length === 0}
            className="px-4 py-2 bg-primary-600 hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg transition-colors"
          >
            Start Chat
          </button>

          {(search || sourceSystemFilter || typeFilter) && (
            <button
              type="button"
              onClick={() => {
                setSearch('');
                setSearchInput('');
                setSourceSystemFilter('');
                setTypeFilter('');
              }}
              className="px-4 py-2 text-surface-400 hover:text-surface-200 transition-colors"
            >
              Clear All
            </button>
          )}
        </div>

        <div className="flex-1 overflow-auto space-y-4">
          {loading ? (
            <div className="flex items-center justify-center h-64 bg-surface-900 rounded-lg border border-surface-800">
              <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : error ? (
            <div className="flex items-center justify-center h-64 text-red-400 bg-surface-900 rounded-lg border border-surface-800">{error}</div>
          ) : (
            Object.entries(groupedTables).map(([group, groupTableNames]) => (
              <section key={group} className="space-y-3">
                {selectedTables.length > 1 && <h2 className="text-sm uppercase tracking-wider text-surface-400 font-semibold">{group}</h2>}
                {groupTableNames.map((tableName) => {
                  const result = dataByTable[tableName];
                  if (!result || result.rows.length === 0) {
                    return (
                      <div key={tableName} className="bg-surface-900 rounded-lg border border-surface-800 p-4 text-surface-400">
                        <p className="font-medium text-surface-200 mb-1">{tableName}</p>
                        <p className="text-sm">No rows returned.</p>
                      </div>
                    );
                  }
                  return (
                    <div key={tableName} className="bg-surface-900 rounded-lg border border-surface-800 overflow-auto">
                      <div className="px-4 py-3 border-b border-surface-800 text-sm font-medium text-surface-200">
                        {tableName} · {result.total.toLocaleString()} total
                      </div>
                      <table className="w-full text-sm">
                        <thead className="bg-surface-800 sticky top-0">
                          <tr>
                            {result.columns.map((col) => (
                              <th
                                key={col}
                                onClick={() => handleSort(col)}
                                className="px-4 py-3 text-left text-surface-300 font-medium whitespace-nowrap cursor-pointer hover:bg-surface-700 transition-colors"
                              >
                                <div className="flex items-center gap-1">
                                  {formatColumnHeader(col)}
                                  {sortBy === col && (
                                    <span>{sortOrder === 'asc' ? '↑' : '↓'}</span>
                                  )}
                                </div>
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-surface-800">
                          {result.rows.map((row) => (
                            <tr key={row.id} className="hover:bg-surface-800/50">
                              {result.columns.map((col) => (
                                <td key={col} className="px-4 py-3 text-surface-200 whitespace-nowrap">
                                  {formatCellValue(row.data[col])}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  );
                })}
              </section>
            ))
          )}
        </div>

        {singleTableResult && singleTableResult.total > singleTableResult.page_size && (
          <div className="flex items-center justify-between mt-4 text-sm">
            <span className="text-surface-400">
              Showing {((page - 1) * singleTableResult.page_size) + 1} - {Math.min(page * singleTableResult.page_size, singleTableResult.total)} of {singleTableResult.total.toLocaleString()}
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="px-3 py-1.5 bg-surface-800 hover:bg-surface-700 disabled:opacity-50 disabled:cursor-not-allowed text-surface-200 rounded transition-colors"
              >
                Previous
              </button>
              <span className="px-3 py-1.5 text-surface-400">Page {page} of {totalPages}</span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className="px-3 py-1.5 bg-surface-800 hover:bg-surface-700 disabled:opacity-50 disabled:cursor-not-allowed text-surface-200 rounded transition-colors"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
