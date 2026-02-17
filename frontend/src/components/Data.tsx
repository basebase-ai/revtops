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

export function Data(): JSX.Element {
  const { organization, setCurrentView, startNewChat, setPendingChatInput, setPendingChatAutoSend } = useAppStore();
  const [tables, setTables] = useState<TableSummary[]>([]);
  const [selectedTargets, setSelectedTargets] = useState<string[]>(['contacts']);
  const [dataByTarget, setDataByTarget] = useState<Record<string, DataResponse>>({});
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
  const isMultiTarget = selectedTargets.length > 1;

  useEffect(() => {
    const timeout = setTimeout(() => {
      setSearch(searchInput.trim());
    }, 350);
    return () => clearTimeout(timeout);
  }, [searchInput]);

  useEffect(() => {
    if (!organizationId) return;

    const fetchSummary = async (): Promise<void> => {
      try {
        const { data: result, error: apiError } = await apiRequest<DataSummaryResponse>('/data/summary');
        if (apiError || !result) {
          throw new Error(apiError || 'Failed to fetch data summary');
        }
        console.debug('[Data] Loaded table summary', result.tables.length);
        setTables(result.tables);
      } catch (err) {
        console.error('Error fetching data summary:', err);
      }
    };

    void fetchSummary();
  }, [organizationId]);

  useEffect(() => {
    if (!organizationId || selectedTargets.length === 0) return;

    const fetchFilters = async (): Promise<void> => {
      try {
        const { data: result, error: apiError } = await apiRequest<FilterOptions>(`/data/${selectedTargets[0]}/filters`);
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
  }, [organizationId, selectedTargets]);

  useEffect(() => {
    if (!organizationId || selectedTargets.length === 0) return;

    const fetchData = async (): Promise<void> => {
      setLoading(true);
      setError(null);
      try {
        const responses = await Promise.all(selectedTargets.map(async (target) => {
          const params = new URLSearchParams({ page: String(page), page_size: '25' });
          if (search) params.set('search', search);
          if (sortBy) {
            params.set('sort_by', sortBy);
            params.set('sort_order', sortOrder);
          }
          if (sourceSystemFilter) params.set('source_system', sourceSystemFilter);
          if (typeFilter && target === 'activities') params.set('type_filter', typeFilter);

          const { data: result, error: apiError } = await apiRequest<DataResponse>(`/data/${target}?${params.toString()}`);
          if (apiError || !result) {
            throw new Error(`Failed to fetch ${target}: ${apiError || 'unknown error'}`);
          }
          return [target, result] as const;
        }));

        const nextDataByTarget: Record<string, DataResponse> = {};
        responses.forEach(([target, result]) => {
          nextDataByTarget[target] = result;
        });
        console.debug('[Data] Loaded data targets', Object.keys(nextDataByTarget));
        setDataByTarget(nextDataByTarget);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    void fetchData();
  }, [organizationId, selectedTargets, page, search, sortBy, sortOrder, sourceSystemFilter, typeFilter]);

  useEffect(() => {
    setPage(1);
  }, [search, sourceSystemFilter, typeFilter, selectedTargets]);

  const handleSort = (column: string): void => {
    if (sortBy === column) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      setSortBy(column);
      setSortOrder('asc');
    }
  };

  const toggleTarget = (target: string): void => {
    setSelectedTargets((previousTargets) => {
      if (previousTargets.includes(target)) {
        if (previousTargets.length === 1) {
          return previousTargets;
        }
        return previousTargets.filter((entry) => entry !== target);
      }
      return [...previousTargets, target];
    });
  };

  const formatColumnHeader = (col: string): string => col.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase());

  const formatCellValue = (value: string | number | boolean | null | undefined): string => {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (typeof value === 'number') return value >= 1000 ? value.toLocaleString() : String(value);
    const str = String(value);
    return str.length > 50 ? `${str.substring(0, 47)}...` : str;
  };

  const groupedResults = useMemo(() => {
    const groups: Array<{ target: string; table: TableSummary | undefined; data: DataResponse | undefined }> = [];
    selectedTargets.forEach((target) => {
      groups.push({
        target,
        table: tables.find((table) => table.name === target),
        data: dataByTarget[target],
      });
    });
    return groups;
  }, [selectedTargets, tables, dataByTarget]);

  const buildChatPrompt = useCallback((): string => {
    const targetSnippets = groupedResults
      .map(({ table, data }) => {
        if (!data || data.rows.length === 0) return null;
        const label = table?.display_name ?? data.table;
        const previewRows = data.rows.slice(0, 5).map((row) => {
          const rowSummary = data.columns.slice(0, 6).map((column) => `${column}: ${formatCellValue(row.data[column])}`).join(', ');
          return `- ${rowSummary}`;
        }).join('\n');
        return `${label} (showing ${Math.min(data.rows.length, 5)} of ${data.total}):\n${previewRows}`;
      })
      .filter(Boolean)
      .join('\n\n');

    return `Summarise this data:\n\n${targetSnippets || 'No records were returned for the selected targets.'}`;
  }, [groupedResults]);

  const handleStartChat = (): void => {
    const prompt = buildChatPrompt();
    setPendingChatInput(prompt);
    setPendingChatAutoSend(false);
    startNewChat();
    setCurrentView('chat');
  };

  return (
    <div className="flex-1 overflow-hidden flex flex-col">
      <header className="sticky top-0 bg-surface-950 border-b border-surface-800 px-4 md:px-8 py-4 md:py-6">
        <h1 className="text-xl md:text-2xl font-bold text-surface-50">Search Data</h1>
        <p className="text-surface-400 mt-1 text-sm md:text-base">Browse and compare synced data from connected sources</p>
      </header>

      <div className="flex-1 overflow-hidden flex flex-col px-4 md:px-8 py-4 md:py-6">
        <div className="flex flex-wrap gap-2 mb-4">
          {tables.map((table) => {
            const isSelected = selectedTargets.includes(table.name);
            return (
              <button
                key={table.name}
                onClick={() => toggleTarget(table.name)}
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

          {selectedTargets.includes('activities') && filterOptions?.activity_types && filterOptions.activity_types.length > 0 && (
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

          <input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder={`Type to filter ${selectedTargets.join(', ')}...`}
            className="flex-1 min-w-[200px] px-4 py-2 bg-surface-800 border border-surface-700 rounded-lg text-surface-100 placeholder-surface-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
          />

          <button
            type="button"
            onClick={handleStartChat}
            className="px-4 py-2 bg-primary-600 hover:bg-primary-500 text-white rounded-lg transition-colors"
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

        <div className="flex-1 overflow-auto bg-surface-900 rounded-lg border border-surface-800 p-3 space-y-4">
          {loading ? (
            <div className="flex items-center justify-center h-64"><div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" /></div>
          ) : error ? (
            <div className="flex items-center justify-center h-64 text-red-400">{error}</div>
          ) : (
            groupedResults.map(({ target, table, data }) => (
              <section key={target} className="border border-surface-800 rounded-lg overflow-hidden">
                <div className="px-4 py-3 bg-surface-800 flex items-center justify-between">
                  <h2 className="text-sm font-semibold text-surface-200">{table?.display_name ?? target}</h2>
                  <span className="text-xs text-surface-400">{data?.total?.toLocaleString() ?? 0} records</span>
                </div>
                {!data || data.rows.length === 0 ? (
                  <div className="px-4 py-6 text-sm text-surface-400">No records found for this target.</div>
                ) : (
                  <table className="w-full text-sm">
                    <thead className="bg-surface-900/40">
                      <tr>
                        {data.columns.map((col) => (
                          <th
                            key={col}
                            onClick={() => !isMultiTarget && handleSort(col)}
                            className={`px-4 py-3 text-left text-surface-300 font-medium whitespace-nowrap ${
                              isMultiTarget ? '' : 'cursor-pointer hover:bg-surface-700 transition-colors'
                            }`}
                          >
                            {formatColumnHeader(col)}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-surface-800">
                      {data.rows.map((row) => (
                        <tr key={row.id} className="hover:bg-surface-800/50">
                          {data.columns.map((col) => (
                            <td key={col} className="px-4 py-3 text-surface-200 whitespace-nowrap">{formatCellValue(row.data[col])}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
