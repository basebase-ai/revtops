import { useEffect, useMemo, useState } from 'react';
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

interface TypeaheadSuggestion {
  table: string;
  label: string;
}

interface TypeaheadResponse {
  suggestions: TypeaheadSuggestion[];
}

type SortOrder = 'asc' | 'desc';

export function Data(): JSX.Element {
  const { organization } = useAppStore();
  const setCurrentView = useAppStore((state) => state.setCurrentView);
  const startNewChat = useAppStore((state) => state.startNewChat);
  const setPendingChatInput = useAppStore((state) => state.setPendingChatInput);
  const setPendingChatAutoSend = useAppStore((state) => state.setPendingChatAutoSend);

  const [tables, setTables] = useState<TableSummary[]>([]);
  const [selectedTables, setSelectedTables] = useState<string[]>(['contacts']);
  const [tableData, setTableData] = useState<Record<string, DataResponse>>({});
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState<number>(1);
  const [searchInput, setSearchInput] = useState<string>('');
  const [search, setSearch] = useState<string>('');
  const [sortBy, setSortBy] = useState<string | null>(null);
  const [sortOrder, setSortOrder] = useState<SortOrder>('asc');
  const [filterOptions, setFilterOptions] = useState<FilterOptions | null>(null);
  const [sourceSystemFilter, setSourceSystemFilter] = useState<string>('');
  const [typeFilter, setTypeFilter] = useState<string>('');
  const [suggestions, setSuggestions] = useState<TypeaheadSuggestion[]>([]);

  const organizationId = organization?.id ?? '';
  const isSingleTable = selectedTables.length === 1;
  const primaryTable = selectedTables[0] ?? 'contacts';

  useEffect(() => {
    if (!organizationId) return;

    const fetchSummary = async (): Promise<void> => {
      try {
        const { data: result, error: apiError } = await apiRequest<DataSummaryResponse>('/data/summary');
        if (apiError || !result) {
          throw new Error(apiError || 'Failed to fetch data summary');
        }
        setTables(result.tables);
      } catch (err) {
        console.error('Error fetching data summary:', err);
      }
    };

    void fetchSummary();
  }, [organizationId]);

  useEffect(() => {
    const timeoutId = setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(1);
    }, 300);

    return () => clearTimeout(timeoutId);
  }, [searchInput]);

  useEffect(() => {
    if (!organizationId || !isSingleTable) {
      setFilterOptions(null);
      return;
    }

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
  }, [organizationId, isSingleTable, primaryTable]);

  useEffect(() => {
    if (!organizationId) return;

    const fetchData = async (): Promise<void> => {
      setLoading(true);
      setError(null);

      try {
        const requests = selectedTables.map(async (table) => {
          const params = new URLSearchParams({
            page: String(isSingleTable ? page : 1),
            page_size: isSingleTable ? '50' : '20',
          });

          if (search) {
            params.set('search', search);
          }

          if (isSingleTable && sortBy) {
            params.set('sort_by', sortBy);
            params.set('sort_order', sortOrder);
          }

          if (isSingleTable && sourceSystemFilter) {
            params.set('source_system', sourceSystemFilter);
          }

          if (isSingleTable && typeFilter && table === 'activities') {
            params.set('type_filter', typeFilter);
          }

          const { data: result, error: apiError } = await apiRequest<DataResponse>(`/data/${table}?${params.toString()}`);
          if (apiError || !result) {
            throw new Error(apiError || `Failed to fetch ${table}`);
          }

          return [table, result] as const;
        });

        const resolved = await Promise.all(requests);
        setTableData(Object.fromEntries(resolved));
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    void fetchData();
  }, [organizationId, selectedTables, isSingleTable, page, search, sortBy, sortOrder, sourceSystemFilter, typeFilter]);

  useEffect(() => {
    if (isSingleTable) return;
    setSortBy(null);
    setTypeFilter('');
    setSourceSystemFilter('');
    setPage(1);
  }, [isSingleTable]);

  useEffect(() => {
    if (!organizationId || searchInput.trim().length < 2) {
      setSuggestions([]);
      return;
    }

    const timeoutId = setTimeout(() => {
      const tablesParam = selectedTables.join(',');
      void apiRequest<TypeaheadResponse>(`/data/typeahead?query=${encodeURIComponent(searchInput.trim())}&tables=${encodeURIComponent(tablesParam)}&limit=8`)
        .then(({ data }) => {
          setSuggestions(data?.suggestions ?? []);
        })
        .catch(() => {
          setSuggestions([]);
        });
    }, 250);

    return () => clearTimeout(timeoutId);
  }, [organizationId, searchInput, selectedTables]);

  const toggleTableSelection = (tableName: string): void => {
    setSelectedTables((prev) => {
      if (prev.includes(tableName)) {
        if (prev.length === 1) return prev;
        return prev.filter((t) => t !== tableName);
      }
      return [...prev, tableName];
    });
  };

  const handleSort = (column: string): void => {
    if (!isSingleTable) return;

    if (sortBy === column) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      setSortBy(column);
      setSortOrder('asc');
    }
    setPage(1);
  };

  const formatColumnHeader = (col: string): string => col.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase());

  const formatCellValue = (value: string | number | boolean | null | undefined): string => {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (typeof value === 'number') return value.toLocaleString();
    return String(value);
  };

  const groupedResults = useMemo(() => {
    return selectedTables
      .map((tableName) => ({
        tableName,
        displayName: tables.find((table) => table.name === tableName)?.display_name ?? tableName,
        result: tableData[tableName],
      }))
      .filter((group) => Boolean(group.result));
  }, [selectedTables, tables, tableData]);

  const singleTableResult = isSingleTable ? tableData[primaryTable] : null;
  const totalPages = singleTableResult ? Math.ceil(singleTableResult.total / singleTableResult.page_size) : 0;

  const buildSummaryPrompt = (): string => {
    const blocks = groupedResults.map(({ displayName, result }) => {
      const rows = result?.rows.slice(0, 10) ?? [];
      const content = rows.map((row) => JSON.stringify(row.data)).join('\n');
      return `## ${displayName}\n${content || 'No rows returned.'}`;
    });
    return `summarise this data\n\n${blocks.join('\n\n')}`;
  };

  const handleSummarizeInChat = (): void => {
    const prompt = buildSummaryPrompt();
    setPendingChatInput(prompt);
    setPendingChatAutoSend(false);
    startNewChat();
    setCurrentView('chat');
  };

  return (
    <div className="flex-1 flex flex-col bg-surface-950 min-h-0">
      <div className="px-6 py-4 border-b border-surface-800">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-surface-100">Search Data</h1>
            <p className="text-sm text-surface-400 mt-1">Explore CRM records by selecting one or more data targets.</p>
          </div>
          <button
            type="button"
            onClick={handleSummarizeInChat}
            className="px-3 py-2 bg-primary-600 hover:bg-primary-700 text-white text-sm font-medium rounded-lg transition-colors"
            disabled={groupedResults.length === 0}
          >
            Summarize in Chat
          </button>
        </div>
      </div>

      <div className="flex-1 p-6 min-h-0 overflow-hidden flex flex-col">
        <div className="mb-4 flex flex-wrap gap-2">
          {tables.map((table) => {
            const selected = selectedTables.includes(table.name);
            return (
              <button
                key={table.name}
                type="button"
                onClick={() => toggleTableSelection(table.name)}
                className={`px-3 py-2 rounded-lg border text-sm font-medium transition-colors ${
                  selected
                    ? 'bg-primary-500/15 border-primary-500 text-primary-300'
                    : 'bg-surface-900 border-surface-700 text-surface-300 hover:bg-surface-800'
                }`}
              >
                {table.display_name} <span className="ml-1 text-xs text-surface-500">{table.count.toLocaleString()}</span>
              </button>
            );
          })}
        </div>

        <div className="flex flex-wrap gap-3 mb-4 items-start">
          {isSingleTable && filterOptions && filterOptions.source_systems.length > 0 && (
            <select
              value={sourceSystemFilter}
              onChange={(e) => setSourceSystemFilter(e.target.value)}
              className="px-3 py-2 text-sm bg-surface-800 border border-surface-700 rounded-lg text-surface-100"
            >
              <option value="">All Sources</option>
              {filterOptions.source_systems.map((source) => (
                <option key={source} value={source}>{source.charAt(0).toUpperCase() + source.slice(1)}</option>
              ))}
            </select>
          )}

          {isSingleTable && primaryTable === 'activities' && filterOptions?.activity_types && filterOptions.activity_types.length > 0 && (
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="px-3 py-2 text-sm bg-surface-800 border border-surface-700 rounded-lg text-surface-100"
            >
              <option value="">All Types</option>
              {filterOptions.activity_types.map((type) => (
                <option key={type} value={type}>{type.charAt(0).toUpperCase() + type.slice(1)}</option>
              ))}
            </select>
          )}

          <div className="flex-1 min-w-[260px] relative">
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search selected data targets..."
              className="w-full px-4 py-2 bg-surface-800 border border-surface-700 rounded-lg text-surface-100 placeholder-surface-500"
            />
            {suggestions.length > 0 && (
              <div className="absolute z-10 top-full mt-1 w-full bg-surface-900 border border-surface-700 rounded-lg overflow-hidden shadow-xl">
                {suggestions.map((suggestion, idx) => (
                  <button
                    key={`${suggestion.table}-${suggestion.label}-${idx}`}
                    type="button"
                    onClick={() => setSearchInput(suggestion.label)}
                    className="w-full text-left px-3 py-2 hover:bg-surface-800 text-sm text-surface-200"
                  >
                    <span className="text-surface-500 mr-2">{suggestion.table}</span>
                    {suggestion.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="flex-1 overflow-auto bg-surface-900 rounded-lg border border-surface-800">
          {loading ? (
            <div className="h-64 flex items-center justify-center"><div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" /></div>
          ) : error ? (
            <div className="h-64 flex items-center justify-center text-red-400">{error}</div>
          ) : groupedResults.length > 0 ? (
            <div className="divide-y divide-surface-800">
              {groupedResults.map(({ tableName, displayName, result }) => {
                if (!result) return null;
                return (
                  <section key={tableName} className="p-4">
                    <h2 className="text-sm font-semibold uppercase tracking-wide text-surface-400 mb-3">{displayName}</h2>
                    {result.rows.length === 0 ? (
                      <p className="text-sm text-surface-500">No matching rows.</p>
                    ) : (
                      <table className="w-full text-sm">
                        <thead className="bg-surface-800">
                          <tr>
                            {result.columns.map((col) => (
                              <th
                                key={col}
                                onClick={() => tableName === primaryTable && handleSort(col)}
                                className="px-3 py-2 text-left text-surface-300 font-medium whitespace-nowrap"
                              >
                                {formatColumnHeader(col)}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-surface-800">
                          {result.rows.map((row) => (
                            <tr key={row.id} className="hover:bg-surface-800/50">
                              {result.columns.map((col) => (
                                <td key={col} className="px-3 py-2 text-surface-200 whitespace-nowrap">{formatCellValue(row.data[col])}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </section>
                );
              })}
            </div>
          ) : (
            <div className="h-64 flex items-center justify-center text-surface-400">No data available.</div>
          )}
        </div>

        {isSingleTable && singleTableResult && singleTableResult.total > singleTableResult.page_size && (
          <div className="flex items-center justify-between mt-4 text-sm">
            <span className="text-surface-400">
              Showing {((page - 1) * singleTableResult.page_size) + 1} - {Math.min(page * singleTableResult.page_size, singleTableResult.total)} of {singleTableResult.total.toLocaleString()}
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="px-3 py-1.5 bg-surface-800 hover:bg-surface-700 disabled:opacity-50 text-surface-200 rounded"
              >
                Previous
              </button>
              <span className="px-3 py-1.5 text-surface-400">Page {page} of {totalPages}</span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className="px-3 py-1.5 bg-surface-800 hover:bg-surface-700 disabled:opacity-50 text-surface-200 rounded"
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
