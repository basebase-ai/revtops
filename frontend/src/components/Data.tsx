/**
 * Data inspector component.
 * 
 * SECURITY: Uses JWT authentication via apiRequest. Organization is determined
 * from the authenticated user, not from query parameters.
 * 
 * Allows users to browse their synced data (contacts, accounts, deals, activities)
 * in a paginated table view. Helps build trust and debug sync issues.
 */

import { useState, useEffect } from 'react';
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
  const { organization } = useAppStore();
  const [tables, setTables] = useState<TableSummary[]>([]);
  const [selectedTable, setSelectedTable] = useState<string>('contacts');
  const [data, setData] = useState<DataResponse | null>(null);
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

  // Fetch table summaries
  useEffect(() => {
    if (!organizationId) return;

    const fetchSummary = async (): Promise<void> => {
      try {
        const { data: result, error: apiError } = await apiRequest<DataSummaryResponse>(
          '/data/summary'
        );
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

  // Fetch filter options when table changes
  useEffect(() => {
    if (!organizationId || !selectedTable) return;

    const fetchFilters = async (): Promise<void> => {
      try {
        const { data: result, error: apiError } = await apiRequest<FilterOptions>(
          `/data/${selectedTable}/filters`
        );
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
  }, [organizationId, selectedTable]);

  // Fetch table data
  useEffect(() => {
    if (!organizationId || !selectedTable) return;

    const fetchData = async (): Promise<void> => {
      setLoading(true);
      setError(null);
      try {
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
        if (typeFilter && selectedTable === 'activities') {
          params.set('type_filter', typeFilter);
        }
        
        const { data: result, error: apiError } = await apiRequest<DataResponse>(
          `/data/${selectedTable}?${params.toString()}`
        );
        if (apiError || !result) {
          throw new Error(apiError || 'Failed to fetch data');
        }
        setData(result);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    void fetchData();
  }, [organizationId, selectedTable, page, search, sortBy, sortOrder, sourceSystemFilter, typeFilter]);

  // Reset page, sort, and filters when table changes
  useEffect(() => {
    setPage(1);
    setSortBy(null);
    setSortOrder('asc');
    setSourceSystemFilter('');
    setTypeFilter('');
  }, [selectedTable]);

  // Reset page when search or filters change
  useEffect(() => {
    setPage(1);
  }, [search, sourceSystemFilter, typeFilter]);

  // Handle column header click for sorting
  const handleSort = (column: string): void => {
    if (sortBy === column) {
      // Toggle order if same column
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      // New column, start with ascending
      setSortBy(column);
      setSortOrder('asc');
    }
    setPage(1);
  };

  const handleSearch = (e: React.FormEvent): void => {
    e.preventDefault();
    setSearch(searchInput);
  };

  const totalPages = data ? Math.ceil(data.total / data.page_size) : 0;

  // Format column header for display
  const formatColumnHeader = (col: string): string => {
    return col
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (l) => l.toUpperCase());
  };

  // Format cell value for display
  const formatCellValue = (value: string | number | boolean | null | undefined): string => {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (typeof value === 'number') {
      // Format currency-like numbers
      if (value >= 1000) return value.toLocaleString();
      return String(value);
    }
    // Truncate long strings
    const str = String(value);
    if (str.length > 50) return str.substring(0, 47) + '...';
    return str;
  };

  return (
    <div className="flex-1 overflow-hidden flex flex-col">
      {/* Header */}
      <header className="sticky top-0 bg-surface-950 border-b border-surface-800 px-4 md:px-8 py-4 md:py-6">
        <h1 className="text-xl md:text-2xl font-bold text-surface-50">Data</h1>
        <p className="text-surface-400 mt-1 text-sm md:text-base">
          Browse your synced data from connected sources
        </p>
      </header>

      <div className="flex-1 overflow-hidden flex flex-col px-4 md:px-8 py-4 md:py-6">
        {/* Table selector tabs */}
        <div className="flex flex-wrap gap-2 mb-4">
          {tables.map((table) => (
            <button
              key={table.name}
              onClick={() => setSelectedTable(table.name)}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                selectedTable === table.name
                  ? 'bg-primary-500 text-white'
                  : 'bg-surface-800 text-surface-300 hover:bg-surface-700'
              }`}
            >
              {table.display_name}
              <span className="ml-2 px-2 py-0.5 rounded-full text-xs bg-surface-900/50">
                {table.count.toLocaleString()}
              </span>
            </button>
          ))}
        </div>

        {/* Filters and Search */}
        <div className="flex flex-wrap gap-3 mb-4">
          {/* Source System Filter */}
          {filterOptions && filterOptions.source_systems.length > 0 && (
            <select
              value={sourceSystemFilter}
              onChange={(e) => setSourceSystemFilter(e.target.value)}
              className="px-3 py-2 bg-surface-800 border border-surface-700 rounded-lg text-surface-100 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            >
              <option value="">All Sources</option>
              {filterOptions.source_systems.map((source) => (
                <option key={source} value={source}>
                  {source.charAt(0).toUpperCase() + source.slice(1)}
                </option>
              ))}
            </select>
          )}

          {/* Activity Type Filter (only for activities table) */}
          {selectedTable === 'activities' && filterOptions?.activity_types && filterOptions.activity_types.length > 0 && (
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="px-3 py-2 bg-surface-800 border border-surface-700 rounded-lg text-surface-100 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            >
              <option value="">All Types</option>
              {filterOptions.activity_types.map((type) => (
                <option key={type} value={type}>
                  {type.charAt(0).toUpperCase() + type.slice(1)}
                </option>
              ))}
            </select>
          )}

          {/* Search bar */}
          <form onSubmit={handleSearch} className="flex-1 flex gap-2">
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder={`Search ${selectedTable}...`}
              className="flex-1 min-w-[200px] px-4 py-2 bg-surface-800 border border-surface-700 rounded-lg text-surface-100 placeholder-surface-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            />
            <button
              type="submit"
              className="px-4 py-2 bg-surface-700 hover:bg-surface-600 text-surface-200 rounded-lg transition-colors"
            >
              Search
            </button>
          </form>

          {/* Clear all filters */}
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

        {/* Data table */}
        <div className="flex-1 overflow-auto bg-surface-900 rounded-lg border border-surface-800">
          {loading ? (
            <div className="flex items-center justify-center h-64">
              <div className="w-8 h-8 border-2 border-primary-500 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : error ? (
            <div className="flex items-center justify-center h-64 text-red-400">
              {error}
            </div>
          ) : data && data.rows.length > 0 ? (
            <table className="w-full text-sm">
              <thead className="bg-surface-800 sticky top-0">
                <tr>
                  {data.columns.map((col) => (
                    <th
                      key={col}
                      onClick={() => handleSort(col)}
                      className="px-4 py-3 text-left text-surface-300 font-medium whitespace-nowrap cursor-pointer hover:bg-surface-700 transition-colors select-none"
                    >
                      <div className="flex items-center gap-1">
                        {formatColumnHeader(col)}
                        {/* Sort indicator */}
                        <span className="text-surface-500">
                          {sortBy === col ? (
                            sortOrder === 'asc' ? (
                              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
                              </svg>
                            ) : (
                              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                              </svg>
                            )
                          ) : (
                            <svg className="w-4 h-4 opacity-0 group-hover:opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16V4m0 0L3 8m4-4l4 4m6 0v12m0 0l4-4m-4 4l-4-4" />
                            </svg>
                          )}
                        </span>
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-800">
                {data.rows.map((row) => (
                  <tr key={row.id} className="hover:bg-surface-800/50">
                    {data.columns.map((col) => (
                      <td
                        key={col}
                        className="px-4 py-3 text-surface-200 whitespace-nowrap"
                      >
                        {formatCellValue(row.data[col])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="flex flex-col items-center justify-center h-64 text-surface-400">
              <svg
                className="w-12 h-12 mb-4 text-surface-600"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4"
                />
              </svg>
              <p>No {selectedTable} found</p>
              <p className="text-sm mt-1">
                {search
                  ? 'Try a different search term'
                  : 'Sync your data sources to see data here'}
              </p>
            </div>
          )}
        </div>

        {/* Pagination */}
        {data && data.total > data.page_size && (
          <div className="flex items-center justify-between mt-4 text-sm">
            <span className="text-surface-400">
              Showing {((page - 1) * data.page_size) + 1} - {Math.min(page * data.page_size, data.total)} of {data.total.toLocaleString()}
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="px-3 py-1.5 bg-surface-800 hover:bg-surface-700 disabled:opacity-50 disabled:cursor-not-allowed text-surface-200 rounded transition-colors"
              >
                Previous
              </button>
              <span className="px-3 py-1.5 text-surface-400">
                Page {page} of {totalPages}
              </span>
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
