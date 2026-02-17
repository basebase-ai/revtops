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

type DataByTable = Record<string, DataResponse>;

export function Data(): JSX.Element {
  const { organization } = useAppStore();
  const setCurrentView = useAppStore((state) => state.setCurrentView);
  const startNewChat = useAppStore((state) => state.startNewChat);
  const setPendingChatInput = useAppStore((state) => state.setPendingChatInput);
  const setPendingChatAutoSend = useAppStore((state) => state.setPendingChatAutoSend);

  const [tables, setTables] = useState<TableSummary[]>([]);
  const [selectedTables, setSelectedTables] = useState<string[]>(['contacts']);
  const [primaryTable, setPrimaryTable] = useState<string>('contacts');
  const [dataByTable, setDataByTable] = useState<DataByTable>({});
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

  useEffect(() => {
    const timeoutId = setTimeout(() => {
      setSearch(searchInput.trim());
    }, 300);
    return () => clearTimeout(timeoutId);
  }, [searchInput]);

  useEffect(() => {
    if (!organizationId) return;

    const fetchSummary = async (): Promise<void> => {
      try {
        const { data: result, error: apiError } = await apiRequest<DataSummaryResponse>('/data/summary');
        if (apiError || !result) {
          throw new Error(apiError || 'Failed to fetch data summary');
        }
        setTables(result.tables);

        const availableTables = new Set(result.tables.map((table) => table.name));
        const validSelected = selectedTables.filter((table) => availableTables.has(table));
        if (validSelected.length === 0 && result.tables[0]) {
          const fallback = result.tables[0].name;
          setSelectedTables([fallback]);
          setPrimaryTable(fallback);
        } else if (validSelected.length !== selectedTables.length) {
          setSelectedTables(validSelected);
          setPrimaryTable(validSelected[0] ?? result.tables[0]?.name ?? 'contacts');
        }
      } catch (err) {
        console.error('Error fetching data summary:', err);
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
        console.error('Error fetching filter options:', err);
        setFilterOptions(null);
      }
    };

    void fetchFilters();
  }, [organizationId, primaryTable]);

  useEffect(() => {
    if (!organizationId || selectedTables.length === 0) return;

    const fetchData = async (): Promise<void> => {
      setLoading(true);
      setError(null);
      try {
        const responses = await Promise.all(
          selectedTables.map(async (tableName) => {
            const params = new URLSearchParams({
              page: String(page),
              page_size: '50',
            });
            if (search) params.set('search', search);
            if (sortBy) {
              params.set('sort_by', sortBy);
              params.set('sort_order', sortOrder);
            }
            if (sourceSystemFilter) params.set('source_system', sourceSystemFilter);
            if (typeFilter && tableName === 'activities') params.set('type_filter', typeFilter);

            const { data: result, error: apiError } = await apiRequest<DataResponse>(`/data/${tableName}?${params.toString()}`);
            if (apiError || !result) {
              throw new Error(apiError || `Failed to fetch ${tableName}`);
            }
            return [tableName, result] as const;
          })
        );

        const nextData: DataByTable = {};
        for (const [tableName, result] of responses) {
          nextData[tableName] = result;
        }
        setDataByTable(nextData);
      } catch (err) {
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
  }, [search, sourceSystemFilter, typeFilter, selectedTables]);

  const handleSort = (column: string): void => {
    if (sortBy === column) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      setSortBy(column);
      setSortOrder('asc');
    }
    setPage(1);
  };

  const toggleTable = (tableName: string): void => {
    setSelectedTables((prev) => {
      if (prev.includes(tableName)) {
        if (prev.length === 1) return prev;
        const next = prev.filter((name) => name !== tableName);
        if (primaryTable === tableName && next[0]) {
          setPrimaryTable(next[0]);
        }
        return next;
      }
      setPrimaryTable(tableName);
      return [...prev, tableName];
    });
  };

  const groupedResults = useMemo(() => {
    return selectedTables
      .map((tableName) => {
        const tableMeta = tables.find((table) => table.name === tableName);
        const result = dataByTable[tableName];
        return {
          tableName,
          title: tableMeta?.display_name ?? tableName,
          result,
        };
      })
      .filter((group) => !!group.result);
  }, [dataByTable, selectedTables, tables]);

  const startChatWithData = useCallback((): void => {
    const promptSections = groupedResults.map((group) => {
      const result = group.result;
      if (!result) return `${group.title}: no rows returned.`;
      const previewRows = result.rows.slice(0, 5).map((row) => {
        const rowData = result.columns.slice(0, 6).map((column) => `${column}: ${String(row.data[column] ?? '—')}`).join(', ');
        return `- ${rowData}`;
      }).join('\n');

      return `${group.title} (${result.total} total rows):\n${previewRows || '- no rows returned'}`;
    }).join('\n\n');

    const prompt = `Summarise this data:\n\n${promptSections}`;
    setPendingChatInput(prompt);
    setPendingChatAutoSend(true);
    startNewChat();
    setCurrentView('chat');
  }, [groupedResults, setCurrentView, setPendingChatAutoSend, setPendingChatInput, startNewChat]);

  const formatColumnHeader = (col: string): string => col.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase());

  const formatCellValue = (value: string | number | boolean | null | undefined): string => {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (typeof value === 'number') return value >= 1000 ? value.toLocaleString() : String(value);
    const str = String(value);
    return str.length > 50 ? `${str.substring(0, 47)}...` : str;
  };

  const firstSelectedTable = selectedTables[0];
  const singleTableResult = selectedTables.length === 1 && firstSelectedTable ? dataByTable[firstSelectedTable] : null;
  const totalPages = singleTableResult ? Math.ceil(singleTableResult.total / singleTableResult.page_size) : 0;

  return (
    <div className="flex-1 overflow-hidden flex flex-col">
      <header className="sticky top-0 bg-surface-950 border-b border-surface-800 px-4 md:px-8 py-4 md:py-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl md:text-2xl font-bold text-surface-50">Search Data</h1>
            <p className="text-surface-400 mt-1 text-sm md:text-base">Browse your synced data from connected sources</p>
          </div>
          <button
            type="button"
            onClick={startChatWithData}
            disabled={groupedResults.length === 0 || loading}
            className="px-4 py-2 rounded-lg bg-primary-600 hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium transition-colors"
          >
            Start Chat
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-hidden flex flex-col px-4 md:px-8 py-4 md:py-6">
        <div className="flex flex-wrap gap-2 mb-4">
          {tables.map((table) => {
            const isSelected = selectedTables.includes(table.name);
            return (
              <button
                key={table.name}
                onClick={() => toggleTable(table.name)}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                  isSelected ? 'bg-primary-500 text-white' : 'bg-surface-800 text-surface-300 hover:bg-surface-700'
                }`}
              >
                {table.display_name}
                <span className="ml-2 px-2 py-0.5 rounded-full text-xs bg-surface-900/50">{table.count.toLocaleString()}</span>
              </button>
            );
          })}
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
                <option key={source} value={source}>{source.charAt(0).toUpperCase() + source.slice(1)}</option>
              ))}
            </select>
          )}

          {primaryTable === 'activities' && filterOptions?.activity_types && filterOptions.activity_types.length > 0 && (
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="px-3 py-2 text-sm font-medium bg-surface-800 border border-surface-700 rounded-lg text-surface-100 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            >
              <option value="">All Types</option>
              {filterOptions.activity_types.map((type) => (
                <option key={type} value={type}>{type.charAt(0).toUpperCase() + type.slice(1)}</option>
              ))}
            </select>
          )}

          <div className="flex-1 min-w-[240px]">
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder={`Search ${selectedTables.join(', ')}...`}
              className="w-full px-4 py-2 bg-surface-800 border border-surface-700 rounded-lg text-surface-100 placeholder-surface-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            />
          </div>

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
            <div className="flex items-center justify-center h-64 bg-surface-900 rounded-lg border border-surface-800 text-red-400">{error}</div>
          ) : groupedResults.length > 0 ? (
            groupedResults.map((group) => {
              const result = group.result;
              if (!result) return null;

              return (
                <section key={group.tableName} className="bg-surface-900 rounded-lg border border-surface-800 overflow-hidden">
                  <div className="px-4 py-3 border-b border-surface-800 flex items-center justify-between">
                    <h2 className="text-sm font-semibold text-surface-100">{group.title}</h2>
                    <span className="text-xs text-surface-400">{result.total.toLocaleString()} rows</span>
                  </div>
                  {result.rows.length > 0 ? (
                    <table className="w-full text-sm">
                      <thead className="bg-surface-800 sticky top-0">
                        <tr>
                          {result.columns.map((col) => (
                            <th
                              key={`${group.tableName}-${col}`}
                              onClick={() => handleSort(col)}
                              className="px-4 py-3 text-left text-surface-300 font-medium whitespace-nowrap cursor-pointer hover:bg-surface-700 transition-colors select-none"
                            >
                              <div className="flex items-center gap-1">
                                {formatColumnHeader(col)}
                                <span className="text-surface-500">
                                  {sortBy === col ? (
                                    sortOrder === 'asc' ? '↑' : '↓'
                                  ) : null}
                                </span>
                              </div>
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-surface-800">
                        {result.rows.map((row) => (
                          <tr key={row.id} className="hover:bg-surface-800/50">
                            {result.columns.map((col) => (
                              <td key={`${row.id}-${col}`} className="px-4 py-3 text-surface-200 whitespace-nowrap">
                                {formatCellValue(row.data[col])}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <div className="px-4 py-10 text-center text-surface-400">
                      No {group.title.toLowerCase()} found
                    </div>
                  )}
                </section>
              );
            })
          ) : (
            <div className="flex flex-col items-center justify-center h-64 text-surface-400 bg-surface-900 rounded-lg border border-surface-800">
              <p>No data found</p>
            </div>
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
